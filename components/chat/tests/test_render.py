"""Replies render as Markdown, never as raw HTML, with long code folded."""
import pytest

from odd1_chat import render

pytest.importorskip("markdown", reason="python-markdown is installed on Odd1 by module.nix")


def test_markdown_renders_and_html_stays_text():
    page = render.reply("**Done.** <script>alert(1)</script>")
    assert "<strong>Done.</strong>" in page and "<script>" not in page and "&lt;script&gt;" in page


def test_code_keeps_its_characters():
    page = render.reply("```python\nif a < b and c > d: pass\n```")
    assert "a &lt; b" in page and "&amp;lt;" not in page


def test_long_code_is_folded_short_code_is_not():
    short = render.reply("```\nx = 1\n```")
    long = render.reply("```python\n" + "\n".join(f"line{i}" for i in range(20)) + "\n```")
    assert "<details" not in short
    assert '<details class="code"><summary>Python · 20 lines</summary>' in long


def test_plain_text_is_escaped_with_line_breaks():
    assert render.plain("a <b>\nc") == "a &lt;b&gt;<br>c"
