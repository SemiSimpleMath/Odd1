"""Drive LibreOffice Calc through its own object model (UNO), on the visible desktop.

    from odd1_office.calc import Calc
    calc = Calc.connect()                 # attach to the running Calc, or start one on DISPLAY :0
    doc = calc.open("/home/agent/people.csv")
    rows = calc.read(doc)                 # the used area of the first sheet, as a list of rows
    calc.write(doc, "Summary", [["name", "avg height"], ["Ana", 171.5]])
    calc.save(doc, "/home/agent/people.ods")

Values come back as Python strings and floats exactly as Calc holds them; nothing is read off the screen.
"""
import os
import signal
import socket
import subprocess
import time
import uno
from com.sun.star.beans import PropertyValue

PORT = int(os.environ.get("ODD1_CALC_PORT", "2002"))
ACCEPT = f"socket,host=127.0.0.1,port={PORT};urp;StarOffice.ComponentContext"
PROFILE = os.environ.get("ODD1_CALC_PROFILE")  # a separate LibreOffice profile directory (tests use their own)


def _props(**kw):
    out = []
    for k, v in kw.items():
        p = PropertyValue()
        p.Name, p.Value = k, v
        out.append(p)
    return tuple(out)


def _profile_dir() -> str:
    if PROFILE:
        return os.path.abspath(PROFILE)
    config = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(config, "libreoffice", "4")


def clear_foreign_lock(profile: str | None = None) -> bool:
    """Remove the profile's .lock if another host name wrote it (the machine was renamed, or the profile copied):
    LibreOffice would wait on its 'personal settings are locked' dialog, with nobody to answer it."""
    lock = os.path.join(profile or _profile_dir(), ".lock")
    try:
        with open(lock) as f:
            host = next((line.split("=", 1)[1].strip() for line in f if line.startswith("Host=")), None)
    except OSError:
        return False
    if host is None or host == socket.gethostname():
        return False  # this host's lock: LibreOffice itself treats it as stale or finds the running instance
    os.remove(lock)
    return True


def _column_name(n: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA."""
    name = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        name = chr(65 + r) + name
    return name


class Calc:
    process: subprocess.Popen | None = None  # the soffice this process started, if any

    def __init__(self, desktop):
        self.desktop = desktop

    @classmethod
    def connect(cls, timeout: float = 30.0, start: bool = True) -> "Calc":
        local = uno.getComponentContext()
        resolver = local.ServiceManager.createInstanceWithContext("com.sun.star.bridge.UnoUrlResolver", local)
        deadline = time.monotonic() + timeout
        started = False
        while True:
            try:
                ctx = resolver.resolve(f"uno:{ACCEPT}")
                desktop = ctx.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)
                return cls(desktop)
            except Exception:
                if start and not started:
                    clear_foreign_lock()
                    env = dict(os.environ, DISPLAY=os.environ.get("DISPLAY", ":0"))
                    args = ["soffice", "--calc", "--norestore", "--nologo", f"--accept={ACCEPT}"]
                    if PROFILE:
                        args.append("-env:UserInstallation=" + uno.systemPathToFileUrl(os.path.abspath(PROFILE)))
                    cls.process = subprocess.Popen(args, env=env, stdin=subprocess.DEVNULL,
                                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                                   start_new_session=True)
                    started = True
                if time.monotonic() > deadline:
                    if started:  # don't leave a launcher behind to queue up with the next connect's
                        os.killpg(cls.process.pid, signal.SIGKILL)  # soffice.bin shares the launcher's session
                        cls.process.wait()
                    raise RuntimeError(f"Calc did not answer on port {PORT} within {timeout:.0f}s")
                time.sleep(0.5)

    def open(self, path: str, hidden: bool = False):
        url = uno.systemPathToFileUrl(os.path.abspath(path))
        return self.desktop.loadComponentFromURL(url, "_blank", 0, _props(Hidden=hidden))

    def new(self, hidden: bool = False):
        return self.desktop.loadComponentFromURL("private:factory/scalc", "_blank", 0, _props(Hidden=hidden))

    @staticmethod
    def sheet(doc, name_or_index=0):
        sheets = doc.Sheets
        if isinstance(name_or_index, int):
            return sheets.getByIndex(name_or_index)
        if not sheets.hasByName(name_or_index):
            sheets.insertNewByName(name_or_index, sheets.getCount())
        return sheets.getByName(name_or_index)

    def read(self, doc, sheet=0, cell_range: str | None = None) -> list[list]:
        """A range (e.g. "A1:C20"), or the sheet's used area, as rows of str/float ('' for empty)."""
        sh = self.sheet(doc, sheet)
        if cell_range is None:
            cursor = sh.createCursor()
            cursor.gotoEndOfUsedArea(False)
            end = cursor.getRangeAddress()
            cell_range = f"A1:{_column_name(end.EndColumn)}{end.EndRow + 1}"
        return [list(r) for r in sh.getCellRangeByName(cell_range).getDataArray()]

    def write(self, doc, sheet, rows: list[list], top_left: str = "A1") -> str:
        """Write rows (str/float/int) starting at top_left; returns the range written."""
        sh = self.sheet(doc, sheet)
        width = max(len(r) for r in rows)
        rows = [list(r) + [""] * (width - len(r)) for r in rows]
        start = sh.getCellRangeByName(top_left).getCellAddress()
        rng = (f"{_column_name(start.Column)}{start.Row + 1}:"
               f"{_column_name(start.Column + width - 1)}{start.Row + len(rows)}")
        sh.getCellRangeByName(rng).setDataArray(tuple(tuple(float(v) if isinstance(v, int) else v for v in r)
                                                      for r in rows))
        return rng

    @staticmethod
    def formula(doc, sheet, cell: str, formula: str):
        """Set a formula (e.g. "=AVERAGE(B2:B9)") and return the value Calc computes."""
        c = Calc.sheet(doc, sheet).getCellRangeByName(cell)
        c.setFormula(formula)
        text = c.getString()
        number = text.replace(".", "", 1).replace("-", "", 1)
        return c.getValue() if number.isdigit() else text  # a number, or the text/error Calc shows

    @staticmethod
    def save(doc, path: str, filter_name: str | None = None) -> str:
        url = uno.systemPathToFileUrl(os.path.abspath(path))
        if filter_name is None:
            filter_name = {"csv": "Text - txt - csv (StarCalc)", "xlsx": "Calc MS Excel 2007 XML",
                           "ods": "calc8"}.get(path.rsplit(".", 1)[-1].lower(), "calc8")
        doc.storeToURL(url, _props(FilterName=filter_name))
        return path

    @staticmethod
    def close(doc) -> None:
        doc.close(True)
