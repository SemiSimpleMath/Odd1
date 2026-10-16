"""The person's own settings: who they are and how agents should address them. Two layers: what the system was
installed with (/etc/odd1/person.json, from `odd1.person` in Nix) and the person's own changes
(~/.config/odd1/person.json), which win. Every Claude Code session reads `context()` when it starts."""
import json
import os
import subprocess

# (key, label, hint) in the order the settings window shows them.
FIELDS = (
    ("name", "Full name", "Your name as it goes on your work (commits, documents)"),
    ("call_me", "What agents call you", "Leave empty for your first name"),
    ("pronouns", "Pronouns", "Leave empty and agents use they/them"),
    ("language", "Language", "The language agents answer in; empty: the one you write in"),
    ("email", "Email", "For your commits and anything sent in your name"),
    ("about", "Anything agents should know about you", "Your work, what you like, how you like answers"),
)
KEYS = tuple(key for key, _, _ in FIELDS)
SYSTEM = "/etc/odd1/person.json"


def user_path():
    return os.environ.get("ODD1_PERSON") or os.path.join(
        os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "odd1", "person.json")


def _read(path):
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return {k: str(v) for k, v in data.items() if k in KEYS and v is not None} if isinstance(data, dict) else {}


def load(system=None, user=None):
    """The settings: the installed ones, overridden by the person's own (an empty value clears an installed one)."""
    profile = {key: "" for key in KEYS}
    profile.update(_read(system or SYSTEM))
    profile.update(_read(user or user_path()))
    return {k: v.strip() for k, v in profile.items()}


def save(changes, system=None, user=None):
    """Store the person's changes (only what differs from the installed settings) and return the new settings."""
    path = user or user_path()
    installed = load(system, os.devnull)
    current = {**_read(path), **{k: str(v).strip() for k, v in changes.items() if k in KEYS}}
    own = {k: v for k, v in current.items() if v != installed.get(k, "")}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(own, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)
    return load(system, path)


def call_me(profile):
    return profile.get("call_me") or (profile.get("name") or "").split(" ")[0]


def context(profile):
    """What a Claude Code session is told about the person when it starts."""
    name, short = profile.get("name"), call_me(profile)
    if not name and not short:
        return ("The person using this computer has not told Odd1 their name yet (Menu > About you, or "
                "`odd1-person set name ...`).")
    lines = ["About the person using this computer (their own settings: Menu > About you; `odd1-person`):"]
    if name and short and short != name:
        lines.append(f"- Name: {name}. Call them {short}.")
    else:
        lines.append(f"- Name: {name or short}.")
    lines.append(f"- Pronouns: {profile['pronouns']}." if profile.get("pronouns")
                 else "- Pronouns: not given; use they/them.")
    if profile.get("language"):
        lines.append(f"- Answer in {profile['language']}.")
    if profile.get("email"):
        lines.append(f"- Email: {profile['email']}.")
    if profile.get("about"):
        lines.append("- In their words: " + " ".join(profile["about"].split()))
    return "\n".join(lines)


def sync_git(profile, run=subprocess.run):
    """Commits made here carry the person's name and email (the user's git config wins over the system's)."""
    for key, field in (("user.name", "name"), ("user.email", "email")):
        if profile.get(field):
            run(["git", "config", "--global", key, profile[field]], check=False)
