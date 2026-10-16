"""Pods: Odd1's unit of content, passed between agents and tools by reference (borrowed from Emi's data pods).

A pod is one thing (a note, a file, an image, a chat, a skill, a report, a secret) with a reference,
`pod:<kind>:<id>`, that agents pass around instead of the content. Every pod has an authority level, and every read
names the projection it wants (`body`, or a secret's `last4`, `length`); a read below the projection's level is
refused exactly like a missing pod, and every read, write and refusal is audited (never the value).

Levels: 10 public, 50 agent (every agent and chat), 99 person (at a terminal), 100 courier (code in courier.py that
puts a secret where its pod allows, never returning it). Secret kinds are level 100: no agent reads them; agents
hand them to the courier. Kinds are registered in kinds.json; an unknown kind is refused.

Storage: /var/lib/odd1/pods/pods.db (headers, bodies, projections below 100, the audit), file bytes in blobs/ by
SHA-256, and secrets' full values apart in /var/lib/odd1/secrets/pods.db (mode 0600, left out of plain backups). All
of it is readable by the agent user with sudo: this keeps secrets out of logs, transcripts, commits and agents'
context by accident, not from an agent that sets out to read the file.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import time
from pathlib import Path

PUBLIC, AGENT, GATED, PERSON, COURIER = 10, 50, 70, 99, 100
URI = re.compile(r"^pod:([a-z][a-z0-9.\-]{0,40}):([A-Za-z0-9_\-]{6,64})$")
KINDS = Path(os.environ.get("ODD1_PODS_KINDS", Path(__file__).resolve().parent.parent / "kinds.json"))
STATE = Path(os.environ.get("ODD1_PODS_STATE", "/var/lib/odd1/pods"))
SECRETS = Path(os.environ.get("ODD1_PODS_SECRETS", "/var/lib/odd1/secrets"))
DENIED = "no such pod, or not at your authority"  # the same answer for both: nobody learns what exists

SCHEMA = """
CREATE TABLE IF NOT EXISTS pods(
  uri TEXT PRIMARY KEY, kind TEXT NOT NULL, one_liner TEXT NOT NULL DEFAULT '', tags TEXT NOT NULL DEFAULT '[]',
  body TEXT NOT NULL DEFAULT '', blob TEXT, authority INTEGER NOT NULL, created_by TEXT NOT NULL,
  created_at REAL NOT NULL, sources TEXT NOT NULL DEFAULT '[]', allow TEXT NOT NULL DEFAULT '[]',
  meta TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS projections(
  uri TEXT NOT NULL REFERENCES pods(uri), name TEXT NOT NULL, authority INTEGER NOT NULL, value TEXT NOT NULL,
  PRIMARY KEY (uri, name));
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY, at REAL NOT NULL, actor TEXT NOT NULL, uri TEXT, projection TEXT, op TEXT NOT NULL,
  allowed INTEGER NOT NULL, detail TEXT NOT NULL DEFAULT '');
CREATE TRIGGER IF NOT EXISTS audit_append_only BEFORE UPDATE ON audit BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit BEGIN SELECT RAISE(ABORT, 'append-only'); END;
"""


class PodError(Exception):
    pass


def actor() -> str:
    agent = os.environ.get("ODD1_TASKS_AGENT", "").strip()
    return agent or "person:" + (os.environ.get("USER") or "unknown")


class Pods:
    def __init__(self, state: Path | None = None, secrets_dir: Path | None = None, kinds: Path | None = None):
        self.state, self.secrets_dir = Path(state or STATE), Path(secrets_dir or SECRETS)
        self.kinds = json.loads(Path(kinds or KINDS).read_text())
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "blobs").mkdir(exist_ok=True)
        self.db = sqlite3.connect(self.state / "pods.db", timeout=30, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.secrets_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.secrets_dir, 0o700)
        path = self.secrets_dir / "pods.db"
        self.vault = sqlite3.connect(path, timeout=30, isolation_level=None)
        os.chmod(path, 0o600)
        self.vault.execute("CREATE TABLE IF NOT EXISTS values_(uri TEXT PRIMARY KEY, value TEXT NOT NULL)")

    # -- kinds and references ---------------------------------------------------------------------------------------
    def kind(self, name: str) -> dict:
        if name not in self.kinds:
            raise PodError(f"unknown kind {name!r}: register it in kinds.json ({', '.join(sorted(self.kinds))})")
        return self.kinds[name]

    @staticmethod
    def parse(uri: str) -> tuple[str, str]:
        m = URI.match(uri or "")
        if not m:
            raise PodError(f"not a pod reference: {uri!r} (pod:<kind>:<id>)")
        return m.group(1), m.group(2)

    def _audit(self, who: str, uri: str | None, projection: str | None, op: str, allowed: bool, detail: str = ""):
        self.db.execute("INSERT INTO audit(at, actor, uri, projection, op, allowed, detail) VALUES (?,?,?,?,?,?,?)",
                        (time.time(), who, uri, projection, op, int(allowed), detail))

    # -- writing ------------------------------------------------------------------------------------------------------
    def put(self, kind: str, body: str = "", *, one_liner: str = "", tags=(), file: str | Path | None = None,
            sources=(), meta: dict | None = None, who: str | None = None) -> str:
        """A pod of a registered, non-secret kind; its id comes from its content, so the same content is one pod."""
        spec, who = self.kind(kind), who or actor()
        if spec.get("secret"):
            raise PodError(f"{kind} is a secret kind: a person stores it (odd1-secrets set)")
        for s in sources:
            self.parse(s)
        digest = hashlib.sha256(f"{kind}\0{body}".encode())
        blob = None
        if file is not None:
            data = Path(file).read_bytes()
            blob = hashlib.sha256(data).hexdigest()
            target = self.state / "blobs" / blob
            if not target.exists():
                shutil.copyfile(file, target)
            digest.update(blob.encode())
        uri = f"pod:{kind}:{digest.hexdigest()[:16]}"
        self.db.execute("INSERT OR IGNORE INTO pods(uri, kind, one_liner, tags, body, blob, authority, created_by,"
                        " created_at, sources, meta) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (uri, kind, one_liner or body[:80], json.dumps(sorted(set(tags))), body, blob,
                         spec.get("authority", AGENT), who, time.time(), json.dumps(list(sources)),
                         json.dumps(meta or {})))
        self._audit(who, uri, None, "put", True)
        return uri

    def put_secret(self, kind: str, value: str, *, name: str, allow=(), who: str | None = None) -> str:
        """A person stores a secret: agents get its reference and its last4/length, never the value; the courier puts
        it only where `allow` says (host:api.example.com, env:VAR, field:card-number)."""
        spec, who = self.kind(kind), who or actor()
        if not spec.get("secret"):
            raise PodError(f"{kind} is not a secret kind")
        if not who.startswith("person:"):
            self._audit(who, None, None, "put-secret", False, "agents do not store secrets")
            raise PodError("only a person stores a secret")
        if not value:
            raise PodError("an empty secret")
        uri = f"pod:{kind}:{secrets.token_hex(8)}"
        self.db.execute("INSERT INTO pods(uri, kind, one_liner, authority, created_by, created_at, allow, meta)"
                        " VALUES (?,?,?,?,?,?,?,?)", (uri, kind, name, COURIER, who, time.time(),
                                                      json.dumps(sorted(set(allow))), json.dumps({"name": name})))
        self.vault.execute("INSERT INTO values_(uri, value) VALUES (?, ?)", (uri, value))
        self.db.execute("INSERT INTO projections(uri, name, authority, value) VALUES (?, 'length', ?, ?)",
                        (uri, AGENT, str(len(value))))
        if len(value) >= 8:
            self.db.execute("INSERT INTO projections(uri, name, authority, value) VALUES (?, 'last4', ?, ?)",
                            (uri, AGENT, value[-4:]))
        self._audit(who, uri, "full", "put-secret", True)
        return uri

    def allow(self, uri: str, destinations, who: str | None = None) -> dict:
        """A person changes where a secret may go."""
        who = who or actor()
        if not who.startswith("person:"):
            raise PodError("only a person says where a secret may go")
        self._row(uri)
        self.db.execute("UPDATE pods SET allow = ? WHERE uri = ?", (json.dumps(sorted(set(destinations))), uri))
        self._audit(who, uri, None, "allow", True, " ".join(sorted(set(destinations))))
        return self.header(uri)

    # -- reading ------------------------------------------------------------------------------------------------------
    def _row(self, uri: str) -> sqlite3.Row:
        self.parse(uri)
        row = self.db.execute("SELECT * FROM pods WHERE uri = ?", (uri,)).fetchone()
        if row is None:
            raise PodError(DENIED)
        return row

    def header(self, uri: str) -> dict:
        """What anyone may know about a pod without its content."""
        r = self._row(uri)
        names = [p["name"] for p in self.db.execute("SELECT name FROM projections WHERE uri = ?", (uri,))]
        return {"uri": uri, "kind": r["kind"], "one_liner": r["one_liner"], "tags": json.loads(r["tags"]),
                "authority": r["authority"], "created_by": r["created_by"], "created_at": r["created_at"],
                "sources": json.loads(r["sources"]), "allow": json.loads(r["allow"]), "has_file": bool(r["blob"]),
                "projections": ["body"] * (r["authority"] < COURIER) + names}

    def get(self, uri: str, projection: str = "body", authority: int = AGENT, who: str | None = None) -> str:
        """A projection's value, if the caller's authority reaches it. Agents read at 50; nothing returns a value at
        100 (the courier resolves those itself)."""
        who = who or actor()
        try:
            r = self._row(uri)
            if projection == "body":
                need, value = r["authority"], r["body"]
            else:
                p = self.db.execute("SELECT authority, value FROM projections WHERE uri = ? AND name = ?",
                                    (uri, projection)).fetchone()
                if p is None:
                    raise PodError(DENIED)
                need, value = p["authority"], p["value"]
            if authority < need or need >= COURIER:
                raise PodError(DENIED)
        except PodError:
            self._audit(who, uri, projection, "get", False)
            raise
        self._audit(who, uri, projection, "get", True)
        return value

    def file(self, uri: str, authority: int = AGENT, who: str | None = None) -> Path:
        """The path of a pod's bytes (images, audio, files), at the same authority as its body."""
        self.get(uri, "body", authority, who)
        blob = self._row(uri)["blob"]
        if not blob or not re.fullmatch(r"[0-9a-f]{64}", blob):
            raise PodError(f"{uri} has no file")
        return self.state / "blobs" / blob

    def find(self, kind: str | None = None, tag: str | None = None, text: str | None = None,
             limit: int = 50) -> list[dict]:
        sql, args = "SELECT uri FROM pods WHERE 1=1", []
        if kind:
            sql, args = sql + " AND kind = ?", args + [kind]
        if tag:
            sql, args = sql + " AND EXISTS (SELECT 1 FROM json_each(tags) WHERE value = ?)", args + [tag]
        if text:
            sql += " AND (one_liner LIKE ? OR (authority < 100 AND body LIKE ?))"
            args += [f"%{text}%"] * 2
        rows = self.db.execute(sql + " ORDER BY created_at DESC LIMIT ?", (*args, limit)).fetchall()
        return [self.header(r["uri"]) for r in rows]

    def audit(self, uri: str | None = None, limit: int = 50) -> list[dict]:
        sql = "SELECT * FROM audit" + (" WHERE uri = ?" if uri else "") + " ORDER BY id DESC LIMIT ?"
        return [dict(r) for r in self.db.execute(sql, ((uri, limit) if uri else (limit,)))]

    # -- the courier's own access (courier.py only) ------------------------------------------------------------------
    def _courier_value(self, uri: str, destination: str, who: str) -> str:
        """A secret's value for one destination its pod allows; audited. Never returned to a caller."""
        r = self._row(uri)
        allowed = destination in json.loads(r["allow"]) or any(
            a.endswith("*") and destination.startswith(a[:-1]) for a in json.loads(r["allow"]))
        if not allowed:
            self._audit(who, uri, "full", f"courier:{destination}", False, "destination not allowed")
            raise PodError(f"{uri} may not go to {destination} (it may go to: "
                           f"{', '.join(json.loads(r['allow'])) or 'nowhere yet'}); a person allows more with "
                           f"odd1-secrets allow")
        if r["authority"] < COURIER:
            return r["body"]
        row = self.vault.execute("SELECT value FROM values_ WHERE uri = ?", (uri,)).fetchone()
        if row is None:
            raise PodError(DENIED)
        self._audit(who, uri, "full", f"courier:{destination}", True)
        return row[0]
