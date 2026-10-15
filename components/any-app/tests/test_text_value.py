"""Text values: displays, entries and terminals carry their text; password fields never do."""
from any_app import describe_accessible as da


class FakeText:
    def __init__(self, value): self.value, self.characterCount = value, len(value)
    def getText(self, start, end): return self.value[start:end]


class Node:
    def __init__(self, value): self.value = value
    def queryText(self): return FakeText(self.value)


def test_text_roles_carry_their_value():
    assert da.text_value(Node("36"), "text") == "36"
    assert da.text_value(Node(""), "entry") is None
    assert da.text_value(Node("secret"), "password text") is None
    assert da.text_value(Node("Save"), "push button") is None


def test_terminal_value_is_the_end_of_its_scrollback():
    value = da.text_value(Node("old\n" * 1000 + "$ ls"), "terminal")
    assert value.endswith("$ ls") and len(value) == da.MAX_TEXT_VALUE
    assert len(da.text_value(Node("x" * 5000), "text")) == da.MAX_TEXT_VALUE
