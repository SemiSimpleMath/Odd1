"""Headless controller-flow tests for the real GUI methods and shared engine.

Tk rendering is intentionally stubbed; this exercises plan, selection, explicit
confirmation, apply, history refresh, undo, and failure invalidation together.
"""
import sys
import pytest

if not sys.platform.startswith("linux"):
    pytest.skip("Intent's engine is Linux-only (fcntl.flock, renameat2, /proc/self/fd); run on Odd1 or another Linux host",
                allow_module_level=True)

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parent.parent  # the component directory


FLOW_SCRIPT = r'''
import sys, types
# Import gui.py without a display or Tk shared library. App methods under test do
# not invoke the widget constructors; their small state protocol is supplied below.
tk = types.ModuleType("tkinter")
tk.Tk = object
tk.StringVar = lambda *a, **k: None
tk.BooleanVar = lambda *a, **k: None
ttk = types.ModuleType("tkinter.ttk")
filedialog = types.ModuleType("tkinter.filedialog")
messagebox = types.ModuleType("tkinter.messagebox")
class FakeWidget:
    def __init__(self, parent=None, **kwargs):
        self.parent, self.children, self.kwargs, self.state = parent, [], kwargs, None
        if parent is not None and hasattr(parent, "items"):
            parent.items.append(self)
        elif parent is not None and hasattr(parent, "children"):
            parent.children.append(self)
    def grid(self, **kwargs): pass
    def pack(self, **kwargs): pass
    def columnconfigure(self, *args, **kwargs): pass
    def destroy(self):
        if self.parent is not None:
            if hasattr(self.parent, "items") and self in self.parent.items: self.parent.items.remove(self)
            if hasattr(self.parent, "children") and self in self.parent.children: self.parent.children.remove(self)
    def configure(self, **kwargs): self.state=kwargs.get("state",self.state)
    def winfo_children(self): return list(self.children)
    def bind(self, *args, **kwargs): pass
for name in ("Frame", "Label", "Checkbutton", "Separator"):
    setattr(ttk, name, FakeWidget)
class FakeBooleanVar:
    def __init__(self, value=False): self.value=value
    def get(self): return self.value
    def set(self, value): self.value=value
tk.BooleanVar = FakeBooleanVar
tk.ttk, tk.filedialog, tk.messagebox = ttk, filedialog, messagebox
sys.modules.update({"tkinter": tk, "tkinter.ttk": ttk,
                    "tkinter.filedialog": filedialog,
                    "tkinter.messagebox": messagebox})
from odd1_intent import gui
from pathlib import Path

class Var:
    def __init__(self, value): self.value = value
    def get(self): return self.value
    def set(self, value): self.value = value
class Row:
    def __init__(self, label): self.label, self.destroyed = label, False
    def destroy(self): self.destroyed = True
class Rows:
    def __init__(self): self.items = []
    def winfo_children(self): return list(self.items)
class Button:
    def __init__(self): self.state = "disabled"
    def configure(self, **kw): self.state = kw.get("state", self.state)
class Listbox:
    def __init__(self): self.items = []
    def delete(self, *args): self.items.clear()
    def insert(self, _where, item): self.items.append(item)

base = Path(sys.argv[1]); src=base/"Downloads"; dst=base/"project"
src.mkdir(); dst.mkdir()
import os
(src/"new.txt").write_text("fresh")
(dst/"new.txt").write_text("old")
(src/"old.txt").write_text("stale")
(dst/"old.txt").write_text("keep-best")
(src/"add.txt").write_text("addition")
(src/"unrelated.txt").write_text("different download")
(dst/"unrelated.txt").write_text("unrelated")
# Give the downloaded same-name file a newer mtime so it remains an unrelated SKIP.
os.utime(src/"unrelated.txt", ns=(2_000_000_000,2_000_000_000))
os.utime(dst/"unrelated.txt", ns=(3_000_000_000,3_000_000_000))
for p, ns in ((src/"new.txt",3_000_000_000),(dst/"new.txt",2_000_000_000),
              (src/"old.txt",2_000_000_000),(dst/"old.txt",3_000_000_000)):
    import os; os.utime(p, ns=(ns,ns))
app = gui.App.__new__(gui.App)
app.plan=None; app.vars=[]; app.rows=Rows(); app.approve=Button()
app.canvas=type("Canvas",(),{"bbox":lambda *a:None,"configure":lambda *a,**k:None})()
app.intent=Var(f'Update files in "{dst}" with the ones from "{src}"')
app.dst=Var(str(dst)); app.src=Var(str(src)); app.state=Var(""); app.hist=Listbox()
confirmations=[]; errors=[]; notices=[]
def ask(title, body): confirmations.append((title,body)); return True
messagebox.askyesno=ask
messagebox.showerror=lambda *a: errors.append(a)
messagebox.showinfo=lambda *a: notices.append(a)
app.plan_request()
assert [(Path(a.target).name,a.kind,a.selected) for a in app.plan.actions] == [
    ("add.txt","ADD",False),("new.txt","REPLACE",True),
    ("old.txt","SKIP",False),("unrelated.txt","SKIP",False)]
assert app.approve.state == "normal"
assert "Nothing has changed" in app.state.get()
# Explicitly exercise approval-state wiring: deselect the default replacement,
# then select ADD; approval must disable then enable on the newly checked ADD.
app.vars[1].set(False); app.refresh_approval_state()
assert app.approve.state == "disabled"
app.vars[0].set(True); app.refresh_approval_state()
assert app.approve.state == "normal"
# Also opt into the replacement and verify the exact paths in confirmation.
app.vars[1].set(True); app.refresh_approval_state()
app.approve_plan()
assert len(confirmations)==1
expected = ("Proceed with only these selected file changes?\n\n"
            f"ADD: {src/'add.txt'} → {dst/'add.txt'}\n"
            f"REPLACE: {src/'new.txt'} → {dst/'new.txt'}")
assert confirmations[0][1] == expected, confirmations[0][1]
assert (dst/"new.txt").read_text()=="fresh"
assert (dst/"add.txt").read_text()=="addition"
assert (dst/"old.txt").read_text()=="keep-best"
assert (dst/"unrelated.txt").read_text()=="unrelated"
assert app.plan is None and app.approve.state=="disabled"
assert app.vars == [] and app.rows.winfo_children() == []
assert app.hist.items and "APPLIED" in app.hist.items[0]
app.undo_latest()
assert (dst/"new.txt").read_text()=="old"
assert not (dst/"add.txt").exists()
assert (dst/"old.txt").read_text()=="keep-best"
assert (dst/"unrelated.txt").read_text()=="unrelated"
assert "UNDONE" in app.hist.items[0]
assert errors == []
print("GUI controller flow passed: preview -> explicit selections -> exact confirm -> apply/hash verification -> history -> undo/hash restoration")
'''


FAILURE_SCRIPT = r'''
import sys, types
for name in ("tkinter.ttk", "tkinter.filedialog", "tkinter.messagebox"):
    sys.modules[name]=types.ModuleType(name)
tk=types.ModuleType("tkinter"); tk.Tk=object; tk.ttk=sys.modules["tkinter.ttk"]
tk.filedialog=sys.modules["tkinter.filedialog"]; tk.messagebox=sys.modules["tkinter.messagebox"]
sys.modules["tkinter"]=tk
from odd1_intent import gui
class Var:
 def __init__(self,v): self.v=v
 def get(self): return self.v
 def set(self,v): self.v=v
class Row:
 def __init__(self, parent): self.parent=parent
 def destroy(self):
  if self in self.parent.items: self.parent.items.remove(self)
class Rows:
 def __init__(self): self.items=[]; self.items.append(Row(self))
 def winfo_children(self): return self.items
class Button:
 def __init__(self): self.state="normal"
 def configure(self,**kw): self.state=kw.get("state",self.state)
app=gui.App.__new__(gui.App); from odd1_intent.engine import Action, Plan
app.plan=Plan("failed", "backup", [Action("ADD", "source", "target", "reason", True)])
app.vars=[Var(True)]; app.rows=Rows()
app.approve=Button(); app.intent=Var("supported"); app.dst=Var("unused"); app.src=Var("unused"); app.state=Var("")
confirmed=[]; errors=[]
tk.messagebox.askyesno=lambda *a: confirmed.append(a) or True
tk.messagebox.showerror=lambda *a: errors.append(a)
gui.apply=lambda *a: (_ for _ in ()).throw(gui.IntentError("injected incomplete rollback"))
app.approve_plan()
assert confirmed and errors and "injected incomplete rollback" in errors[0][1]
assert app.plan is None and app.vars==[] and app.rows.items==[] and app.approve.state=="disabled"
assert "inspect details" in app.state.get()
print("GUI failed-apply controller flow passed: stale preview retired and approval disabled")
'''


class GUIFlowTests(unittest.TestCase):
    def run_script(self, script, home):
        env = dict(os.environ, HOME=str(home), PYTHONPATH=str(ROOT))
        env.pop("ODD1_INTENT_STATE", None)
        return subprocess.run([sys.executable, "-c", textwrap.dedent(script), str(home)],
                              cwd=ROOT, env=env, text=True, capture_output=True, timeout=20)

    def test_preview_approve_apply_history_undo_flow(self):
        with tempfile.TemporaryDirectory(prefix="orbit-gui-flow-") as td:
            result=self.run_script(FLOW_SCRIPT, Path(td))
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertIn("preview -> explicit selections",result.stdout)

    def test_apply_error_retires_preview_in_gui_controller(self):
        with tempfile.TemporaryDirectory(prefix="orbit-gui-failure-") as td:
            result=self.run_script(FAILURE_SCRIPT, Path(td))
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertIn("stale preview retired",result.stdout)


if __name__ == "__main__":
    unittest.main()
