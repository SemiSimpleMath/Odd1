"""Acting through the app's accessibility interface: element lookup, action choice, honest text writes, and
minimized windows staying in the model with addresses."""
import pytest

from odd1_screen import atspi_act, live, model
from test_model import calculator


class Action:
    def __init__(self, names, ok=True): self.names, self.ok, self.done = names, ok, []
    @property
    def nActions(self): return len(self.names)
    def getName(self, i): return self.names[i]
    def doAction(self, i): self.done.append(self.names[i]); return self.ok


class Text:
    def __init__(self, node): self.node = node
    @property
    def characterCount(self): return len(self.node.text)
    def getText(self, start, end): return self.node.text[start:end]
    def setTextContents(self, value):
        if self.node.applies: self.node.text = value
        return True


class Node:
    def __init__(self, role, name="", children=(), actions=(), text="", applies=True, pid=None):
        self.role, self.name, self.kids, self.text, self.applies, self.pid = role, name, list(children), text, applies, pid
        self.action = Action(list(actions))
    @property
    def childCount(self): return len(self.kids)
    def getChildAtIndex(self, i): return self.kids[i]
    def getRoleName(self): return self.role
    def get_process_id(self): return self.pid
    def queryAction(self): return self.action
    def queryText(self): return Text(self)
    def queryEditableText(self): return Text(self)


def desktop(entry):
    button = Node("push button", "Seven", actions=["focus", "click"])
    frame = Node("frame", "Calculator", [button, entry])
    return Node("desktop frame", "", [Node("application", "other", pid=2), Node("application", "calc", [frame], pid=1)]), button


def test_resolve_by_path_and_refuse_a_changed_tree():
    root, button = desktop(Node("entry", "Name"))
    assert atspi_act.resolve(root, 1, 0, "0", "push button", "Seven") is button
    with pytest.raises(LookupError, match="moved or changed"):
        atspi_act.resolve(root, 1, 0, "0", "push button", "Eight")
    with pytest.raises(LookupError, match="pid 9"):
        atspi_act.resolve(root, 9, 0, "0", "push button", "Seven")


def test_press_prefers_click_and_text_writes_are_read_back():
    entry = Node("entry", "Name")
    _, button = desktop(entry)
    assert atspi_act.act(button, "action") == {"action": "click"} and button.action.done == ["click"]
    assert atspi_act.act(entry, "set-text", "Ada") == {"set_text": 3} and entry.text == "Ada"
    with pytest.raises(RuntimeError, match="did not change"):
        atspi_act.act(Node("entry", "Name", applies=False), "set-text", "Ada")
    with pytest.raises(PermissionError):
        atspi_act.act(Node("password text", "Password"), "set-text", "hunter2")


class LateAction(Action):
    """Firefox web content: 0 actions until the page's process has answered the first query."""
    def __init__(self, names, empty_reads): super().__init__(names); self.empty_reads = empty_reads
    @property
    def nActions(self):
        if self.empty_reads:
            self.empty_reads -= 1
            return 0
        return len(self.names)


def test_firefox_button_action_arrives_late(monkeypatch):
    monkeypatch.setattr(atspi_act.time, "sleep", lambda s: None)
    button = Node("push button", "Submit")
    button.action = LateAction(["press"], empty_reads=3)
    assert atspi_act.act(button, "action") == {"action": "press"} and button.action.done == ["press"]
    clock = iter(range(0, 100))
    monkeypatch.setattr(atspi_act.time, "monotonic", lambda: next(clock))
    with pytest.raises(ValueError, match="no actions"):
        atspi_act.act(Node("section", "Text"), "action")


def test_minimized_window_keeps_its_tree_and_auto_clicks_through_the_app(monkeypatch):
    x, atspi, _ = calculator()
    x["windows"][1]["states"] = ["hidden"]
    x["windows"][1]["geometry"]["viewable"] = False
    screen = model.Screen()
    calc = next(w for w in screen.fuse(x, atspi, {})["windows"] if w["title"] == "Calculator")
    assert calc["minimized"]
    seven = next(e["id"] for e in calc["elements"] if e["name"] == "Seven")
    assert screen.addresses[seven] == {"pid": 1, "frame": 0, "path": "0", "role": "push button", "name": "Seven"}
    with pytest.raises(PermissionError, match="minimized"):
        live.click(screen, seven, via="pointer")
    calls = []
    monkeypatch.setattr(live, "_app_act", lambda address, op, value=None: calls.append((address["path"], op)) or {"action": "click"})
    monkeypatch.setattr(live, "observe", lambda s, **_: s.fuse(x, atspi, {}))
    result = live.click(screen, seven, wait_ms=0)
    assert result["clicked"]["via"] == "app" and calls == [("0", "action")]
