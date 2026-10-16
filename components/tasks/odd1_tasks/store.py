"""The task store: one SQLite file whose triggers hold the rules, so they bind every writer (CLI, MCP, raw SQL).

Triggers enforce: a new task starts proposed (only a run of an approved recurring task starts ready); status moves
only along proposed>ready>claimed>verifying>done, claimed>ready|blocked, blocked>ready, verifying>ready|parked;
a claim needs the claimer's tags (agents table), finished dependencies and no resource shared with another claimed
or verifying task; done needs a passing check logged after the last finish; retries before parked; the log is
append-only; nothing is deleted; an approved task's goal, check, tags, resources and budget are fixed.
Python adds what triggers cannot: atomic claims (BEGIN IMMEDIATE), leases and their expiry, recurring runs, who may
approve (a person, never an agent), and running done_when.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import time
from pathlib import Path

LEASE_SECONDS = float(os.environ.get("ODD1_TASKS_LEASE_SECONDS", 600))
CHECK_TIMEOUT = 300
OPEN = ("proposed", "ready", "claimed", "blocked", "verifying")
CLOSED = ("done", "parked", "declined", "cancelled")  # declined: a proposal a person turned down; cancelled: by a person
STATUSES = OPEN + CLOSED
VERSION = 3  # PRAGMA user_version: 1 added declined, cancelled, tasks.run_if and agents.decides; 2 messages;
#             3 messages.agent (the subagent that asked) and messages.waits_until
FIXED = "goal, done_when, tags, resources, depends_on, schedule, run_if, max_turns, max_minutes, max_attempts, created_by"
MOVES = ("proposed>ready", "ready>claimed", "claimed>ready", "claimed>blocked", "claimed>verifying",
         "blocked>ready", "verifying>ready", "verifying>parked", "verifying>done", "proposed>declined",
         "ready>cancelled", "claimed>cancelled", "blocked>cancelled", "verifying>cancelled", "parked>cancelled",
         "done>cancelled")

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS tasks(
  id INTEGER PRIMARY KEY,
  goal TEXT NOT NULL CHECK (length(trim(goal)) > 0),
  done_when TEXT NOT NULL CHECK (length(trim(done_when)) > 0),
  tags TEXT NOT NULL CHECK (json_valid(tags) AND json_array_length(tags) > 0),
  priority INTEGER NOT NULL DEFAULT 0,
  max_turns INTEGER, max_minutes REAL,
  depends_on TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(depends_on)),
  resources TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(resources)),
  schedule TEXT,
  run_if TEXT,
  parent_id INTEGER REFERENCES tasks(id),
  created_by TEXT NOT NULL, created_at REAL NOT NULL,
  status TEXT NOT NULL CHECK (status IN ({", ".join(repr(s) for s in STATUSES)})),
  approved_by TEXT, approved_at REAL,
  claim_agent TEXT, lease_until REAL,
  attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 3,
  updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS log(
  id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL REFERENCES tasks(id), at REAL NOT NULL,
  actor TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL DEFAULT '', data TEXT NOT NULL DEFAULT '{{}}');
CREATE INDEX IF NOT EXISTS log_task ON log(task_id, id);
CREATE TABLE IF NOT EXISTS agents(name TEXT PRIMARY KEY, tags TEXT NOT NULL CHECK (json_valid(tags)),
  decides TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(decides)));
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY, at REAL NOT NULL, sender TEXT NOT NULL, actor TEXT NOT NULL, session TEXT,
  recipient TEXT NOT NULL, task_id INTEGER REFERENCES tasks(id), reply_to INTEGER REFERENCES messages(id),
  text TEXT NOT NULL CHECK (length(trim(text)) > 0), agent TEXT, waits_until REAL);
CREATE INDEX IF NOT EXISTS messages_reply ON messages(reply_to);
CREATE TRIGGER IF NOT EXISTS no_message_update BEFORE UPDATE ON messages
BEGIN SELECT RAISE(ABORT, 'messages are append-only'); END;
CREATE TRIGGER IF NOT EXISTS no_message_delete BEFORE DELETE ON messages
BEGIN SELECT RAISE(ABORT, 'messages are append-only'); END;
CREATE TRIGGER IF NOT EXISTS one_reply BEFORE INSERT ON messages
WHEN NEW.reply_to IS NOT NULL AND EXISTS (SELECT 1 FROM messages m WHERE m.reply_to = NEW.reply_to)
BEGIN SELECT RAISE(ABORT, 'that message is answered already'); END;

CREATE TRIGGER IF NOT EXISTS no_task_delete BEFORE DELETE ON tasks
BEGIN SELECT RAISE(ABORT, 'tasks are never deleted'); END;
CREATE TRIGGER IF NOT EXISTS no_log_update BEFORE UPDATE ON log
BEGIN SELECT RAISE(ABORT, 'the log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS no_log_delete BEFORE DELETE ON log
BEGIN SELECT RAISE(ABORT, 'the log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS new_is_proposed BEFORE INSERT ON tasks
WHEN NOT ((NEW.status = 'proposed' AND NEW.approved_at IS NULL AND NEW.claim_agent IS NULL)
          OR (NEW.status = 'ready' AND NEW.claim_agent IS NULL AND EXISTS (SELECT 1 FROM tasks p WHERE
              p.id = NEW.parent_id AND p.schedule IS NOT NULL AND p.approved_at IS NOT NULL)))
BEGIN SELECT RAISE(ABORT, 'a new task starts as proposed and needs approval'); END;
CREATE TRIGGER IF NOT EXISTS fixed_after_approval BEFORE UPDATE OF {FIXED} ON tasks
WHEN OLD.status != 'proposed'
BEGIN SELECT RAISE(ABORT, 'an approved task cannot be changed'); END;
CREATE TRIGGER IF NOT EXISTS allowed_moves BEFORE UPDATE OF status ON tasks
WHEN OLD.status != NEW.status AND OLD.status || '>' || NEW.status NOT IN ({", ".join(repr(m) for m in MOVES)})
BEGIN SELECT RAISE(ABORT, 'status change not allowed'); END;
CREATE TRIGGER IF NOT EXISTS approve_recorded BEFORE UPDATE OF status ON tasks
WHEN OLD.status = 'proposed' AND NEW.status = 'ready' AND (NEW.approved_at IS NULL OR NEW.approved_by IS NULL)
BEGIN SELECT RAISE(ABORT, 'approval must name who approved'); END;
CREATE TRIGGER IF NOT EXISTS claim_lease BEFORE UPDATE OF status ON tasks
WHEN NEW.status = 'claimed' AND OLD.status != 'claimed' AND (NEW.claim_agent IS NULL OR NEW.lease_until IS NULL)
BEGIN SELECT RAISE(ABORT, 'a claim names its agent and lease'); END;
CREATE TRIGGER IF NOT EXISTS claim_tags BEFORE UPDATE OF status ON tasks
WHEN NEW.status = 'claimed' AND OLD.status != 'claimed' AND EXISTS (
  SELECT 1 FROM json_each(NEW.tags) t WHERE t.value NOT IN (
    SELECT a.value FROM agents g, json_each(g.tags) a WHERE g.name = NEW.claim_agent))
BEGIN SELECT RAISE(ABORT, 'the agent does not hold every tag the task requires'); END;
CREATE TRIGGER IF NOT EXISTS claim_deps BEFORE UPDATE OF status ON tasks
WHEN NEW.status = 'claimed' AND OLD.status != 'claimed' AND EXISTS (
  SELECT 1 FROM json_each(NEW.depends_on) d LEFT JOIN tasks x ON x.id = d.value WHERE x.status IS NOT 'done')
BEGIN SELECT RAISE(ABORT, 'a dependency is not done'); END;
CREATE TRIGGER IF NOT EXISTS claim_resources BEFORE UPDATE OF status ON tasks
WHEN NEW.status = 'claimed' AND OLD.status != 'claimed' AND EXISTS (
  SELECT 1 FROM tasks o, json_each(o.resources) a, json_each(NEW.resources) b
  WHERE o.id != NEW.id AND o.status IN ('claimed', 'verifying')
    AND (a.value = b.value OR a.value LIKE b.value || '/%' OR b.value LIKE a.value || '/%'))
BEGIN SELECT RAISE(ABORT, 'a resource is held by another claimed task'); END;
CREATE TRIGGER IF NOT EXISTS done_needs_check BEFORE UPDATE OF status ON tasks
WHEN NEW.status = 'done' AND NOT EXISTS (
  SELECT 1 FROM log c WHERE c.task_id = NEW.id AND c.kind = 'check' AND json_extract(c.data, '$.exit') = 0
    AND c.id > (SELECT max(f.id) FROM log f WHERE f.task_id = NEW.id AND f.kind = 'finish'))
BEGIN SELECT RAISE(ABORT, 'done only when done_when passed after the last finish'); END;
CREATE TRIGGER IF NOT EXISTS cancel_done_series_only BEFORE UPDATE OF status ON tasks
WHEN OLD.status = 'done' AND NEW.status = 'cancelled' AND OLD.schedule IS NULL
BEGIN SELECT RAISE(ABORT, 'a done task stays done (only a recurring task is cancelled after it is done)'); END;
CREATE TRIGGER IF NOT EXISTS retries_first BEFORE UPDATE OF status ON tasks
WHEN OLD.status = 'verifying' AND ((NEW.status = 'parked' AND NEW.attempts < NEW.max_attempts)
                                OR (NEW.status = 'ready' AND NEW.attempts >= NEW.max_attempts))
BEGIN SELECT RAISE(ABORT, 'parked only after the retries are used up'); END;
"""


PERSON_ONLY = frozenset({"needs-person", "manage"})  # tasks tagged so are decided by a person, whatever is delegated


def covers(decides, tags) -> bool:
    """Whether a role delegated `decides` may decide a task with `tags`: one of its tags is delegated (the kind of
    work, e.g. "friction" on a ["friction", "desktop"] ticket) and none is a person's only."""
    return bool(set(decides) & set(tags)) and not PERSON_ONLY & set(tags)


class TaskError(Exception):
    """A rule refused the operation; the message says which."""


def actor_from_env() -> str:
    """Who is calling: the runner sets ODD1_TASKS_AGENT for its agent; anyone else is a person."""
    agent = os.environ.get("ODD1_TASKS_AGENT", "").strip()
    return agent or "person:" + (os.environ.get("USER") or os.environ.get("LOGNAME") or "unknown")


def is_person(actor: str) -> bool:
    return actor.startswith("person:")


def interval_seconds(schedule: str) -> float:
    """'30m', '12h', '1d', '2w' (an optional 'every ' in front) -> seconds."""
    s = schedule.strip().lower().removeprefix("every").strip()
    units = {"m": 60, "h": 3600, "d": 86400, "w": 604800}
    try:
        n = float(s[:-1])
    except ValueError:
        n = 0
    if s[-1:] not in units or n <= 0:
        raise TaskError(f"schedule {schedule!r}: use a number and m, h, d or w (e.g. '1d')")
    return n * units[s[-1]]


def _row(r: sqlite3.Row) -> dict:
    t = dict(r)
    for k in ("tags", "depends_on", "resources"):
        t[k] = json.loads(t[k])
    return t


class Store:
    def __init__(self, state: str | Path | None = None):
        self.state = Path(state or os.environ.get("ODD1_TASKS_STATE", "/var/lib/odd1/tasks"))
        self.state.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.state / "tasks.db", timeout=30, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self._migrate()
        self.db.executescript(SCHEMA)
        self.db.execute(f"PRAGMA user_version = {VERSION}")

    def _migrate(self) -> None:
        """Bring an older store to this schema. Version 0 -> 1: the status column's CHECK gains declined and
        cancelled; SQLite cannot change a CHECK in place, so the table is rebuilt (every row kept, the log untouched;
        its triggers are recreated from SCHEMA right after)."""
        messages = [r[1] for r in self.db.execute("PRAGMA table_info(messages)")]
        for column in ("agent TEXT", "waits_until REAL"):
            if messages and column.split()[0] not in messages:
                self.db.execute(f"ALTER TABLE messages ADD COLUMN {column}")
        agents = [r[1] for r in self.db.execute("PRAGMA table_info(agents)")]
        if agents and "decides" not in agents:
            self.db.execute("ALTER TABLE agents ADD COLUMN decides TEXT NOT NULL DEFAULT '[]'")
        old = self.db.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'tasks'").fetchone()
        if old is None or "'declined'" in old[0]:
            return
        start = SCHEMA.index("CREATE TABLE IF NOT EXISTS tasks(")
        new = SCHEMA[start:SCHEMA.index(");", start) + 2].replace("IF NOT EXISTS tasks(", "tasks_new(")
        columns = ", ".join(r[1] for r in self.db.execute("PRAGMA table_info(tasks)"))
        self.db.executescript(f"""BEGIN IMMEDIATE;
            {new}
            INSERT INTO tasks_new ({columns}) SELECT {columns} FROM tasks;
            DROP TABLE tasks;
            ALTER TABLE tasks_new RENAME TO tasks;
            COMMIT;""")

    # -- plumbing
    def _tx(self):
        return _Tx(self.db)

    def _get(self, task_id: int) -> dict:
        r = self.db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if r is None:
            raise TaskError(f"no task {task_id}")
        return _row(r)

    def _log(self, task_id, actor, kind, text="", **data) -> None:
        self.db.execute("INSERT INTO log(task_id, at, actor, kind, text, data) VALUES (?, ?, ?, ?, ?, ?)",
                        (task_id, time.time(), actor, kind, text, json.dumps(data)))

    def _set(self, task_id, **fields) -> None:
        fields["updated_at"] = time.time()
        cols = ", ".join(f"{k} = ?" for k in fields)
        try:
            self.db.execute(f"UPDATE tasks SET {cols} WHERE id = ?", (*fields.values(), task_id))
        except sqlite3.IntegrityError as e:
            raise TaskError(str(e)) from None

    def _holder(self, t: dict, actor: str) -> None:
        if t["status"] != "claimed":
            raise TaskError(f"task {t['id']} is {t['status']}, not claimed")
        if not is_person(actor) and actor.split(":")[-1] != t["claim_agent"]:
            raise TaskError(f"task {t['id']} is claimed by {t['claim_agent']}, not {actor}")

    # -- operations
    def create(self, goal: str, done_when: str, tags, actor: str, priority: int = 0, max_turns=None,
               max_minutes=None, depends_on=(), resources=(), schedule=None, max_attempts: int = 3,
               run_if: str | None = None) -> dict:
        if schedule:
            interval_seconds(schedule)
        tags = sorted({t.strip() for t in tags if t.strip()})
        if not tags:
            raise TaskError("a task needs at least one tag (it decides which agent may claim it)")
        now = time.time()
        with self._tx():
            for d in depends_on:
                self._get(int(d))
            try:
                cur = self.db.execute(
                    "INSERT INTO tasks(goal, done_when, tags, priority, max_turns, max_minutes, depends_on, resources,"
                    " schedule, run_if, created_by, created_at, status, max_attempts, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'proposed', ?, ?)",
                    (goal.strip(), done_when.strip(), json.dumps(tags), priority, max_turns, max_minutes,
                     json.dumps([int(d) for d in depends_on]), json.dumps(list(resources)), schedule or None,
                     (run_if or "").strip() or None, actor, now, max_attempts, now))
            except sqlite3.IntegrityError as e:
                raise TaskError(str(e)) from None
            self._log(cur.lastrowid, actor, "create", "proposed; waits for a person's approval")
        return self._get(cur.lastrowid)

    def list(self, status: str | None = None, tag: str | None = None) -> list[dict]:
        self.maintain()
        rows = [_row(r) for r in self.db.execute(
            "SELECT * FROM tasks WHERE (? IS NULL OR status = ?) ORDER BY id", (status, status))]
        return [t for t in rows if tag is None or tag in t["tags"]]

    def show(self, task_id: int) -> dict:
        self.maintain()
        t = self._get(task_id)
        t["log"] = [dict(r, data=json.loads(r["data"])) for r in self.db.execute(
            "SELECT id, at, actor, kind, text, data FROM log WHERE task_id = ? ORDER BY id", (task_id,))]
        return t

    def may_decide(self, actor: str, t: dict) -> bool:
        """A person decides anything. An agent decides (approve, decline, unblock, cancel) only tasks of a kind a
        person delegated to it (`covers`: agents.decides, from its role.toml), and never a task it proposed itself."""
        if is_person(actor):
            return True
        r = self.db.execute("SELECT decides FROM agents WHERE name = ?", (actor,)).fetchone()
        decides = set(json.loads(r["decides"])) if r else set()
        return covers(decides, t["tags"]) and t["created_by"] != actor

    def _authority(self, actor: str, t: dict) -> None:
        if not self.may_decide(actor, t):
            raise TaskError(f"{actor} may not decide task {t['id']} (tags {', '.join(t['tags'])}"
                            + (", proposed by itself" if t["created_by"] == actor else "")
                            + "): escalate it to a person")

    def approve(self, task_id: int, actor: str) -> dict:
        with self._tx():
            t = self._get(task_id)
            self._authority(actor, t)
            if t["status"] == "proposed":
                self._set(task_id, status="ready", approved_by=actor, approved_at=time.time())
                self._log(task_id, actor, "approve", "approved" + (f"; recurring every {t['schedule']}"
                                                                     if t["schedule"] else ""))
            elif t["status"] == "blocked":
                self._set(task_id, status="ready")
                self._log(task_id, actor, "approve", "unblocked")
            else:
                raise TaskError(f"task {task_id} is {t['status']}; only proposed or blocked tasks are approved")
        return self._get(task_id)

    def decline(self, task_id: int, actor: str, reason: str) -> dict:
        """A person turns a proposal down. It stays in the store (closed), with the reason in its log."""
        return self._close(task_id, actor, reason, "declined", ("proposed",))

    def cancel(self, task_id: int, actor: str, reason: str) -> dict:
        """A person cancels open work. A running agent learns it at its runner's next lease renewal (within 2
        minutes) and is stopped; a recurring task's series ends with it."""
        return self._close(task_id, actor, reason, "cancelled", OPEN[1:] + ("parked", "done"))

    def _close(self, task_id, actor, reason, status, allowed) -> dict:
        if not reason.strip():
            raise TaskError(f"say why it is {status} (the reason stays in its log)")
        with self._tx():
            t = self._get(task_id)
            self._authority(actor, t)
            if t["status"] not in allowed:
                raise TaskError(f"task {task_id} is {t['status']}; it cannot be {status}")
            self._set(task_id, status=status, claim_agent=None, lease_until=None)
            self._log(task_id, actor, status[:-1] if status == "declined" else "cancel", f"{status}: {reason.strip()}")
        return self._get(task_id)

    def escalate(self, task_id: int, actor: str, reason: str) -> dict:
        """Hand a decision to a person: the task needs them (see attention.py) until a person acts on it."""
        if not reason.strip():
            raise TaskError("say what the person has to decide")
        with self._tx():
            t = self._get(task_id)
            if t["status"] not in OPEN + ("parked",):
                raise TaskError(f"task {task_id} is {t['status']}: nothing left to decide")
            self._log(task_id, actor, "escalate", f"needs a person: {reason.strip()}")
        return self._get(task_id)

    def changes(self, since: int = 0, limit: int = 200) -> dict:
        """Everything that happened after log entry `since`, oldest first, with each task's status now. Keep the
        returned cursor and ask again from it: every reader catches up from the same append-only log."""
        rows = [dict(r) for r in self.db.execute(
            "SELECT l.id, l.task_id, l.at, l.actor, l.kind, l.text, t.status, t.goal FROM log l JOIN tasks t "
            "ON t.id = l.task_id WHERE l.id > ? ORDER BY l.id LIMIT ?", (since, limit))]
        return {"cursor": rows[-1]["id"] if rows else since, "changes": rows}

    # -- the task manager's inbox (inbox.py finds who is asking and where the answer goes)
    MANAGER = "manager"

    def ask(self, sender: str, actor: str, text: str, session: str | None = None, task_id: int | None = None,
            agent: str | None = None, wait_minutes: float = 0) -> dict:
        """Leave the task manager a message (a question, not a ticket). sender: the address its answer goes to (a
        session); agent: the subagent of that session that asked, if one did; wait_minutes: how long the asker
        waits for the answer itself (wait_answer), so the manager need not send it."""
        if task_id is not None:
            self._get(task_id)
        now = time.time()
        try:
            cur = self.db.execute("INSERT INTO messages(at, sender, actor, session, recipient, task_id, text, agent, "
                                  "waits_until) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                  (now, sender, actor, session, self.MANAGER, task_id, text.strip(),
                                   (agent or "").strip() or None, now + wait_minutes * 60 if wait_minutes else None))
        except sqlite3.IntegrityError as e:
            raise TaskError("a message needs text" if "CHECK" in str(e) else str(e)) from None
        return self.message(cur.lastrowid)

    def message(self, message_id: int) -> dict:
        r = self.db.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        if r is None:
            raise TaskError(f"no message {message_id}")
        return dict(r)

    def may_answer(self, actor: str) -> bool:
        """The manager role (it holds the manage tag) or a person answers the inbox."""
        if is_person(actor):
            return True
        r = self.db.execute("SELECT tags FROM agents WHERE name = ?", (actor,)).fetchone()
        return bool(r) and "manage" in json.loads(r["tags"])

    def reply(self, message_id: int, actor: str, text: str) -> tuple[dict, dict]:
        """Answer one inbox message; returns (question, reply). Once only."""
        if not self.may_answer(actor):
            raise TaskError("only the task manager (or a person) answers its inbox")
        question = self.message(message_id)
        if question["recipient"] != self.MANAGER:
            raise TaskError(f"message {message_id} is a reply, not a question to the task manager")
        with self._tx():
            try:
                cur = self.db.execute("INSERT INTO messages(at, sender, actor, session, recipient, task_id, "
                                      "reply_to, text) VALUES (?, ?, ?, NULL, ?, ?, ?, ?)",
                                      (time.time(), self.MANAGER, actor, question["sender"], question["task_id"],
                                       message_id, text.strip()))
            except sqlite3.IntegrityError as e:
                raise TaskError(str(e)) from None
            if question["task_id"] is not None:
                self._log(question["task_id"], actor, "note",
                          f"answered message #{message_id} from @{question['sender']}: {text.strip()}")
        return question, self.message(cur.lastrowid)

    def answer(self, message_id: int) -> dict | None:
        r = self.db.execute("SELECT * FROM messages WHERE reply_to = ?", (message_id,)).fetchone()
        return dict(r) if r else None

    def inbox(self, answered: bool = False) -> list[dict]:
        """The task manager's messages, oldest first: unanswered ones (default), or all with their answers."""
        rows = [dict(r) for r in self.db.execute(
            "SELECT m.*, (SELECT a.text FROM messages a WHERE a.reply_to = m.id) AS answer FROM messages m "
            "WHERE m.recipient = ? ORDER BY m.id", (self.MANAGER,))]
        return rows if answered else [m for m in rows if m["answer"] is None]

    def replies(self, recipient: str, since: float = 0) -> list[dict]:
        """Answers the task manager sent to `recipient` (a session name or a role) after `since`, with the
        question each answers."""
        return [dict(r) for r in self.db.execute(
            "SELECT a.id, a.at, a.text, a.task_id, a.reply_to, q.text AS question FROM messages a JOIN messages q "
            "ON q.id = a.reply_to WHERE a.recipient = ? AND a.at > ? ORDER BY a.id", (recipient, since))]

    def register_agent(self, name: str, tags, decides=()) -> None:
        self.db.execute("INSERT INTO agents(name, tags, decides) VALUES (?, ?, ?) ON CONFLICT(name) DO UPDATE SET "
                        "tags = excluded.tags, decides = excluded.decides",
                        (name, json.dumps(sorted(set(tags))), json.dumps(sorted(set(decides)))))

    def claim(self, agent: str, task_id: int | None = None, lease: float | None = None) -> dict | None:
        """Atomically claim task_id, or the best ready task this agent may take. None if there is none."""
        self.maintain()
        lease = LEASE_SECONDS if lease is None else lease
        r = self.db.execute("SELECT tags FROM agents WHERE name = ?", (agent,)).fetchone()
        held = set(json.loads(r["tags"])) if r else set()
        if task_id is not None:
            candidates = [self._get(task_id)]
            if candidates[0]["status"] != "ready":
                raise TaskError(f"task {task_id} is {candidates[0]['status']}, not ready")
        else:
            candidates = [_row(r) for r in self.db.execute(
                "SELECT * FROM tasks WHERE status = 'ready' ORDER BY priority DESC, id")]
            # run_if: a cheap shell check that says there is something to do (no model call otherwise). Checked
            # before taking the write lock: the check may read this store itself.
            candidates = [t for t in candidates if set(t["tags"]) <= held and self._run_if(t)]
        with self._tx():
            for t in candidates:
                if self._get(t["id"])["status"] != "ready":
                    continue  # taken meanwhile
                try:
                    self._set(t["id"], status="claimed", claim_agent=agent, lease_until=time.time() + lease)
                except TaskError:
                    if task_id is not None:
                        raise
                    continue  # dependency or resource busy: try the next one
                self._log(t["id"], agent, "claim", f"claimed by {agent}; lease {lease / 60:g} min",
                          lease_until=time.time() + lease)
                return self._get(t["id"])
        return None

    def _run_if(self, t: dict) -> bool:
        if not t.get("run_if"):
            return True
        try:
            return subprocess.run(["bash", "-c", t["run_if"]], capture_output=True, timeout=60,
                                  env=dict(os.environ, ODD1_TASKS_TASK=str(t["id"]))).returncode == 0
        except subprocess.TimeoutExpired:
            return False

    def renew(self, task_id: int, actor: str, lease: float | None = None) -> dict:
        lease = LEASE_SECONDS if lease is None else lease
        with self._tx():
            self._holder(self._get(task_id), actor)
            self._set(task_id, lease_until=time.time() + lease)
        return self._get(task_id)

    def release(self, task_id: int, actor: str, blocked: str = "") -> dict:
        """Give the task back (ready), or mark it blocked with the reason (a person approves it again)."""
        with self._tx():
            self._holder(self._get(task_id), actor)
            if blocked:
                self._set(task_id, status="blocked", claim_agent=None, lease_until=None)
                self._log(task_id, actor, "release", f"blocked: {blocked}")
            else:
                self._set(task_id, status="ready", claim_agent=None, lease_until=None)
                self._log(task_id, actor, "release", "released")
        return self._get(task_id)

    def log(self, task_id: int, actor: str, text: str, kind: str = "note", **data) -> None:
        with self._tx():
            t = self._get(task_id)
            if not is_person(actor) and ":" not in actor:  # an agent writes only to the task it holds
                self._holder(t, actor)
            self._log(task_id, actor, kind, text, **data)

    def finish(self, task_id: int, actor: str, note: str = "") -> dict:
        """The claimer says it is done; the task waits in verifying for the runner to run done_when."""
        with self._tx():
            t = self._get(task_id)
            self._holder(t, actor)
            self._set(task_id, status="verifying", lease_until=None)
            self._log(task_id, actor, "finish", note or "finished; done_when will be checked")
        return self._get(task_id)

    def verify(self, task_id: int, actor: str, cwd: str | Path | None = None) -> dict:
        """Run done_when (exit 0 = done). A failure uses one attempt: back to ready, or parked when none are left."""
        t = self._get(task_id)
        if t["status"] != "verifying":
            raise TaskError(f"task {task_id} is {t['status']}, not verifying")
        env = dict(os.environ, ODD1_TASKS_TASK=str(task_id))
        env.pop("ODD1_TASKS_AGENT", None)
        try:
            p = subprocess.run(["bash", "-c", t["done_when"]], cwd=cwd, env=env, capture_output=True, text=True,
                               timeout=CHECK_TIMEOUT)
            code, out = p.returncode, (p.stdout + p.stderr)[-2000:]
        except subprocess.TimeoutExpired:
            code, out = 124, f"done_when took over {CHECK_TIMEOUT} s"
        with self._tx():
            self._log(task_id, actor, "check", f"done_when exited {code}", exit=code, output=out)
            if code == 0:
                self._set(task_id, status="done", claim_agent=None)
                self._log(task_id, actor, "result", "done")
            else:
                attempts = t["attempts"] + 1
                status = "ready" if attempts < t["max_attempts"] else "parked"
                self._set(task_id, status=status, attempts=attempts, claim_agent=None)
                self._log(task_id, actor, "result", f"check failed ({attempts}/{t['max_attempts']}): " +
                          ("retry" if status == "ready" else "parked, retries used up"))
        return self._get(task_id)

    def runs_since(self, agent: str, since: float) -> int:
        return self.db.execute("SELECT count(*) FROM log WHERE kind = 'run' AND actor = ? AND at >= ?",
                               (agent, since)).fetchone()[0]

    def maintain(self, now: float | None = None) -> None:
        """Expired leases return their task to ready; due runs of approved recurring tasks are created ready."""
        now = time.time() if now is None else now
        with self._tx():
            for r in self.db.execute("SELECT id, claim_agent FROM tasks WHERE status = 'claimed' AND lease_until < ?",
                                     (now,)).fetchall():
                self._set(r["id"], status="ready", claim_agent=None, lease_until=None)
                self._log(r["id"], "store", "release", f"lease of {r['claim_agent']} expired; back to ready")
            for root in [_row(r) for r in self.db.execute(
                    "SELECT * FROM tasks WHERE schedule IS NOT NULL AND approved_at IS NOT NULL")]:
                series = self.db.execute("SELECT max(created_at) AS last, sum(status IN (%s)) AS open,"
                                         " sum(status = 'cancelled') AS cancelled FROM tasks"
                                         " WHERE id = ? OR parent_id = ?" % ", ".join("?" * len(OPEN)),
                                         (*OPEN, root["id"], root["id"])).fetchone()
                if series["cancelled"] or series["open"] or now < series["last"] + interval_seconds(root["schedule"]):
                    continue
                cur = self.db.execute(
                    "INSERT INTO tasks(goal, done_when, tags, priority, max_turns, max_minutes, depends_on, resources,"
                    " run_if, parent_id, created_by, created_at, status, max_attempts, updated_at)"
                    " SELECT goal, done_when, tags, priority, max_turns, max_minutes, '[]', resources, run_if, id,"
                    " 'schedule', ?, 'ready', max_attempts, ? FROM tasks WHERE id = ?", (now, now, root["id"]))
                self._log(cur.lastrowid, "schedule", "create",
                          f"run of recurring task {root['id']} (every {root['schedule']}); approved with it")
                self._log(root["id"], "schedule", "spawn", f"started run {cur.lastrowid}", run=cur.lastrowid)


class _Tx:
    """BEGIN IMMEDIATE ... COMMIT (ROLLBACK on error): one writer at a time, so claims are atomic. Nests."""

    def __init__(self, db):
        self.db, self.outer = db, not db.in_transaction

    def __enter__(self):
        if self.outer:
            self.db.execute("BEGIN IMMEDIATE")

    def __exit__(self, kind, *_):
        if self.outer:
            self.db.execute("COMMIT" if kind is None else "ROLLBACK")
