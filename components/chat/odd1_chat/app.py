"""The chat window: a conversation with Claude Code as a chat, not a terminal. The transcript is a web page (bubbles,
Markdown, code folded away, tools as one-line notes, permission requests as Allow/Deny cards); the message box is a
native text field (Enter sends, Shift+Enter breaks the line, Ctrl+V pastes a picture, Esc interrupts Claude).

The window carries the role odd1-agent:chat (or $ODD1_AGENT_ROLE), so the desktop docks it in the chat column as a
tab; when Claude needs the person and the window is not in front, it marks itself urgent (the tab's orange ball).
"""
import base64
import json
import os

import gi

# No GPU here (a VM, Xvnc): WebKit renders on the CPU instead of failing to open an EGL display.
for _key, _value in (("WEBKIT_DISABLE_DMABUF_RENDERER", "1"), ("WEBKIT_DISABLE_COMPOSITING_MODE", "1"),
                     ("WEBKIT_SKIA_ENABLE_CPU_RENDERING", "1"), ("LIBGL_ALWAYS_SOFTWARE", "1")):
    os.environ.setdefault(_key, _value)
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import Gdk, GLib, Gtk, WebKit2  # noqa: E402

from . import render  # noqa: E402
from .session import Session  # noqa: E402

PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "page.html")
CSS = b"""
window, .odd1-chat { background: #141827; }
.odd1-input { background: #1e2438; border-radius: 12px; padding: 10px 12px; border: 1px solid #262c44; }
.odd1-input text, .odd1-input textview { background: #1e2438; color: #e6e8f0; caret-color: #e8805f;
                                         font-family: Inter; font-size: 14px; }
.odd1-status { color: #8b92aa; font-family: Inter; font-size: 12px; padding: 0 4px; }
.odd1-attach { color: #e8805f; font-family: Inter; font-size: 12px; padding: 0 4px; }
"""


class ChatWindow(Gtk.Window):
    def __init__(self, cwd=None, resume=None):
        super().__init__(title="Chat")
        self.set_role(os.environ.get("ODD1_AGENT_ROLE") or "odd1-agent:chat")
        self.set_default_size(680, 900)
        self.get_style_context().add_class("odd1-chat")
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.pending_js, self.loaded = [], False
        self.permissions = {}  # request id -> the tool input to allow
        self.images = []  # (media type, base64) pasted for the next message
        self.busy = False

        content = WebKit2.UserContentManager()
        content.register_script_message_handler("odd1")
        content.connect("script-message-received::odd1", self.on_page_message)
        self.view = WebKit2.WebView.new_with_user_content_manager(content)
        self.view.set_background_color(Gdk.RGBA(0.078, 0.094, 0.153, 1))
        self.view.connect("load-changed", self.on_load)
        with open(PAGE) as page:
            self.view.load_html(page.read(), "file:///")

        self.input = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False)
        self.input.connect("key-press-event", self.on_key)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.set_min_content_height(48)
        scroller.set_max_content_height(200)
        scroller.set_propagate_natural_height(True)
        scroller.add(self.input)
        scroller.get_style_context().add_class("odd1-input")
        self.status = Gtk.Label(label="Claude", xalign=0)
        self.status.get_style_context().add_class("odd1-status")
        self.attach = Gtk.Label(label="", xalign=0)
        self.attach.get_style_context().add_class("odd1-attach")

        bottom = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=14, margin_top=6)
        bottom.pack_start(self.attach, False, False, 0)
        bottom.pack_start(scroller, False, False, 0)
        bottom.pack_start(self.status, False, False, 0)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.pack_start(self.view, True, True, 0)
        box.pack_start(bottom, False, False, 0)
        self.add(box)
        self.connect("notify::is-active", lambda *_: self.is_active() and self.set_urgency_hint(False))
        self.connect("destroy", self.on_destroy)

        self.session = Session(lambda event: GLib.idle_add(self.on_event, event), cwd=cwd, resume=resume)
        self.input.grab_focus()

    # The page ---------------------------------------------------------------------------------------------------
    def js(self, function, *args):
        script = f"{function}({', '.join(json.dumps(a) for a in args)})"
        if not self.loaded:
            self.pending_js.append(script)
        elif hasattr(self.view, "evaluate_javascript"):
            self.view.evaluate_javascript(script, -1, None, None, None, None, None)
        else:
            self.view.run_javascript(script, None, None, None)

    def on_load(self, view, event):
        if event == WebKit2.LoadEvent.FINISHED:
            self.loaded = True
            for script in self.pending_js:
                self.view.evaluate_javascript(script, -1, None, None, None, None, None)
            self.pending_js = []

    def on_page_message(self, manager, result):
        value = result.to_string() if hasattr(result, "to_string") else result.get_js_value().to_string()
        message = json.loads(value)
        if "answer" in message:
            request = message["answer"]
            allow = bool(message.get("allow"))
            self.session.answer(request, allow, self.permissions.pop(request, {}))
            self.js("answered", request, allow)
            self.set_status("Working…" if allow else "Claude was told no.")
        elif "open" in message:
            Gtk.show_uri_on_window(self, message["open"], Gdk.CURRENT_TIME)

    # Claude ------------------------------------------------------------------------------------------------------
    def on_event(self, event):
        kind = event["kind"]
        if kind == "ready":
            model = (event.get("model") or "").replace("claude-", "").replace("-", " ").title()
            self.set_status(f"Claude · {model}" if model else "Claude")
        elif kind == "delta":
            self.js("delta", event["text"])
        elif kind == "text":
            self.js("reply", render.reply(event["text"]))
        elif kind == "tool":
            self.js("tool", event["summary"])
        elif kind == "permission":
            self.permissions[event["id"]] = event["input"]
            self.js("permission", event["id"], event["prompt"], event["detail"])
            self.set_status("Waiting for you")
            self.ring()
        elif kind == "done":
            self.busy = False
            if event.get("error"):
                self.js("note", event.get("text") or "Something went wrong.", True)
            self.set_status("Ready")
            self.ring()
        elif kind == "ended":
            self.busy = False
            detail = (getattr(self.session, "stderr", None) or [""])[-1]
            self.js("note", f"Claude stopped. {detail}".strip(), True)
            self.set_status("Stopped")
        return False

    def ring(self):
        if not self.is_active():
            self.set_urgency_hint(True)

    def set_status(self, text):
        self.status.set_text(text)

    # The person --------------------------------------------------------------------------------------------------
    def on_key(self, widget, event):
        ctrl = event.state & Gdk.ModifierType.CONTROL_MASK
        if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and not event.state & Gdk.ModifierType.SHIFT_MASK:
            self.send()
            return True
        if event.keyval == Gdk.KEY_Escape and self.busy:
            self.session.interrupt()
            self.set_status("Stopping…")
            return True
        if ctrl and event.keyval in (Gdk.KEY_v, Gdk.KEY_V) and self.paste_image():
            return True
        return False

    def paste_image(self):
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        if not clipboard.wait_is_image_available():
            return False
        pixbuf = clipboard.wait_for_image()
        if pixbuf is None:
            return False
        ok, data = pixbuf.save_to_bufferv("png", [], [])
        if not ok:
            return False
        self.images.append(("image/png", base64.b64encode(data).decode()))
        count = len(self.images)
        self.attach.set_text(f"{count} picture{'s' if count > 1 else ''} attached")
        return True

    def send(self):
        buffer = self.input.get_buffer()
        text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False).strip()
        if not text and not self.images:
            return
        pictures = "".join(f'<img class="pasted" src="data:{kind};base64,{data}">' for kind, data in self.images)
        self.js("mine", pictures + render.plain(text))
        self.session.send(text or "What is in this picture?", self.images)
        buffer.set_text("")
        self.images = []
        self.attach.set_text("")
        self.busy = True
        self.set_status("Thinking…  (Esc stops)")

    def on_destroy(self, *_):
        self.session.close()
        Gtk.main_quit()


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(prog="odd1-chat", description="A chat window with Claude Code")
    parser.add_argument("--cwd", help="the conversation's working folder (default: home)")
    parser.add_argument("--resume", help="continue a conversation by its session id")
    args = parser.parse_args(argv)
    ChatWindow(cwd=args.cwd, resume=args.resume).show_all()
    Gtk.main()


if __name__ == "__main__":
    main()
