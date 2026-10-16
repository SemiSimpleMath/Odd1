"""Claude's replies as HTML for the chat page: Markdown rendered, raw HTML escaped, and code folded away (the
person reads the conversation; code longer than a few lines opens on a click)."""
import html
import re

FOLD_LINES = 6  # code blocks longer than this start folded

_FENCE = re.compile(r"<pre><code(?: class=\"language-([\w+-]+)\")?>(.*?)</code></pre>", re.S)


def markdown(text):
    """Markdown to HTML, with any HTML in the text shown as text (a reply can't inject markup into the page)."""
    import markdown as md
    converter = md.Markdown(extensions=["fenced_code", "tables", "sane_lists"], output_format="html")
    converter.preprocessors.deregister("html_block")  # raw HTML is not passed through: it stays text
    converter.inlinePatterns.deregister("html")
    return converter.convert(text)


def fold_code(page):
    """Wrap long code blocks in a closed <details> labelled with the language and length."""
    def fold(match):
        language, body = match.group(1), match.group(2)
        lines = body.rstrip("\n").count("\n") + 1
        block = match.group(0)
        if lines <= FOLD_LINES:
            return block
        label = f"{language.capitalize() if language else 'Code'} · {lines} lines"
        return f'<details class="code"><summary>{html.escape(label)}</summary>{block}</details>'
    return _FENCE.sub(fold, page)


def reply(text):
    return fold_code(markdown(text))


def plain(text):
    """Text as HTML without any Markdown (the person's own messages, and text still being written)."""
    return html.escape(text).replace("\n", "<br>")
