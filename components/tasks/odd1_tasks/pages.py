"""Decision pages: what the person has to decide, as a page with the options, their pros and cons, a recommendation
and the evidence; and a chat to discuss it before deciding.

An agent escalating a decision gives options (tasks_escalate with options=[{label, pros, cons}] and a
recommendation); `odd1-tasks page ID` renders the latest one, with the ticket and its log, to
<state>/pages/task-<ID>.html (Agent Settings opens it); `odd1-tasks discuss ID` opens a chat in the chat column that
already has the ticket and the page, so the person can ask before deciding; the chat records the decision as theirs
(approve, decline, cancel, or `odd1-tasks edit` to change the goal or check on their decision).
"""
from __future__ import annotations

import html
import os
import shutil
import subprocess
import time
from pathlib import Path

from .store import Store

STYLE = """
:root { --bg: #ffffff; --fg: #1b1d27; --dim: #5d6274; --card: #f3f4f8; --line: #dfe1ea; --accent: #3a5bd9;
        --good: #1e7d4f; --bad: #b3261e; }
@media (prefers-color-scheme: dark) { :root { --bg: #11131c; --fg: #e6e8f0; --dim: #9aa0b4; --card: #1a1d2a;
        --line: #2b2f40; --accent: #8aa4ff; --good: #6fd39e; --bad: #ff8a80; } }
body { background: var(--bg); color: var(--fg); font: 15px/1.5 Inter, system-ui, sans-serif; margin: 0; }
main { max-width: 860px; margin: 0 auto; padding: 24px 16px 48px; }
h1 { font-size: 22px; margin: 0 0 4px; } h2 { font-size: 16px; margin: 28px 0 8px; }
.meta { color: var(--dim); font-size: 13px; }
.question { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 14px 16px; }
.options { display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); }
.option { border: 1px solid var(--line); border-radius: 10px; padding: 12px 14px; }
.option.recommended { border: 2px solid var(--accent); }
.option h3 { font-size: 15px; margin: 0 0 6px; } .tag { color: var(--accent); font-size: 12px; font-weight: 600; }
.pros li::marker { content: "+  "; color: var(--good); } .cons li::marker { content: "−  "; color: var(--bad); }
ul { margin: 4px 0; padding-left: 18px; } pre { white-space: pre-wrap; font-size: 12px; color: var(--dim); }
.log div { border-top: 1px solid var(--line); padding: 6px 0; font-size: 13px; }
code { background: var(--card); padding: 1px 5px; border-radius: 4px; }
"""


def _items(value) -> list[str]:
    if isinstance(value, str):
        return [v.strip() for v in value.split(";") if v.strip()]
    return [str(v) for v in value or []]


def decision(task: dict) -> dict:
    """The latest decision asked of the person: the question, its options and the recommendation."""
    asked = [e for e in task["log"] if e["kind"] == "escalate"]
    if asked:
        e = asked[-1]
        return {"question": e["text"].removeprefix("needs a person: "), "options": e["data"].get("options") or [],
                "recommendation": e["data"].get("recommendation", ""), "by": e["actor"], "at": e["at"]}
    question = {"proposed": "Approve or decline this proposal.", "blocked": "Unblock it, or cancel it.",
                "parked": "Its check kept failing: cancel it, or propose a better task."}.get(task["status"], "")
    return {"question": question, "options": [], "recommendation": "", "by": "", "at": task["updated_at"]}


def render(task: dict) -> str:
    d, e = decision(task), html.escape
    cards = []
    for o in d["options"]:
        label = str(o.get("label", ""))
        rec = d["recommendation"] and label.lower() in d["recommendation"].lower()
        cards.append(f"""<div class="option{' recommended' if rec else ''}">{'<div class="tag">Recommended</div>' if rec else ''}
<h3>{e(label)}</h3>{f'<p>{e(str(o["what"]))}</p>' if o.get("what") else ''}
<ul class="pros">{''.join(f'<li>{e(p)}</li>' for p in _items(o.get('pros')))}</ul>
<ul class="cons">{''.join(f'<li>{e(c)}</li>' for c in _items(o.get('cons')))}</ul></div>""")
    log = "".join(f"<div><span class='meta'>{time.strftime('%m-%d %H:%M', time.localtime(x['at']))} · "
                  f"{e(x['actor'])} · {e(x['kind'])}</span><br>{e(x['text'][:600])}</div>" for x in task["log"][-15:])
    checks = [x for x in task["log"] if x["kind"] == "check"]
    check = f"<h2>Latest check</h2><pre>{e(checks[-1]['data'].get('output', '')[-2000:])}</pre>" if checks else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Decision #{task['id']}</title>
<style>{STYLE}</style></head><body><main>
<div class="meta">Ticket #{task['id']} · {e(task['status'])} · {e(', '.join(task['tags']))}{' · asked by ' + e(d['by']) if d['by'] else ''}</div>
<h1>{e(task['goal'])}</h1>
<h2>What you decide</h2><div class="question">{e(d['question']) or 'Nothing is asked right now.'}</div>
{'<h2>Options</h2><div class="options">' + ''.join(cards) + '</div>' if cards else ''}
{'<h2>Recommendation</h2><p>' + e(d['recommendation']) + '</p>' if d['recommendation'] else ''}
<h2>How to decide</h2><p>Press <b>Discuss</b> in Agent Settings to talk it through with an agent that has this page,
or tell any chat, e.g. <code>decide #{task['id']}: {e(str(d['options'][0].get('label', 'approve')) if d['options'] else 'approve')}</code>.
Your decision is recorded on the ticket as yours.</p>
<h2>The check</h2><pre>{e(task['done_when'])}</pre>{check}
<h2>Its log</h2><div class="log">{log}</div>
</main></body></html>"""


def write(store: Store, task_id: int) -> Path:
    path = store.state / "pages" / f"task-{task_id}.html"
    path.parent.mkdir(exist_ok=True)
    path.write_text(render(store.show(task_id)))
    return path


def primer(task: dict, page: Path) -> str:
    d = decision(task)
    return (f"The person wants to discuss ticket #{task['id']} before deciding. Its decision page is {page} (read it) "
            f"and `odd1-tasks show {task['id']}` has the whole log. The question: {d['question']} Answer their "
            "questions plainly, with your recommendation. When they decide, record it as theirs: log their words on "
            "the ticket (odd1-tasks log), then approve, decline, cancel, or `odd1-tasks edit` the goal or check as "
            "they said. Start by summing up the decision in a few lines.")


def discuss(store: Store, task_id: int, display: str | None = None) -> list[str]:
    """Open a chat in the chat column (a terminal with the chat role) primed with the ticket and its page."""
    task, page = store.show(task_id), write(store, task_id)
    if shutil.which("odd1-desktop"):  # the column's chat window: its bar docks it and holds its settings
        command = ["odd1-desktop", "chat", f"--title=Discuss #{task_id}", "--role=odd1-agent:claude",
                   "--cwd=" + os.path.expanduser("~"), "--command=claude", f"--prompt={primer(task, page)}"]
    else:
        command = ["xfce4-terminal", "--disable-server", f"--title=Discuss #{task_id}", "--role=odd1-agent:claude",
                   "--working-directory=" + os.path.expanduser("~"), "-x", "bash", "-lc",
                   f"claude {_quote(primer(task, page))}; exec bash"]
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE") and k != "ODD1_TASKS_AGENT"}
    env["DISPLAY"] = display or env.get("DISPLAY", ":0")
    if shutil.which(command[0]):
        subprocess.Popen(command, env=env, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return command


def _quote(text: str) -> str:
    return "'" + text.replace("'", "'\\''") + "'"


def open_page(path: Path) -> None:
    opener = shutil.which("xdg-open") or "firefox"
    subprocess.Popen([opener, str(path)], start_new_session=True, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)
