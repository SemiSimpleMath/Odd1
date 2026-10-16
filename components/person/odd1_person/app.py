"""About you: the person's settings window (GTK 4, libadwaita). One field per setting, Save writes them (and the
git identity); every Claude Code session started afterwards knows them. Accessible over AT-SPI like any GTK 4 app."""
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from . import profile  # noqa: E402


class AboutYou(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="About you", default_width=560, default_height=640)
        self.current = profile.load()
        self.rows = {}
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(title="About you",
                                     description="Every agent on Odd1 reads this when it starts a conversation.")
        page.add(group)
        for key, label, hint in profile.FIELDS:
            if key == "about":
                continue
            row = Adw.EntryRow(title=label, text=self.current[key])
            row.set_tooltip_text(hint)
            row.connect("changed", lambda *_: self.dirty())
            group.add(row)
            self.rows[key] = row
        self.rows["call_me"].set_title(f"What agents call you  (if empty: {profile.call_me({**self.current, 'call_me': ''}) or 'your first name'})")

        key, label, hint = next(f for f in profile.FIELDS if f[0] == "about")
        notes = Adw.PreferencesGroup(title=label, description=hint)
        self.about = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=10, bottom_margin=10,
                                  left_margin=12, right_margin=12)
        self.about.get_buffer().set_text(self.current["about"])
        self.about.get_buffer().connect("changed", lambda *_: self.dirty())
        frame = Gtk.Frame(child=Gtk.ScrolledWindow(child=self.about, min_content_height=140))
        notes.add(frame)
        page.add(notes)

        self.save_button = Gtk.Button(label="Save", sensitive=False)
        self.save_button.add_css_class("suggested-action")
        self.save_button.connect("clicked", lambda *_: self.save())
        header = Adw.HeaderBar()
        header.pack_end(self.save_button)
        self.toasts = Adw.ToastOverlay(child=page)
        view = Adw.ToolbarView(content=self.toasts)
        view.add_top_bar(header)
        self.set_content(view)

    def values(self):
        buffer = self.about.get_buffer()
        values = {key: row.get_text() for key, row in self.rows.items()}
        values["about"] = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
        return values

    def dirty(self):
        self.save_button.set_sensitive(any(v.strip() != self.current[k] for k, v in self.values().items()))

    def save(self):
        self.current = profile.save(self.values())
        profile.sync_git(self.current)
        self.save_button.set_sensitive(False)
        self.toasts.add_toast(Adw.Toast(title=f"Saved. New conversations will know you as {profile.call_me(self.current) or 'you'}."))


def main():
    app = Adw.Application(application_id="ai.odd1.Person")
    app.connect("activate", lambda a: (a.get_active_window() or AboutYou(a)).present())
    return app.run([])
