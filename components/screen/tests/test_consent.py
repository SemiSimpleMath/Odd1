"""Asking the person before an agent controls one of their windows: grants, refusals, and the window actions that
go through the same question."""
import pytest

from odd1_screen import consent, live, model
from test_model import calculator


class Bar:
    """The bar a yes leaves on the person's screen: open until the minutes run out or they take control back."""
    def __init__(self): self.returncode = None
    def poll(self): return self.returncode


def test_a_yes_holds_while_its_bar_is_open_and_only_for_that_window():
    grants, asked, bars = consent.Grants(), [], []

    def asker(display, what, title, reason, slot):
        asked.append((title, slot))
        bars.append(Bar())
        return 0, bars[-1]

    assert consent.require(":0", "0x1", "Calculator", "maximize", grants=grants, asker=asker) == "allowed"
    assert consent.require(":0", "0x1", "Calculator", "minimize", grants=grants, asker=asker) == "granted"
    assert consent.require(":0", "0x2", "Mousepad", "maximize", grants=grants, asker=asker) == "allowed"
    assert asked == [("Calculator", 0), ("Mousepad", 1)], "one question per window; bars stack in slots"
    bars[0].returncode = consent.EXPIRED  # the minutes ran out: the next step asks again
    assert consent.require(":0", "0x1", "Calculator", "maximize", grants=grants, asker=asker) == "allowed"


def test_taking_control_back_stops_the_agent_and_it_may_not_ask_again_soon():
    grants, bar, clock = consent.Grants(), Bar(), [1000.0]
    consent.require(":0", "0x1", "Calc", "type into", grants=grants, asker=lambda *a: (0, bar), now=lambda: clock[0])
    assert consent.still_held("0x1", grants)
    bar.returncode = consent.TAKEN_BACK  # the person clicked Take back
    assert not consent.still_held("0x1", grants)
    with pytest.raises(PermissionError, match="took back control"):
        consent.require(":0", "0x1", "Calc", "type into", grants=grants,
                        asker=lambda *a: pytest.fail("asked again"), now=lambda: clock[0])
    assert consent.still_held("0x9", grants), "a window that needed no dialog is not stopped"


def test_no_or_no_answer_refuses_and_grants_nothing():
    grants = consent.Grants()
    with pytest.raises(PermissionError, match="declined"):
        consent.require(":0", "0x1", "Calc", "maximize", grants=grants, asker=lambda *a: (consent.DENIED, None))
    with pytest.raises(PermissionError, match="did not answer"):
        consent.require(":0", "0x1", "Calc", "maximize", grants=grants, asker=lambda *a: (consent.NO_ANSWER, None))
    assert not grants.held("0x1")


def fake_desktop(monkeypatch, prefix="w"):
    x, atspi, _ = calculator()
    screen = model.Screen(prefix)
    monkeypatch.setattr(live, "observe", lambda s, **_: s.fuse(x, atspi, {}))
    monkeypatch.setattr(live, "_settle", lambda *a: {})
    ran = []
    monkeypatch.setattr(live.subprocess, "run", lambda command, **_: ran.append(command))
    return screen, ran


def test_window_action_asks_the_person_on_their_screen_and_never_activates(monkeypatch):
    screen, ran = fake_desktop(monkeypatch)
    asked = []
    monkeypatch.setattr(consent, "require", lambda display, xid, title, what, reason="": asked.append((title, what))
                        or "allowed")
    result = live.window_action(screen, "Calculator", "maximize", reason="to read its title in the top bar")
    assert asked == [("Calculator", "maximize")] and result["window"]["allowed"] == "allowed"
    assert ran and all("-a" not in c and "windowactivate" not in c for c in ran)
    assert ran[0][-1] == "add,maximized_vert,maximized_horz"


def test_window_action_refused_does_nothing(monkeypatch):
    screen, ran = fake_desktop(monkeypatch)
    def refuse(*a, **k):
        raise PermissionError("the person declined")
    monkeypatch.setattr(consent, "require", refuse)
    with pytest.raises(PermissionError, match="declined"):
        live.window_action(screen, "Calculator", "minimize")
    assert ran == []


def test_window_action_on_the_agent_desktop_or_approved_in_chat_needs_no_dialog(monkeypatch):
    monkeypatch.setattr(consent, "require", lambda *a, **k: pytest.fail("asked the person"))
    screen, ran = fake_desktop(monkeypatch, prefix="a")
    assert live.window_action(screen, "Calculator", "restore")["window"]["allowed"] == "agent desktop"
    screen, ran = fake_desktop(monkeypatch)
    result = live.window_action(screen, "Calculator", "move", box=[10, 40, -1, -1], person_approved=True)
    assert result["window"]["allowed"] == "approved in the chat" and ran[-1][-1] == "0,10,40,-1,-1"


def test_window_action_checks_its_arguments(monkeypatch):
    screen, _ = fake_desktop(monkeypatch)
    with pytest.raises(ValueError, match="action must be"):
        live.window_action(screen, "Calculator", "close")
    with pytest.raises(ValueError, match="box"):
        live.window_action(screen, "Calculator", "move")
    with pytest.raises(LookupError, match="0 windows match"):
        live.window_action(screen, "gimp", "maximize", person_approved=True)
