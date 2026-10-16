"""One conversation with Claude Code: the `claude` process, and a reader thread that turns its output into events
(protocol.events) for a callback. Writes are serialized; the callback runs on the reader thread (a GUI hands the
events to its own main loop)."""
import itertools
import os
import shutil
import subprocess
import threading

from . import protocol


def claude_path():
    return os.environ.get("ODD1_CHAT_CLAUDE") or shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")


class Session:
    def __init__(self, on_event, cwd=None, resume=None, claude=None):
        self.on_event = on_event
        self.ids = itertools.count(1)
        self.lock = threading.Lock()
        self.session_id = resume
        self.proc = subprocess.Popen(protocol.command(claude or claude_path(), resume=resume),
                                     cwd=cwd or os.path.expanduser("~"), env=protocol.environment(os.environ),
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, bufsize=1)
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._drain_stderr, daemon=True).start()
        self._write(protocol.initialize(f"init_{next(self.ids)}"))

    def _write(self, text):
        with self.lock:
            try:
                self.proc.stdin.write(text)
                self.proc.stdin.flush()
            except (BrokenPipeError, ValueError):
                self.on_event({"kind": "ended", "code": self.proc.poll()})

    def _read(self):
        for raw in self.proc.stdout:
            for event in protocol.events(raw):
                if event.get("session"):
                    self.session_id = event["session"]
                self.on_event(event)
        self.on_event({"kind": "ended", "code": self.proc.wait()})

    def _drain_stderr(self):
        self.stderr = []
        for raw in self.proc.stderr:
            self.stderr = (self.stderr + [raw.rstrip()])[-20:]  # the last lines, for an error report

    def send(self, text, images=()):
        self._write(protocol.user(text, images))

    def answer(self, request_id, allow, tool_input=None):
        self._write(protocol.answer(request_id, allow, tool_input))

    def interrupt(self):
        self._write(protocol.interrupt(f"stop_{next(self.ids)}"))

    def close(self):
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
