"""The Agent Settings app: one window for the health and settings of every agent system (GTK 4, libadwaita).

Pages: Overview (each role, today, what waits for you, the machine), Tasks (approve, stop, unblock, leave notes),
Roles (on/off, budgets, model, tags; their files), Claude Code (version and update, the agent systems in the chat
menu, the MCP servers every agent gets). Every read and change goes through model.py; this file only shows it.
GTK 4 is accessible over AT-SPI, so agents can operate this window too (the screen component).
"""
from __future__ import annotations

import sys
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from odd1_tasks.store import Store, TaskError  # noqa: E402

from . import model  # noqa: E402

PAGES = (("overview", "Overview", "utilities-system-monitor-symbolic"),
         ("tasks", "Tasks", "view-list-bullet-symbolic"),
         ("roles", "Roles", "system-users-symbolic"),
         ("claude", "Claude Code", "applications-system-symbolic"))
REFRESH_S = 5


def row(title, subtitle="", suffix=None, cls=Adw.ActionRow):
    r = cls(use_markup=False)  # plain text: goals and checks hold "&&" and "<" (markup first, then the text)
    r.set_title(title)
    r.set_subtitle(subtitle)
    if suffix is not None:
        r.add_suffix(suffix)
    return r


def badge(text, kind=""):
    label = Gtk.Label(label=text, valign=Gtk.Align.CENTER)
    if kind:
        label.add_css_class(kind)
    return label


def button(text, callback, kind=""):
    b = Gtk.Button(label=text, valign=Gtk.Align.CENTER)
    if kind:
        b.add_css_class(kind)
    b.connect("clicked", lambda *_: callback())
    return b


class Section:
    """A preferences group whose rows are replaced as a whole on refresh."""

    def __init__(self, page, title, description=""):
        self.group = Adw.PreferencesGroup(title=title, description=description)
        page.add(self.group)
        self.rows = []

    def set(self, rows):
        for r in self.rows:
            self.group.remove(r)
        self.rows = list(rows)
        for r in self.rows:
            self.group.add(r)


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Agent Settings", default_width=1040, default_height=740)
        self.store = Store()
        self.toasts = Adw.ToastOverlay()
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.content = Adw.NavigationPage(title="Overview")
        content = Adw.ToolbarView(content=self.stack)
        content.add_top_bar(Adw.HeaderBar())
        self.content.set_child(content)
        sidebar = Gtk.ListBox(css_classes=["navigation-sidebar"])
        for name, title, icon in PAGES:
            box = Gtk.Box(spacing=12, margin_start=6, margin_top=6, margin_bottom=6)
            box.append(Gtk.Image(icon_name=icon))
            box.append(Gtk.Label(label=title, xalign=0))
            item = Gtk.ListBoxRow(child=box, name=name)
            sidebar.append(item)
        sidebar.connect("row-selected", lambda _, r: r and self.show_page(r.get_name()))
        side = Adw.ToolbarView(content=sidebar)
        side.add_top_bar(Adw.HeaderBar())
        split = Adw.NavigationSplitView(sidebar=Adw.NavigationPage(title="Agent Settings", child=side), content=self.content)
        self.toasts.set_child(split)
        self.set_content(self.toasts)
        self.sidebar = sidebar
        self.build_overview()
        self.build_tasks()
        self.build_roles()
        self.build_claude()
        sidebar.select_row(sidebar.get_row_at_index(0))
        self.refresh()
        GLib.timeout_add_seconds(REFRESH_S, self.refresh)

    # -- plumbing
    def toast(self, text):
        toast = Adw.Toast(timeout=4, use_markup=False)
        toast.set_title(text)
        self.toasts.add_toast(toast)

    def act(self, what, *args, done=""):
        """Run a model call; say what happened; show the change at once."""
        try:
            what(self.store, *args)
        except (TaskError, ValueError, OSError) as e:
            self.toast(str(e))
        else:
            if done:
                self.toast(done)
        self.refresh(force=True)

    def show_page(self, name):
        self.stack.set_visible_child_name(name)
        self.content.set_title(dict((n, t) for n, t, _ in PAGES)[name])
        if name == "roles":
            self.fill_roles()
        if name == "claude":
            self.fill_claude()

    def go_tasks(self, view):
        self.task_filter.set_selected(list(model.FILTERS).index(view))
        self.sidebar.select_row(self.sidebar.get_row_at_index(1))

    def refresh(self, force=False):
        try:
            self.fill_overview()
            self.fill_tasks(force)
        except Exception as e:  # a broken file must not take the window down; say it and keep going
            self.toast(f"Could not read: {e}")
        return True

    # -- Overview
    def build_overview(self):
        page = Adw.PreferencesPage()
        self.ov_line = Adw.PreferencesGroup()
        page.add(self.ov_line)
        self.ov_line_label = Gtk.Label(xalign=0, wrap=True, css_classes=["dim-label"])
        self.ov_line.add(self.ov_line_label)
        self.ov_roles = Section(page, "Roles", "Agents the task manager runs, one task at a time each")
        self.ov_needs = Section(page, "Needs you", "Only what an agent may not or could not decide")
        self.ov_agents = Section(page, "Handled by agents", "Routine decisions the manager makes for you")
        self.ov_today = Section(page, "Today")
        self.ov_machine = Section(page, "Machine", "From the caretaker")
        self.stack.add_named(page, "overview")

    def fill_overview(self):
        view = model.overview(self.store)
        self.ov_line_label.set_label(view["line"])
        rows = []
        for r in view["roles"]:
            if r["runner"] != "active":
                state = badge(f"Runner {r['runner']}", "error")
            elif not r["enabled"]:
                state = badge("Paused", "warning")
            else:
                state = badge("On", "success")
            doing = r["doing"]
            subtitle = (f"Working on #{doing['id']}: {doing['goal']}" if doing and doing["status"] == "claimed" else
                        f"Checking #{doing['id']}" if doing else "Idle")
            subtitle += f" · {r['runs_today']} of {r['runs_per_day']} runs today · ${r['cost_today']:.2f}"
            rows.append(row(r["name"], subtitle, state))
        self.ov_roles.set(rows)
        kinds = {"decide": "To approve or decline", "blocked": "Blocked", "parked": "Check kept failing",
                 "orphan": "No role runs it", "escalated": "Escalated to you", "runner": "Runner"}
        needs = [row(i["text"], kinds.get(i["kind"], i["kind"]),
                     button("Open", lambda: self.go_tasks("Waiting for a decision")) if i["task"] else None)
                 for i in view["needs_you"]]
        self.ov_needs.set(needs or [row("Nothing needs you", "", badge("✓", "success"))])
        by_agent = {}
        for i in view["with_agents"]:
            by_agent.setdefault(i["who"], []).append(i)
        self.ov_agents.set([row(name, f"{len(items)} decision{'s' if len(items) != 1 else ''} to make: " +
                                "; ".join(f"#{i['task']}" for i in items[:8]), badge(str(len(items))))
                            for name, items in by_agent.items()] or [row("Nothing waiting", "")])
        t = view["today"]
        self.ov_today.set([row("Runs", "", badge(str(t["runs"]))), row("Cost", "", badge(f"${t['cost']:.2f}")),
                           row("Results", "", badge(f"{t['done']} done · {t['failed']} failed checks"))])
        m = view["machine"]
        level = {"ok": "success", "elevated": "warning", "high": "error"}.get(m["level"], "dim-label")
        state = "" if m["state"] == "current" else f" ({m['state']})"
        self.ov_machine.set([row("Pressure", "", badge(m["level"] + state, level))] +
                            [row(str(p)) for p in m["problems"]])

    # -- Tasks
    def build_tasks(self):
        page = Adw.PreferencesPage()
        top = Adw.PreferencesGroup()
        self.task_filter = Gtk.DropDown.new_from_strings(list(model.FILTERS))
        self.task_filter.set_valign(Gtk.Align.CENTER)
        self.task_filter.connect("notify::selected", lambda *_: self.fill_tasks(force=True))
        top.add(row("Show", "", self.task_filter))
        page.add(top)
        self.task_list = Section(page, "Tasks")
        self.task_seen, self.task_open = None, set()
        self.stack.add_named(page, "tasks")

    def fill_tasks(self, force=False):
        view = list(model.FILTERS)[self.task_filter.get_selected()]
        tasks = model.tasks(self.store, view)
        seen = (view, [(t["id"], t["status"], t["updated_at"]) for t in tasks])
        if seen == self.task_seen and not force:
            return  # unchanged: keep open rows open
        self.task_seen = seen
        self.task_open = {r.task_id for r in self.task_list.rows if r.get_expanded()}
        self.task_list.set([self.task_row(t) for t in tasks] or [row("Nothing here")])

    def task_row(self, t):
        who = ", ".join(t["roles"]) or "no role yet"
        waits = {None: "", "person": " · waiting on you"}.get(t["waiting_on"], f" · waiting on {t['waiting_on']}")
        r = row(f"#{t['id']}  {t['goal']}", f"{t['status']} · {', '.join(t['tags'])} · run by {who}{waits}",
                cls=Adw.ExpanderRow)
        r.task_id = t["id"]
        r.add_suffix(badge(t["status"], {"proposed": "accent", "blocked": "warning", "parked": "error",
                                         "done": "success"}.get(t["status"], "dim-label")))
        r.add_row(row("Done when", t["done_when"]))
        budget = f"{t['max_turns'] or 'role default'} turns, {t['max_minutes'] or 'role default'} minutes"
        r.add_row(row("Budget", f"{budget} · attempt {t['attempts']} of {t['max_attempts']}"
                      + (f" · every {t['schedule']}" if t["schedule"] else "")))
        actions = Gtk.Box(spacing=8, margin_top=8, margin_bottom=8, margin_start=12)
        if t["status"] == "proposed":
            actions.append(button("Approve", lambda: self.act(model.approve, t["id"], done=f"#{t['id']} approved"),
                                  "suggested-action"))
        if t["status"] == "blocked":
            actions.append(button("Unblock", lambda: self.act(model.approve, t["id"], done=f"#{t['id']} ready")))
        if t["status"] == "proposed":
            actions.append(button("Decline", lambda: self.ask_reason(t, "Decline", model.decline)))
        if t["status"] == "claimed":
            actions.append(button("Stop", lambda: self.act(model.stop, t["id"], done=f"#{t['id']} stopped"),
                                  "destructive-action"))
        if t["status"] in ("ready", "claimed", "blocked", "verifying", "parked") or (
                t["status"] == "done" and t["schedule"]):
            actions.append(button("Cancel", lambda: self.ask_reason(t, "Cancel", model.cancel), "destructive-action"))
        if actions.get_first_child():
            r.add_row(actions)
        note = Adw.EntryRow(title="Leave a note for the next run", show_apply_button=True)
        note.connect("apply", lambda e: (self.act(model.note, t["id"], e.get_text(), done="Note added"),
                                         e.set_text("")))
        r.add_row(note)
        for entry in self.store.show(t["id"])["log"][-8:]:
            when = time.strftime("%d %b %H:%M", time.localtime(entry["at"]))
            r.add_row(row(entry["text"], f"{when} · {entry['actor']} · {entry['kind']}"))
        r.set_expanded(t["id"] in self.task_open)
        return r

    def ask_reason(self, t, verb, what):
        """Decline or cancel only with a reason: it stays in the task's log for everyone who reads it later."""
        dialog = Adw.AlertDialog(heading=f"{verb} #{t['id']}?", body=t["goal"])
        entry = Gtk.Entry(placeholder_text="Why? (kept in the task's log)", activates_default=True)
        dialog.set_extra_child(entry)
        dialog.add_response("keep", "Keep it")
        dialog.add_response("go", verb)
        dialog.set_response_appearance("go", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("go")
        dialog.connect("response", lambda _, answer: answer == "go" and self.act(
            what, t["id"], entry.get_text(), done=f"#{t['id']} {'declined' if verb == 'Decline' else 'cancelled'}"))
        dialog.present(self)

    # -- Roles
    def build_roles(self):
        self.roles_page = Adw.PreferencesPage()
        self.role_sections = []
        self.stack.add_named(self.roles_page, "roles")

    def fill_roles(self):
        for section in self.role_sections:
            self.roles_page.remove(section.group)
        self.role_sections = []
        for name in model.role_names(self.store):
            r = model.role(self.store, name)
            section = Section(self.roles_page, name, f"Its settings file: {r['folder']}/role.toml")
            rows = []
            on = Adw.SwitchRow(title="Takes new tasks", subtitle="Off pauses the role: no new runs, no model calls",
                               active=r["enabled"])
            on.connect("notify::active", lambda w, _, n=name: self.set_role(n, enabled=w.get_active()))
            rows.append(on)
            limits = [("runs_per_day", "Runs per day", "At most this many in any 24 hours"),
                      ("max_turns", "Max turns per run", "Caps every task's own budget"),
                      ("max_minutes", "Max minutes per run", "The run is stopped after this")]
            if r["decides"]:
                limits.append(("escalate_after_minutes", "Escalate after (minutes)",
                               "A decision it leaves this long comes to you"))
            for key, title, subtitle in limits:
                low, high = model.LIMITS[key]
                spin = Adw.SpinRow.new_with_range(low, high, 1)
                spin.set_title(title)
                spin.set_subtitle(subtitle)
                spin.set_value(r[key])
                spin.connect("notify::value",
                             lambda w, _, n=name, k=key: self.set_role(n, **{k: int(w.get_value())}))
                rows.append(spin)
            for key, title, value in (("model", "Model (empty: Claude Code's default)", r["model"]),
                                      ("tags", "Tags it claims (comma separated)", ", ".join(r["tags"])),
                                      ("decides", "Decides for you on tags (approve, decline, unblock, cancel)",
                                       ", ".join(r["decides"]))):
                entry = Adw.EntryRow(title=title, text=value, show_apply_button=True)
                entry.connect("apply", lambda w, n=name, k=key: self.set_role(
                    n, **{k: [s.strip() for s in w.get_text().split(",") if s.strip()] if k in ("tags", "decides")
                          else w.get_text().strip()}))
                rows.append(entry)
            files = Gtk.Box(spacing=8, margin_top=8, margin_bottom=8, margin_start=12)
            files.append(button("Instructions", lambda p=r["instructions"]: model.open_file(p)))
            files.append(button("Permissions", lambda p=r["permissions"]: model.open_file(p)))
            if r["report"]:
                files.append(button("Latest report", lambda p=r["report"]: model.open_file(p)))
            rows.append(files)
            section.set(rows)
            self.role_sections.append(section)

    def set_role(self, name, **values):
        try:
            model.set_role(self.store, name, **values)
        except (ValueError, OSError) as e:
            self.toast(str(e))
            self.fill_roles()  # show what the file really says
            return
        self.toast(f"{name}: saved")
        self.refresh(force=True)

    # -- Claude Code and tools
    def build_claude(self):
        page = Adw.PreferencesPage()
        self.cl_code = Section(page, "Claude Code", "The agent every chat and every role runs")
        self.cl_agents = Section(page, "Agent systems", "What + New chat in the chat column offers")
        self.cl_mcp = Section(page, "MCP servers", "Tools every agent on this machine gets")
        self.stack.add_named(page, "claude")

    def fill_claude(self):
        latest = row("Newest on npm", "checking…")
        update = button("Update now", self.update_claude)
        self.cl_code.set([row("Installed", model.claude_version()), latest, row("Update", "", update)])
        threading.Thread(target=lambda: GLib.idle_add(latest.set_subtitle, model.claude_latest()), daemon=True).start()
        tools = model.tools()
        self.cl_agents.set([row(a.get("title", n), a.get("description", "")) for n, a in tools["agents"].items()]
                           or [row("None registered")])
        self.cl_mcp.set([row(n, c) for n, c in tools["mcp_servers"].items()] or [row("None registered")])

    def update_claude(self):
        self.toast("Updating Claude Code…")

        def run():
            ok, text = model.claude_update()
            GLib.idle_add(lambda: (self.toast(text or ("Updated" if ok else "Update failed")), self.fill_claude()))
        threading.Thread(target=run, daemon=True).start()


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id="org.odd1.Agents")

    def do_activate(self):
        window = self.get_active_window() or Window(self)
        window.present()


def main(argv=None) -> int:
    return App().run(sys.argv[:1] if argv is None else argv)


if __name__ == "__main__":
    sys.exit(main())
