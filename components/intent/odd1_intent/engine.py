"""Deterministic, preview-first file intent engine. No model/network calls."""
from __future__ import annotations
import ctypes, errno, fcntl, hashlib, json, os, re, shutil, stat, tempfile, uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# Private per-user state; on Odd1 it is /var/lib/odd1/intent (set by module.nix).
ROOT = Path(os.environ.get("ODD1_INTENT_STATE") or Path.home() / ".local/share/odd1-intent")
TXROOT = ROOT / "transactions"
LOG = ROOT / "history.json"

@dataclass
class Action:
    kind: str
    source: str
    target: str
    reason: str
    selected: bool = False
    source_hash: str = ""
    target_hash: str = ""
    source_parent_id: str = ""
    target_parent_id: str = ""

@dataclass
class Plan:
    intent: str
    mode: str
    actions: list[Action]

class IntentError(ValueError): pass


class CommitUncertainError(OSError):
    """History name is published, but post-publication durability is uncertain."""

def _fsync_file(path: Path) -> None:
    """Flush file contents/metadata before publishing a name in a transaction."""
    fd=os.open(path,os.O_RDONLY|getattr(os,"O_CLOEXEC",0)|getattr(os,"O_NOFOLLOW",0))
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise IntentError(f"Refusing to fsync a non-regular file: {path}")
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_dir(path: Path) -> None:
    """Persist directory-entry changes on filesystems honoring fsync semantics."""
    fd=os.open(path,os.O_RDONLY|getattr(os,"O_CLOEXEC",0)|getattr(os,"O_DIRECTORY",0)|getattr(os,"O_NOFOLLOW",0))
    try:
        info=os.fstat(fd)
        if not stat.S_ISDIR(info.st_mode):
            raise IntentError(f"Refusing to fsync a non-directory: {path}")
        os.fsync(fd)
    finally:
        os.close(fd)


def _failure_boundary(name: str) -> None:
    """Named, inert test seam for deterministic failures at semantic boundaries.

    Production does nothing here. Tests may patch this function to raise at a
    specific operation without relying on syscall call counts or global hooks.
    """
    return None


def _rename_exchange_at(left: str, right: str, dirfd: int) -> None:
    """Atomically exchange sibling names relative to a pinned directory fd.

    /proc/self/fd/<n> resolves to the already-open directory, preserving the
    legacy _rename_exchange seam while ensuring the kernel operation stays
    anchored to the validated directory inode.
    """
    pinned=Path('/proc/self/fd')/str(dirfd)
    _rename_exchange(pinned/left,pinned/right)


def _rename_exchange(left: Path, right: Path) -> None:
    """Atomically exchange two names on Linux; fail closed if unavailable."""
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise IntentError("Safe replacement requires renameat2 exchange support")
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    rc = renameat2(-100, os.fsencode(left), -100, os.fsencode(right), 2)  # AT_FDCWD, RENAME_EXCHANGE
    if rc != 0:
        code = ctypes.get_errno()
        if code in (errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP):
            raise IntentError("Safe replacement requires filesystem rename-exchange support")
        raise OSError(code, os.strerror(code), str(right))


def _rename_noreplace(source: Path, target: Path) -> None:
    """Move a name only if target is still absent (Linux renameat2)."""
    libc=ctypes.CDLL(None,use_errno=True); fn=getattr(libc,"renameat2",None)
    if fn is None: raise IntentError("Safe no-clobber rename requires renameat2 support")
    fn.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint]; fn.restype=ctypes.c_int
    if fn(-100,os.fsencode(source),-100,os.fsencode(target),1)!=0:
        code=ctypes.get_errno()
        if code in (errno.ENOSYS,errno.EINVAL,errno.EOPNOTSUPP): raise IntentError("Safe no-clobber rename unsupported")
        raise OSError(code,os.strerror(code),str(target))


def _rename_noreplace_in_parent(source: Path, target: Path, expected: str, label: str,
                                on_rename=None) -> None:
    """No-clobber rename sibling names and fsync their pinned, validated parent."""
    source,target=Path(source),Path(target)
    if source.parent != target.parent:
        raise IntentError("Safe no-clobber rename requires sibling paths")
    parent_fd=_open_parent_directory(target,expected,label)
    try:
        pinned=Path('/proc/self/fd')/str(parent_fd)
        # Keep the legacy syscall seam while resolving both names through the
        # already validated directory descriptor.
        # Preserve the operation seam used by race/failure tests even though
        # both names resolve through the validated pinned directory fd.
        _rename_noreplace(pinned/source.name,pinned/target.name)
        callback_error=None
        try:
            if on_rename is not None:
                on_rename()
        except Exception as error:
            callback_error=error
        try:
            os.fsync(parent_fd)
            _failure_boundary("rename_noreplace.after_parent_fsync")
        except Exception as sync_error:
            if callback_error is not None:
                raise IntentError(f"Rename bookkeeping and directory fsync failed: {callback_error}; {sync_error}") from sync_error
            raise
        if callback_error is not None:
            raise callback_error
    finally:
        os.close(parent_fd)


def _unlink_in_parent(path: Path, expected: str, label: str, on_unlink=None) -> None:
    """Unlink one leaf and sync the exact validated parent directory fd."""
    path=Path(path)
    parent_fd=_open_parent_directory(path,expected,label)
    try:
        # Resolve through the pinned descriptor while retaining Path.unlink as
        # an injectable operation seam for existing failure tests.
        (Path('/proc/self/fd')/str(parent_fd)/path.name).unlink()
        if on_unlink is not None:
            on_unlink()
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def _claim_if_unchanged(path: Path, expected_hash: str, prefix: str, on_claim=None,
                        expected_parent: str = "") -> Path:
    """Move a path to a unique sibling without clobbering, then validate contents.

    ``on_claim`` records the mutation before directory sync or later validation;
    callers retain the claimed name on incomplete recovery.
    """
    claimed=path.with_name(prefix+uuid.uuid4().hex)
    try:
        _rename_noreplace_in_parent(
            path,claimed,expected_parent,"Destination",
            on_rename=(lambda: on_claim(claimed)) if on_claim is not None else None)
    except Exception as error:
        raise IntentError(f"Removal claim interrupted; recovery artifact retained at {claimed}: {error}") from error
    if claimed.is_symlink() or not claimed.is_file() or digest(claimed)!=expected_hash or path.exists() or path.is_symlink():
        try: _rename_noreplace_in_parent(claimed,path,expected_parent,"Destination")
        except OSError: pass
        raise IntentError(f"Target changed before removal; claimed path retained at {claimed}")
    return claimed


def _remove_if_unchanged(path: Path, expected_hash: str, prefix: str, boundary_prefix: str | None = None,
                         expected_parent: str = "") -> None:
    def after_claim(claimed: Path) -> None:
        if boundary_prefix:
            _failure_boundary(f"{boundary_prefix}.after_claim")
    claimed=_claim_if_unchanged(path,expected_hash,prefix,on_claim=after_claim if boundary_prefix else None,
                                expected_parent=expected_parent)
    try:
        if boundary_prefix:
            _failure_boundary(f"{boundary_prefix}.before_unlink")
        def after_unlink():
            if boundary_prefix:
                _failure_boundary(f"{boundary_prefix}.after_unlink")
                _failure_boundary(f"{boundary_prefix}.before_sync")
        _unlink_in_parent(
            claimed,expected_parent,"Destination",
            on_unlink=after_unlink if boundary_prefix else None)
    except Exception as error:
        # Distinguish a retained claim from failure after its unlink: never tell
        # the operator that recovery bytes exist when the namespace change ran.
        if claimed.exists() or claimed.is_symlink():
            detail=f"Claimed file could not be removed; recovery artifact retained at {claimed}: {error}"
        else:
            detail=f"Claimed file was removed but directory sync/cleanup failed; inspect {path.parent} before retrying (claim {claimed}): {error}"
        raise IntentError(detail) from error


def digest(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda:f.read(1024*1024), b""): h.update(b)
    return h.hexdigest()

def safe_dir(path: str) -> Path:
    p=Path(path).expanduser().resolve(strict=True)
    if not p.is_dir(): raise IntentError(f"Not a directory: {p}")
    return p


def _directory_identity(path: Path) -> str:
    """Return a local identity for a directory, refusing symlink/non-directory parents."""
    try:
        info=os.stat(path, follow_symlinks=False)
    except OSError as error:
        raise IntentError(f"Folder changed since preview: {path}: {error}") from error
    if not stat.S_ISDIR(info.st_mode):
        raise IntentError(f"Folder changed since preview (not a directory): {path}")
    return f"{info.st_dev}:{info.st_ino}"


def _bind_parent_identities(actions: list[Action]) -> None:
    for action in actions:
        action.source_parent_id=_directory_identity(Path(action.source).parent)
        action.target_parent_id=_directory_identity(Path(action.target).parent)


def _open_parent_directory(path: Path, expected: str, label: str) -> int:
    """Open and pin the approved parent directory against textual-path swaps."""
    flags=os.O_RDONLY | getattr(os,"O_DIRECTORY",0) | getattr(os,"O_NOFOLLOW",0)
    try:
        fd=os.open(path.parent,flags)
        info=os.fstat(fd)
    except OSError as error:
        raise IntentError(f"{label} folder changed since preview: {path.parent}: {error}") from error
    actual=f"{info.st_dev}:{info.st_ino}"
    if not stat.S_ISDIR(info.st_mode) or (expected and actual!=expected):
        os.close(fd)
        raise IntentError(f"{label} folder changed since preview: {path.parent}")
    return fd


def _rename_exchange_in_parent(left: Path, right: Path, expected: str, label: str,
                               on_exchange=None) -> None:
    """Exchange sibling names and sync the exact pinned parent directory.

    ``on_exchange`` records the namespace mutation immediately, before fsync
    can fail, so caller recovery retains the exchanged recovery artifact. The
    same validated directory fd anchors both renameat2 and fsync; a textual
    parent replacement cannot redirect the sync to an unrelated directory.
    """
    left, right = Path(left), Path(right)
    if left.parent != right.parent:
        raise IntentError("Safe exchange requires sibling paths")
    parent_fd = _open_parent_directory(right, expected, label)
    try:
        _rename_exchange_at(left.name, right.name, parent_fd)
    except Exception:
        os.close(parent_fd)
        raise
    # Namespace mutation happened: run the bookkeeping callback even when
    # fsync fails so callers preserve/restore the exchanged recovery artifact.
    callback_error = None
    try:
        if on_exchange is not None:
            on_exchange()
    except Exception as error:
        callback_error = error
    try:
        os.fsync(parent_fd)
    except Exception as sync_error:
        os.close(parent_fd)
        if callback_error is not None:
            raise IntentError(f"Exchange bookkeeping and directory fsync failed: {callback_error}; {sync_error}") from sync_error
        raise
    os.close(parent_fd)
    if callback_error is not None:
        raise callback_error


def _require_parent_identity(path: Path, expected: str, label: str) -> None:
    """Fail closed if the directory named by a plan was replaced before mutation."""
    try:
        actual = _directory_identity(path.parent)
    except IntentError as error:
        raise IntentError(f"{label} folder changed since preview: {path.parent}") from error
    if expected and actual != expected:
        raise IntentError(f"{label} folder changed since preview: {path.parent}")

def parse(intent: str) -> Plan:
    raw=intent.strip()
    m=re.fullmatch(r"(?is)\s*update\s+(?:the\s+)?files\s+in\s+(.+?)\s+with\s+(?:the\s+ones\s+)?from\s+(.+?)\s*[.!]?\s*", raw)
    if m:
        dst,src=safe_dir(m.group(1).strip().strip("\"'")),safe_dir(m.group(2).strip().strip("\"'"))
        if dst==src or dst in src.parents or src in dst.parents: raise IntentError("Source and destination must be separate, non-nested folders")
        actions=[]
        for f in sorted(src.iterdir(), key=lambda p:p.name.casefold()):
            if f.is_symlink() or not f.is_file(): continue
            target=dst/f.name; sh=digest(f)
            # exists() follows symlinks and is False for dangling links. Treat any
            # named symlink as occupied before deciding that the path is new.
            if target.is_symlink(): actions.append(Action("SKIP",str(f),str(target),"Destination is not a regular file",False,sh,""))
            elif not target.exists(): actions.append(Action("ADD",str(f),str(target),"New source file; review relevance before adding",False,sh,""))
            elif not target.is_file(): actions.append(Action("SKIP",str(f),str(target),"Destination is not a regular file",False,sh,""))
            else:
                th=digest(target)
                if sh==th: actions.append(Action("SKIP",str(f),str(target),"Contents already match",False,sh,th))
                elif f.stat().st_mtime_ns>target.stat().st_mtime_ns: actions.append(Action("REPLACE",str(f),str(target),"Source is newer; existing destination will be backed up",True,sh,th))
                else: actions.append(Action("SKIP",str(f),str(target),"Source is not newer; keep destination",False,sh,th))
        _bind_parent_identities(actions)
        return Plan(raw,"sync",actions)
    m=re.fullmatch(r"(?is)\s*(?:make|create)\s+(?:a\s+)?backup\s+of\s+(.+?)\s*[.!]?\s*",raw)
    if m:
        raw_src=Path(m.group(1).strip().strip("\"'")).expanduser()
        # Check the user-named final component before resolve(): resolve follows
        # symlinks, which otherwise makes the non-symlink guard ineffective.
        if raw_src.is_symlink(): raise IntentError("Backup source must be a regular, non-symlink file")
        src=raw_src.resolve(strict=True)
        if not src.is_file(): raise IntentError("Backup source must be a regular, non-symlink file")
        target=src.with_name(src.name+".bak")
        if target.exists() or target.is_symlink(): raise IntentError(f"Backup target already exists; refusing to overwrite: {target}")
        action=Action("ADD",str(src),str(target),"Create a byte-for-byte backup",True,digest(src),"")
        _bind_parent_identities([action])
        return Plan(raw,"backup",[action])
    raise IntentError("I understand: update files in <folder> with the ones from <folder> (newer + missing files), or make a backup of <file>. Use explicit paths or set folders in the GUI.")

def _validate_history(entries):
    """Validate the history shape before any record can guide undo mutations.

    The store is private, but partial writes, manual edits, and old/corrupt data
    must not turn arbitrary JSON values into paths or transaction directory names.
    Keep this deliberately structural; it is not an authenticity check.
    """
    if not isinstance(entries, list):
        raise IntentError("History must contain a list of transactions")
    txid_pattern = re.compile(r"[0-9]{8}T[0-9]{6}-[0-9a-f]{8}\Z")
    hash_pattern = re.compile(r"[0-9a-f]{64}\Z")
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise IntentError("History contains a transaction with an invalid shape")
        txid = entry.get("id")
        if not isinstance(txid, str) or not txid_pattern.fullmatch(txid) or txid in seen:
            raise IntentError("History contains an invalid or duplicate transaction ID")
        seen.add(txid)
        try:
            timestamp = datetime.fromisoformat(entry.get("time", "").replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                raise ValueError("missing timezone")
        except (AttributeError, TypeError, ValueError) as error:
            raise IntentError(f"History transaction has invalid timestamp: {txid}") from error
        if (not isinstance(entry.get("intent"), str) or not entry["intent"].strip()
                or not isinstance(entry.get("time"), str)
                or type(entry.get("undone")) is not bool
                or not isinstance(entry.get("records"), list) or not entry["records"]):
            raise IntentError(f"History transaction has invalid fields: {txid}")
        if entry["undone"] != ("undone_time" in entry):
            raise IntentError(f"History transaction has inconsistent undo state: {txid}")
        if "undone_time" in entry:
            try:
                undone_timestamp = datetime.fromisoformat(entry["undone_time"].replace("Z", "+00:00"))
                if undone_timestamp.tzinfo is None:
                    raise ValueError("missing timezone")
            except (AttributeError, TypeError, ValueError) as error:
                raise IntentError(f"History transaction has invalid undo time: {txid}") from error
        intent = entry["intent"]
        sync_match = re.fullmatch(r"(?is)\s*update\s+(?:the\s+)?files\s+in\s+(.+?)\s+with\s+(?:the\s+ones\s+)?from\s+(.+?)\s*[.!]?\s*", intent)
        backup_match = re.fullmatch(r"(?is)\s*(?:make|create)\s+(?:a\s+)?backup\s+of\s+(.+?)\s*[.!]?\s*", intent)
        if sync_match:
            expected_target_dir = Path(sync_match.group(1).strip().strip("\"'")).expanduser().resolve(strict=False)
            expected_source_dir = Path(sync_match.group(2).strip().strip("\"'")).expanduser().resolve(strict=False)
            intent_kind = "sync"
        elif backup_match:
            expected_backup_source = Path(backup_match.group(1).strip().strip("\"'")).expanduser().resolve(strict=False)
            intent_kind = "backup"
        else:
            raise IntentError(f"History transaction has unsupported intent: {txid}")
        seen_targets = set()
        for index, record in enumerate(entry["records"]):
            if not isinstance(record, dict):
                raise IntentError(f"History record has invalid shape: {txid}/{index}")
            kind = record.get("kind")
            source, target = record.get("source"), record.get("target")
            backup = record.get("backup")
            if (kind not in ("ADD", "REPLACE")
                    or not isinstance(source, str) or not Path(source).is_absolute()
                    or not isinstance(target, str) or not Path(target).is_absolute()
                    or Path(source) == Path(target)
                    or not isinstance(backup, str)
                    or not isinstance(record.get("after_hash"), str)
                    or not hash_pattern.fullmatch(record["after_hash"])):
                raise IntentError(f"History record has invalid fields: {txid}/{index}")
            if intent_kind == "sync":
                if (Path(target).parent.resolve(strict=False) != expected_target_dir
                        or Path(source).parent.resolve(strict=False) != expected_source_dir
                        or Path(target).name != Path(source).name):
                    raise IntentError(f"History record paths do not match its sync intent: {txid}/{index}")
            elif (Path(source).resolve(strict=False) != expected_backup_source
                    or Path(target) != Path(str(expected_backup_source) + ".bak")
                    or kind != "ADD"):
                raise IntentError(f"History record paths do not match its backup intent: {txid}/{index}")
            target_path = Path(target)
            target_identity = (str(target_path.parent.resolve(strict=False)), target_path.name)
            if target_identity in seen_targets:
                raise IntentError(f"History transaction contains duplicate destination paths: {txid}/{index}")
            seen_targets.add(target_identity)
            before_hash = record.get("before_hash")
            if kind == "REPLACE":
                expected_backup = str(TXROOT / txid / f"{index}.before")
                if (not isinstance(before_hash, str) or not hash_pattern.fullmatch(before_hash)
                        or backup != expected_backup):
                    raise IntentError(f"History replacement backup is invalid: {txid}/{index}")
            elif backup != "" or before_hash not in ("", None):
                raise IntentError(f"History addition has unexpected backup data: {txid}/{index}")
            parent_id = record.get("target_parent_id", "")
            if (not isinstance(parent_id, str)
                    or (parent_id and not re.fullmatch(r"[0-9]+:[0-9]+", parent_id))):
                raise IntentError(f"History destination identity is invalid: {txid}/{index}")
            if "displaced" in record:
                displaced = record["displaced"]
                staged_parent = Path(target).parent
                if (not isinstance(displaced, str) or not Path(displaced).is_absolute()
                        or Path(displaced).parent != staged_parent
                        or not Path(displaced).name.startswith(".orbit-")):
                    raise IntentError(f"History displaced path is invalid: {txid}/{index}")
    return entries


def _load():
    """Read history without following links or blocking on special files.

    History controls which paths undo may later mutate, so a symlink here must
    not silently import records from outside the private state directory. Missing
    history is the only condition interpreted as an empty log.
    """
    ROOT.mkdir(parents=True,exist_ok=True)
    flags=os.O_RDONLY|getattr(os,"O_CLOEXEC",0)|getattr(os,"O_NOFOLLOW",0)|getattr(os,"O_NONBLOCK",0)
    try:
        fd=os.open(LOG,flags)
    except FileNotFoundError:
        return []
    except OSError as error:
        if error.errno in (errno.ELOOP, errno.EMLINK):
            raise IntentError(f"History must not be a symlink; refusing: {LOG}") from error
        raise
    try:
        before=os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_uid!=os.geteuid() or before.st_nlink!=1:
            raise IntentError(f"History has unexpected type, owner, or link count: {LOG}")
        # Avoid unbounded reads if a corrupt or hostile log is presented.
        limit=64*1024*1024
        if before.st_size>limit:
            raise IntentError(f"History exceeds the {limit}-byte safety limit: {LOG}")
        chunks=[]; remaining=limit+1
        while remaining:
            block=os.read(fd,min(1024*1024,remaining))
            if not block: break
            chunks.append(block); remaining-=len(block)
        raw=b"".join(chunks)
        after=os.fstat(fd)
        if len(raw)>limit:
            raise IntentError(f"History exceeds the {limit}-byte safety limit: {LOG}")
        identity=lambda info:(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns,info.st_nlink,info.st_uid,stat.S_IFMT(info.st_mode))
        try:
            named=os.stat(LOG,follow_symlinks=False)
        except OSError as error:
            raise IntentError(f"History name changed or disappeared while being read: {LOG}") from error
        if (identity(before)!=identity(after) or len(raw)!=after.st_size
                or (named.st_dev,named.st_ino)!=(before.st_dev,before.st_ino)
                or not stat.S_ISREG(named.st_mode)):
            raise IntentError(f"History changed while being read; refusing inconsistent state: {LOG}")
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise IntentError(f"History is malformed; refusing to use it for undo: {LOG}: {error}") from error
        return _validate_history(parsed)
    finally:
        os.close(fd)

def _save(entries):
    """Atomically publish history through a unique, private same-directory file.

    A predictable ``history.tmp`` can already be a symlink (or be shared by
    concurrent writers); opening it with ``write_text`` follows the link and
    can overwrite an unrelated file. ``mkstemp`` creates an exclusive 0600
    sibling and ``replace`` publishes only that owned temporary file.
    """
    ROOT.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix=".history-",dir=ROOT)
    published=False
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as stream:
            json.dump(entries,stream,indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            _fsync_dir(ROOT)  # persist the exclusive temporary directory entry
        except OSError as error:
            raise IntentError(f"History could not be safely published; directory fsync failed: {error}") from error
        _failure_boundary("history.before_publish")
        os.replace(tmp,LOG)
        published=True
        try:
            # From this point the new history name is visible. Any failure must
            # be reported as commit-uncertain so callers never compensate files
            # against a possibly published transaction record.
            _failure_boundary("history.after_publish")
            _fsync_dir(ROOT)
        except Exception as error:
            # The new history name is already visible. Any ordinary exception
            # after publication (not just an fsync OSError) must be treated as
            # commit-uncertain: callers must never compensate files against a
            # possibly committed record. Interrupt-style BaseExceptions still
            # propagate without entering callers' Exception rollback handlers.
            raise CommitUncertainError(f"History was published but durability could not be confirmed: {error}") from error
    finally:
        # os.replace() consumes the temporary name. Do not let cleanup failures
        # after publication mask CommitUncertainError or any other primary result.
        if not published:
            try: os.unlink(tmp)
            except OSError:
                # Cleanup is secondary: preserve the failure that decided whether
                # publication committed rather than replacing it with temp residue.
                pass

def _apply_locked(plan: Plan, selected_indices=None) -> dict:
    if selected_indices is None:
        selected={i for i,a in enumerate(plan.actions) if a.selected}
    else:
        # A caller supplies the final reviewed selection. Never silently discard
        # invalid indexes (or SKIP actions), which can make the applied set differ
        # from what that caller believed it approved.
        try:
            requested=list(selected_indices)
        except TypeError as error:
            raise IntentError("Selected action indexes must be an iterable of integers") from error
        if any(type(index) is not int or index < 0 or index >= len(plan.actions) for index in requested):
            raise IntentError("Selected action index is invalid")
        if any(plan.actions[index].kind not in ("ADD", "REPLACE") for index in requested):
            raise IntentError("Cannot select an action that is not applicable")
        selected=set(requested)
    actions=[a for i,a in enumerate(plan.actions) if i in selected and a.kind in ("ADD","REPLACE")]
    if not actions: raise IntentError("No actions selected")
    for a in actions:
        s,t=Path(a.source),Path(a.target)
        # Bind the approved child names to the source/destination directories
        # that were actually previewed. Replacing a folder with a symlink or
        # another directory at the same textual path invalidates the plan.
        try:
            source_parent_id=_directory_identity(s.parent)
        except IntentError as error:
            raise IntentError(f"Source folder changed since preview: {s.parent}: {error}") from error
        try:
            target_parent_id=_directory_identity(t.parent)
        except IntentError as error:
            raise IntentError(f"Destination folder changed since preview: {t.parent}: {error}") from error
        if not a.source_parent_id: a.source_parent_id=source_parent_id
        if not a.target_parent_id: a.target_parent_id=target_parent_id
        if a.source_parent_id and source_parent_id!=a.source_parent_id:
            raise IntentError(f"Source folder changed since preview: {s.parent}")
        if a.target_parent_id and target_parent_id!=a.target_parent_id:
            raise IntentError(f"Destination folder changed since preview: {t.parent}")
        if not s.is_file() or s.is_symlink() or digest(s)!=a.source_hash: raise IntentError(f"Source changed since preview: {s}")
        if a.kind=="ADD" and (t.exists() or t.is_symlink()): raise IntentError(f"Target appeared since preview; refusing: {t}")
        if a.kind=="REPLACE" and (not t.is_file() or t.is_symlink() or digest(t)!=a.target_hash): raise IntentError(f"Target changed since preview: {t}")
    txid=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")+"-"+uuid.uuid4().hex[:8]
    TXROOT.mkdir(parents=True,exist_ok=True)
    _fsync_dir(ROOT)
    txdir=TXROOT/txid; txdir.mkdir(parents=True,mode=0o700)
    _fsync_dir(TXROOT)
    records=[]
    try:
        for n,a in enumerate(actions):
            s,t=Path(a.source),Path(a.target); backup=""
            if a.kind=="REPLACE":
                backup=str(txdir/f"{n}.before")
                shutil.copy2(t,backup)
                _fsync_file(Path(backup))
                _fsync_dir(txdir)
                # Do not perform the irreversible replacement unless the undo
                # before-image is a regular file with the exact previewed bytes.
                if Path(backup).is_symlink() or not Path(backup).is_file() or digest(Path(backup))!=a.target_hash:
                    raise IntentError(f"Transaction backup verification failed; destination not changed: {t}")
            fd,tmp=tempfile.mkstemp(prefix=".orbit-",dir=t.parent); os.close(fd)
            preserve_tmp = False
            try:
                # mkstemp is an external filesystem operation: a parent can be
                # swapped while it runs. Revalidate before copying or publishing
                # through the textual path, and again at the final mutation seam.
                _require_parent_identity(t,a.target_parent_id,"Destination")
                shutil.copy2(s,tmp)
                _fsync_file(Path(tmp))
                if digest(Path(tmp))!=a.source_hash: raise IntentError(f"Source changed while copying; not installing: {s}")
                if a.kind=="ADD":
                    # link(2) is an atomic no-clobber install on the same filesystem.
                    # Unlike replace(), it refuses files and symlinks (including
                    # dangling links) created after the earlier stale-plan check.
                    _failure_boundary("apply.add.before_link")
                    parent_fd=_open_parent_directory(t,a.target_parent_id,"Destination")
                    try:
                        try: os.link(Path(tmp).name,t.name,src_dir_fd=parent_fd,dst_dir_fd=parent_fd,follow_symlinks=False)
                        except FileExistsError: raise IntentError(f"Target appeared since preview; refusing: {t}")
                        records.append({"kind":a.kind,"source":str(s),"target":str(t),"backup":backup,"before_hash":a.target_hash,"after_hash":a.source_hash,"target_parent_id":a.target_parent_id})
                        _failure_boundary("apply.add.after_link")
                        os.fsync(parent_fd)
                        os.unlink(Path(tmp).name,dir_fd=parent_fd)
                        os.fsync(parent_fd)
                    finally:
                        os.close(parent_fd)
                else:
                    # Exchange the prepared file with the destination in one
                    # filesystem operation. If the name was swapped after our
                    # digest check, the displaced object stays in tmp and is
                    # checked before we discard or restore it.
                    # Preserve the historical generic exchange seam used by
                    # rollback tests, while exposing a distinct preflight hook
                    # immediately before opening the pinned destination parent.
                    _failure_boundary("apply.replace.before_exchange")
                    parent_fd=_open_parent_directory(t,a.target_parent_id,"Destination")
                    try:
                        _rename_exchange_at(Path(tmp).name,t.name,parent_fd)
                        os.fsync(parent_fd)
                    finally:
                        os.close(parent_fd)
                    records.append({"kind":a.kind,"source":str(s),"target":str(t),"backup":backup,"before_hash":a.target_hash,"after_hash":a.source_hash,"displaced":tmp,"target_parent_id":a.target_parent_id})
                    # The exchanged-out destination is a recovery copy until all
                    # post-exchange validation completes; never let finally erase it.
                    preserve_tmp = True
                    _failure_boundary("apply.replace.after_exchange")
                    _fsync_dir(t.parent)
                    displaced=Path(tmp)
                    if displaced.is_symlink() or not displaced.is_file() or digest(displaced)!=a.target_hash:
                        try: _rename_exchange_in_parent(displaced,t,a.target_parent_id,"Destination")
                        except Exception as restore_error:
                            # Keep the rollback record and displaced file: finally must
                            # not delete the only copy of an unexpected path.
                            preserve_tmp = True
                            raise IntentError(f"Target changed during replacement; recovery artifact retained at {displaced}: {restore_error}")
                        records.pop()
                        preserve_tmp = False
                        raise IntentError(f"Target changed during replacement; refusing: {t}")
                    _unlink_in_parent(Path(tmp),a.target_parent_id,"Destination")
                    preserve_tmp=False
            finally:
                if not preserve_tmp:
                    # Cleanup uses the approved parent directory, not its mutable
                    # textual path. If it changed, leave the recovery stage alone.
                    try:
                        parent_fd=_open_parent_directory(Path(tmp),a.target_parent_id,"Destination")
                    except IntentError as error:
                        if Path(tmp).exists() or Path(tmp).is_symlink():
                            preserve_tmp=True
                            raise IntentError(f"{error}; staged recovery file retained at {tmp}") from error
                    else:
                        try:
                            try: _unlink_in_parent(Path(tmp),a.target_parent_id,"Destination")
                            except FileNotFoundError: pass
                        finally:
                            os.close(parent_fd)
            # Include installed targets in rollback before any post-install check
            # can fail (including digest/read errors).
            got=digest(t)
            if got!=a.source_hash: raise IntentError(f"Verification failed after copy: {t}")
    except Exception:
        rollback_errors=[]
        for r in reversed(records):
            t=Path(r["target"])
            try:
                if r["backup"]:
                    displaced=Path(r.get("displaced","")) if r.get("displaced") else None
                    if displaced and displaced.exists() and t.is_file() and not t.is_symlink() and digest(t)==r["after_hash"]:
                        _rename_exchange_in_parent(displaced,t,r.get("target_parent_id", ""),"Destination")
                        _failure_boundary("apply.rollback.after_exchange")
                        if displaced.is_symlink() or not displaced.is_file() or digest(displaced)!=r["after_hash"]:
                            rollback_errors.append(f"Rollback stopped: unexpected exchanged file preserved at {displaced}")
                        elif t.is_symlink() or not t.is_file() or digest(t)!=r["before_hash"]:
                            # The exchange returned a competing path, not the
                            # original before-image. Keep transaction backup and
                            # staged bytes for manual recovery.
                            rollback_errors.append(f"Rollback restored a competing target; recovery artifacts retained: {t}, {displaced}")
                        else:
                            try:
                                _failure_boundary("apply.rollback.remove_displaced")
                            except Exception as cleanup_error:
                                rollback_errors.append(f"Rollback restored original; displaced after-image retained at {displaced}: {cleanup_error}")
                                continue
                            _unlink_in_parent(displaced,r.get("target_parent_id", ""),"Destination",
                                              on_unlink=lambda: (_failure_boundary("apply.rollback.after_remove_displaced"), _failure_boundary("apply.rollback.before_sync_displaced")))
                    elif not t.is_file() or t.is_symlink() or digest(t)!=r["after_hash"]:
                        rollback_errors.append(f"Rollback stopped: target changed: {t}"); continue
                    else:
                        rollback=t.with_name(".orbit-rollback-"+uuid.uuid4().hex)
                        shutil.copy2(r["backup"],rollback)
                        _rename_exchange_in_parent(rollback,t,r.get("target_parent_id", ""),"Destination")
                        if rollback.is_symlink() or not rollback.is_file() or digest(rollback)!=r["after_hash"]:
                            rollback_errors.append(f"Rollback stopped: exchanged file changed: {rollback}")
                        else:
                            try:
                                _failure_boundary("apply.rollback.remove_staged_after_image")
                            except Exception as cleanup_error:
                                rollback_errors.append(f"Rollback restored original; staged after-image retained at {rollback}: {cleanup_error}")
                                continue
                            _unlink_in_parent(rollback,r.get("target_parent_id", ""),"Destination",
                                              on_unlink=lambda: (_failure_boundary("apply.rollback.after_remove_staged_after_image"), _failure_boundary("apply.rollback.before_sync_staged_after_image")))
                elif t.exists() and not t.is_symlink() and digest(t)==r["after_hash"]:
                    _remove_if_unchanged(t,r["after_hash"],".orbit-remove-","apply.rollback.add",r.get("target_parent_id", ""))
            except Exception as rollback_error:
                rollback_errors.append(str(rollback_error))
        if rollback_errors:
            # Preserve transaction backups for manual recovery rather than deleting
            # the only recoverable prior contents.
            raise IntentError("Apply failed and rollback incomplete: " + "; ".join(rollback_errors))
        _failure_boundary("apply.rollback.remove_transaction")
        shutil.rmtree(txdir,ignore_errors=True)
        try: _fsync_dir(TXROOT)
        except OSError: pass  # pre-commit failure already selected rollback; residue is safe to inspect
        raise
    entry={"id":txid,"intent":plan.intent,"time":datetime.now(timezone.utc).isoformat(),"records":records,"undone":False}
    try:
        entries=_load(); entries.append(entry); _save(entries)
    except CommitUncertainError:
        # New history is visible but directory durability is unknown; preserve
        # the corresponding installed files/backups instead of compensating.
        raise IntentError("Operation may be committed, but durability could not be confirmed; inspect history and files before retrying")
    except Exception as error:
        # If durable undo metadata cannot be recorded, retain backups if any
        # compensation is incomplete; never claim a clean rollback implicitly.
        rollback_errors=[]
        for r in reversed(records):
            t=Path(r["target"])
            try:
                if not t.is_file() or t.is_symlink() or digest(t)!=r["after_hash"]:
                    rollback_errors.append(f"Rollback stopped: target changed: {t}"); continue
                if r["backup"]:
                    rollback=t.with_name(".orbit-rollback-"+uuid.uuid4().hex)
                    shutil.copy2(r["backup"],rollback)
                    _rename_exchange_in_parent(rollback,t,r.get("target_parent_id", ""),"Destination")
                    _failure_boundary("apply.history_rollback.after_exchange")
                    if rollback.is_symlink() or not rollback.is_file() or digest(rollback)!=r["after_hash"]:
                        rollback_errors.append(f"Rollback stopped: exchanged file changed: {rollback}")
                    else:
                        try:
                            _failure_boundary("apply.history_rollback.remove_staged_after_image")
                        except Exception as cleanup_error:
                            rollback_errors.append(f"History rollback restored original; staged after-image retained at {rollback}: {cleanup_error}")
                            continue
                        rollback.unlink()
                        _failure_boundary("apply.history_rollback.after_remove_staged_after_image")
                        _failure_boundary("apply.history_rollback.before_sync_staged_after_image")
                        _fsync_dir(t.parent)
                else: _remove_if_unchanged(t,r["after_hash"],".orbit-remove-","apply.history_rollback.add",r.get("target_parent_id", ""))
            except Exception as rollback_error:
                rollback_errors.append(str(rollback_error))
        if rollback_errors:
            raise IntentError("History save failed and rollback incomplete: " + "; ".join(rollback_errors))
        _failure_boundary("apply.history_rollback.remove_transaction")
        shutil.rmtree(txdir,ignore_errors=True)
        try: _fsync_dir(TXROOT)
        except OSError: pass  # rollback is complete but cleanup persistence is unconfirmed
        raise
    return entry

@contextmanager
def _engine_lock():
    """Serialize filesystem transactions and history across local API clients.

    One stable lock avoids split-lock ordering/deadlock and spans every
    transaction's read, mutation and history publication.
    """
    ROOT.mkdir(parents=True,exist_ok=True)
    lock_path=ROOT/"engine.lock"
    flags=os.O_CREAT|os.O_RDWR|getattr(os,"O_CLOEXEC",0)|getattr(os,"O_NOFOLLOW",0)
    fd=os.open(lock_path,flags,0o600)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_nlink!=1:
            raise IntentError(f"Engine lock has unexpected type/owner/link count: {lock_path}")
        os.fchmod(fd,0o600)
        fcntl.flock(fd,fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def apply(plan: Plan, selected_indices=None) -> dict:
    with _engine_lock():
        return _apply_locked(plan,selected_indices)


def _remove_transaction_dir(txdir: Path) -> None:
    """Remove only regular artifacts from the pinned transaction directory.

    Do not use Path.iterdir()/unlink(): a transaction-directory symlink would
    otherwise enumerate and delete files in its referent. Descriptor-relative
    operations keep cleanup anchored to the opened TXROOT/transaction inodes.
    """
    txdir = Path(txdir)
    if txdir.parent != TXROOT or txdir.name in ("", ".", ".."):
        raise IntentError(f"Transaction directory is outside the transaction root: {txdir}")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        root_fd = os.open(TXROOT, flags)
    except OSError as error:
        raise IntentError(f"Transaction root cannot be safely opened: {TXROOT}: {error}") from error
    tx_fd = None
    try:
        try:
            tx_fd = os.open(txdir.name, flags, dir_fd=root_fd)
        except FileNotFoundError:
            # Distinguish absence from a dangling/symlink or other unexpected entry.
            try:
                os.stat(txdir.name, dir_fd=root_fd, follow_symlinks=False)
            except FileNotFoundError:
                return
            raise IntentError(f"Unexpected transaction directory type preserved: {txdir}")
        except OSError as error:
            raise IntentError(f"Transaction directory is not a real directory; preserved: {txdir}: {error}") from error

        opened = os.fstat(tx_fd)
        # Stable order makes partial-cleanup residue predictable and inspectable.
        for name in sorted(os.listdir(tx_fd)):
            try:
                info = os.stat(name, dir_fd=tx_fd, follow_symlinks=False)
            except FileNotFoundError:
                raise IntentError(f"Transaction artifact changed during cleanup; preserved: {txdir/name}")
            if not stat.S_ISREG(info.st_mode):
                raise IntentError(f"Unexpected transaction artifact preserved: {txdir/name}")
            try:
                os.unlink(name, dir_fd=tx_fd)
            except OSError as error:
                raise IntentError(f"Transaction artifact cleanup failed; inspect {txdir/name}: {error}") from error
        os.fsync(tx_fd)
        os.close(tx_fd)
        tx_fd = None
        try:
            current = os.stat(txdir.name, dir_fd=root_fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino) or not stat.S_ISDIR(current.st_mode):
            raise IntentError(f"Transaction directory changed during cleanup; preserved: {txdir}")
        os.rmdir(txdir.name, dir_fd=root_fd)
        os.fsync(root_fd)
    finally:
        if tx_fd is not None:
            os.close(tx_fd)
        os.close(root_fd)


def _copy_history_backup(txid: str, index: int, destination: Path) -> None:
    """Copy an undo before-image without following transaction-directory links.

    Persisted history names a transaction directory and backup leaf. Open each
    directory relative to a pinned parent with O_NOFOLLOW, then open the backup
    relative to that directory. This prevents a replaced transaction directory
    or backup symlink from redirecting undo reads outside the private store.
    """
    directory_flags = (os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
                       | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
    file_flags = (os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
                  | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    root_fd = tx_fd = source_fd = output_fd = None
    created_destination = False
    completed = False
    try:
        root_fd = os.open(TXROOT, directory_flags)
        root_info = os.fstat(root_fd)
        if not stat.S_ISDIR(root_info.st_mode) or root_info.st_uid != os.geteuid():
            raise IntentError(f"Undo transaction root has unexpected type or owner: {TXROOT}")
        tx_fd = os.open(txid, directory_flags, dir_fd=root_fd)
        tx_info = os.fstat(tx_fd)
        if not stat.S_ISDIR(tx_info.st_mode) or tx_info.st_uid != os.geteuid():
            raise IntentError(f"Undo transaction directory has unexpected type or owner: {TXROOT/txid}")
        name = f"{index}.before"
        source_fd = os.open(name, file_flags, dir_fd=tx_fd)
        before = os.fstat(source_fd)
        if not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid() or before.st_nlink != 1:
            raise IntentError(f"Undo backup has unexpected type, owner, or link count: {TXROOT/txid/name}")
        output_flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL
                        | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
                        | getattr(os, "O_NONBLOCK", 0))
        output_fd = os.open(destination, output_flags, 0o600)
        created_destination = True
        output_info = os.fstat(output_fd)
        if (not stat.S_ISREG(output_info.st_mode) or output_info.st_uid != os.geteuid()
                or output_info.st_nlink != 1):
            raise IntentError(f"Undo staging path has unexpected type, owner, or link count: {destination}")
        while True:
            block = os.read(source_fd, 1024 * 1024)
            if not block:
                break
            view = memoryview(block)
            while view:
                written = os.write(output_fd, view)
                if written <= 0:
                    raise OSError("short write while staging undo backup")
                view = view[written:]
        os.fsync(output_fd)
        after = os.fstat(source_fd)
        identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
                                 info.st_ctime_ns, info.st_nlink, info.st_uid, stat.S_IFMT(info.st_mode))
        named = os.stat(name, dir_fd=tx_fd, follow_symlinks=False)
        current_tx = os.stat(txid, dir_fd=root_fd, follow_symlinks=False)
        if (identity(before) != identity(after) or not stat.S_ISREG(named.st_mode)
                or (named.st_dev, named.st_ino) != (before.st_dev, before.st_ino)
                or not stat.S_ISDIR(current_tx.st_mode)
                or (current_tx.st_dev, current_tx.st_ino) != (tx_info.st_dev, tx_info.st_ino)):
            raise IntentError(f"Undo backup changed while being read: {TXROOT/txid/name}")
        completed = True
    except OSError as error:
        if isinstance(error, FileNotFoundError):
            message = f"Undo transaction backup is missing or unsafe: {TXROOT/txid}/{index}.before"
        elif error.errno in (getattr(errno, "ELOOP", 40), getattr(errno, "ENOTDIR", 20)):
            message = f"Undo transaction path must contain real directories and files: {TXROOT/txid}/{index}.before"
        else:
            message = f"Undo transaction backup could not be safely read: {TXROOT/txid}/{index}.before: {error}"
        raise IntentError(message) from error
    finally:
        for fd in (source_fd, output_fd, tx_fd, root_fd):
            if fd is not None:
                os.close(fd)
        if created_destination and not completed:
            # Keep a partial copy from being mistaken for a valid staging file.
            try:
                destination.unlink()
            except FileNotFoundError:
                pass


def _undo_locked(txid=None) -> dict:
    entries=_load()
    entry=next((e for e in reversed(entries) if not e["undone"] and (txid is None or e["id"]==txid)),None)
    if not entry: raise IntentError("No undoable transaction found")
    for r in entry["records"]:
        t=Path(r["target"])
        # History records the approved destination parent identity when available.
        # Refuse to undo into a different directory now occupying the same path.
        expected_parent=r.get("target_parent_id")
        if expected_parent:
            try:
                actual_parent=_directory_identity(t.parent)
            except IntentError as error:
                raise IntentError(f"Undo stopped: destination folder changed since operation: {t.parent}") from error
            if actual_parent!=expected_parent:
                raise IntentError(f"Undo stopped: destination folder changed since operation: {t.parent}")
        if not t.is_file() or t.is_symlink() or digest(t)!=r["after_hash"]: raise IntentError(f"Undo stopped: target changed since operation: {t}")
    # Stage and verify all prior contents before changing the first target.
    staged=[]
    exchanged=[]; removed=[]
    try:
        for n,r in enumerate(entry["records"]):
            if not r["backup"]: continue
            t=Path(r["target"])
            tmp=t.with_name(".orbit-undo-"+uuid.uuid4().hex)
            # Track the unique staging name before copy/verification can fail;
            # a partial copy must not become an invisible orphan.
            staged.append((r,t,tmp))
            _copy_history_backup(entry["id"], n, tmp)
            _fsync_dir(t.parent)
            if digest(tmp)!=r["before_hash"]:
                tmp.unlink(); staged.pop(); raise IntentError(f"Undo backup verification failed: {t}")
        for r,t,tmp in reversed(staged):
            # Register the namespace mutation before the helper's directory
            # fsync: if syncing fails, recovery still knows the exchange happened.
            _rename_exchange_in_parent(
                tmp,t,r.get("target_parent_id", ""),"Undo destination",
                on_exchange=lambda r=r,t=t,tmp=tmp: exchanged.append((r,t,tmp)))
            if tmp.is_symlink() or not tmp.is_file() or digest(tmp)!=r["after_hash"]:
                try:
                    _rename_exchange_in_parent(tmp,t,r.get("target_parent_id", ""),"Undo destination")
                except Exception as compensation_error:
                    raise IntentError(f"Undo stopped: compensation failed; recovery artifacts retained: {t}, {tmp}: {compensation_error}") from compensation_error
                if not (t.is_file() and not t.is_symlink() and digest(t)==r["after_hash"] and tmp.is_file() and not tmp.is_symlink() and digest(tmp)==r["before_hash"]):
                    raise IntentError(f"Undo stopped: target changed during restore; compensation unverified and recovery artifacts retained: {t}, {tmp}")
                exchanged.pop()
                raise IntentError(f"Undo stopped: target changed during restore: {t}")
        for r in reversed(entry["records"]):
            if not r["backup"]:
                target=Path(r["target"])
                claimed=_claim_if_unchanged(
                    target,r["after_hash"],".orbit-undo-remove-",
                    on_claim=lambda claimed,r=r,target=target: removed.append((r,target,claimed)),
                    expected_parent=r.get("target_parent_id", ""))
    except Exception as undo_error:
        # A no-clobber claim mutates the filesystem before its validation. The
        # callback records it immediately so even a failed claim restoration is
        # reported and its only recoverable bytes are never treated as cleanup.
        rollback_errors=[]
        for r,t,claimed in reversed(removed):
            try:
                if not claimed.exists() and t.is_file() and not t.is_symlink() and digest(t)==r["after_hash"]:
                    continue  # _claim_if_unchanged already restored the original name
                if claimed.is_symlink() or not claimed.is_file() or digest(claimed)!=r["after_hash"]:
                    rollback_errors.append(f"Undo removal claim has unexpected state; inspect {t}, {claimed}")
                elif t.exists() or t.is_symlink():
                    rollback_errors.append(f"Undo removal claim retained because target is occupied: {t}, {claimed}")
                else:
                    _rename_noreplace(claimed,t)
                    _fsync_dir(t.parent)
                    if not t.is_file() or t.is_symlink() or digest(t)!=r["after_hash"] or claimed.exists():
                        rollback_errors.append(f"Undo removal claim restoration unverified; inspect {t}, {claimed}")
            except Exception as rollback_error:
                rollback_errors.append(f"Undo removal claim recovery failed; artifact retained at {claimed}: {rollback_error}")
        for r,t,tmp in reversed(exchanged):
            try:
                if t.is_file() and not t.is_symlink() and digest(t)==r["before_hash"] and tmp.is_file() and not tmp.is_symlink() and digest(tmp)==r["after_hash"]:
                    _rename_exchange_in_parent(tmp,t,r.get("target_parent_id", ""),"Undo destination")
                    if not (t.is_file() and not t.is_symlink() and digest(t)==r["after_hash"] and tmp.is_file() and not tmp.is_symlink() and digest(tmp)==r["before_hash"]):
                        rollback_errors.append(f"Undo compensation exchange unverified; recovery artifacts retained: {t}, {tmp}")
                elif t.is_file() and not t.is_symlink() and digest(t)==r["after_hash"] and tmp.is_file() and not tmp.is_symlink() and digest(tmp)==r["before_hash"]:
                    pass  # already compensated by the inner recovery path
                else:
                    rollback_errors.append(f"Undo rollback states unexpected; recovery artifacts retained: {t}, {tmp}")
            except Exception as rollback_error:
                rollback_errors.append(f"Undo rollback exchange failed; recovery artifacts retained: {t}, {tmp}: {rollback_error}")
        if rollback_errors:
            raise IntentError("Undo rollback incomplete after " + str(undo_error) + "; recovery artifacts retained: " + "; ".join(rollback_errors)) from undo_error
        for r,_,tmp in staged:
            try:
                if not tmp.exists() and not tmp.is_symlink():
                    continue
                if tmp.is_symlink() or not tmp.is_file():
                    rollback_errors.append(f"Undo staging path has unexpected type; inspect {tmp}")
                    continue
                staged_hash=digest(tmp)
                if staged_hash not in (r["before_hash"],r["after_hash"]):
                    rollback_errors.append(f"Undo staging bytes are unrecognized; recovery artifact retained at {tmp}")
                    continue
                _failure_boundary("undo.rollback.remove_stage")
                tmp.unlink()
                if tmp.exists() or tmp.is_symlink():
                    rollback_errors.append(f"Undo staging cleanup incomplete; recovery artifact retained at {tmp}")
            except Exception as cleanup_error:
                rollback_errors.append(f"Undo staging cleanup failed; recovery artifact retained at {tmp}: {cleanup_error}")
        if rollback_errors:
            raise IntentError("Undo rollback incomplete after " + str(undo_error) + "; recovery artifacts retained: " + "; ".join(rollback_errors)) from undo_error
        raise
    entry["undone"]=True; entry["undone_time"]=datetime.now(timezone.utc).isoformat()
    try: _save(entries)
    except CommitUncertainError:
        # Undo history is visible but directory durability is unknown. Files
        # are already restored; do not compensate against a possibly committed log.
        raise IntentError("Undo may be committed, but durability could not be confirmed; inspect history and files before retrying")
    except Exception as save_error:
        entry["undone"]=False; entry.pop("undone_time",None)
        rollback_errors=[]
        for r,t,claimed in reversed(removed):
            try:
                if claimed.is_symlink() or not claimed.is_file() or digest(claimed)!=r["after_hash"]:
                    rollback_errors.append(f"Undo rollback stopped: claimed file changed; recovery artifact retained at {claimed}")
                elif t.exists() or t.is_symlink():
                    rollback_errors.append(f"Undo rollback stopped: target occupied; recovery artifact retained at {claimed}: {t}")
                else:
                    _rename_noreplace_in_parent(claimed,t,r.get("target_parent_id", ""),"Destination")
                    if not t.is_file() or t.is_symlink() or digest(t)!=r["after_hash"]:
                        rollback_errors.append(f"Undo rollback restored unexpected bytes; inspect {t}")
            except Exception as rollback_error:
                rollback_errors.append(f"Undo rollback could not restore {claimed}: {rollback_error}")
        for r,t,tmp in reversed(exchanged):
            try:
                if not (t.is_file() and not t.is_symlink() and digest(t)==r["before_hash"] and tmp.is_file() and not tmp.is_symlink() and digest(tmp)==r["after_hash"]):
                    rollback_errors.append(f"Undo rollback states unexpected; recovery artifacts retained: {t}, {tmp}")
                    continue
                _rename_exchange_in_parent(tmp,t,r.get("target_parent_id", ""),"Undo destination")
                if not (t.is_file() and not t.is_symlink() and digest(t)==r["after_hash"] and tmp.is_file() and not tmp.is_symlink() and digest(tmp)==r["before_hash"]):
                    rollback_errors.append(f"Undo rollback exchange unverified; recovery artifacts retained: {t}, {tmp}")
            except Exception as rollback_error:
                rollback_errors.append(f"Undo rollback exchange failed; recovery artifacts retained: {t}, {tmp}: {rollback_error}")
        if rollback_errors:
            raise IntentError("History save failed and undo rollback incomplete; transaction backup retained: " + "; ".join(rollback_errors)) from save_error
        cleanup_errors=[]
        for _,_,tmp in staged:
            try:
                if not tmp.exists() and not tmp.is_symlink():
                    continue
                if tmp.is_symlink() or not tmp.is_file():
                    cleanup_errors.append(f"Undo rollback staging path has unexpected type; inspect {tmp}")
                    continue
                _failure_boundary("undo.history_rollback.remove_stage")
                tmp.unlink()
                if tmp.exists() or tmp.is_symlink():
                    cleanup_errors.append(f"Undo rollback staging cleanup incomplete; recovery artifact retained at {tmp}")
            except Exception as cleanup_error:
                cleanup_errors.append(f"Undo rollback staging cleanup failed; recovery artifact retained at {tmp}: {cleanup_error}")
        if cleanup_errors:
            raise IntentError("Undo history save failed; filesystem state was restored, but staging cleanup was incomplete after " + str(save_error) + "; recovery artifacts retained: " + "; ".join(cleanup_errors)) from save_error
        raise
    # At this point the restored filesystem state and undone history are committed.
    # Cleanup failures must not look like a failed/rolled-back undo: name the
    # committed outcome and any possible residue so the caller knows not to retry.
    try:
        for _,_,claimed in removed:
            _failure_boundary("undo.cleanup.remove_add_claim")
            claimed.unlink()
        for _,_,tmp in exchanged:
            _failure_boundary("undo.cleanup.remove_replace_stage")
            tmp.unlink()
        for parent in {target.parent for _,target,_ in removed + exchanged}:
            _failure_boundary("undo.cleanup.sync_destination")
            _fsync_dir(parent)
        _failure_boundary("undo.cleanup.remove_transaction")
        _remove_transaction_dir(TXROOT/entry["id"])
    except Exception as cleanup_error:
        artifacts = [str(claimed) for _,_,claimed in removed if claimed.exists()]
        artifacts += [str(tmp) for _,_,tmp in exchanged if tmp.exists()]
        txdir = TXROOT/entry["id"]
        if txdir.exists(): artifacts.append(str(txdir))
        residue = "; recovery artifacts: " + ", ".join(artifacts) if artifacts else "; inspect transaction state"
        raise IntentError("Undo completed and is recorded, but cleanup was incomplete" + residue + f": {cleanup_error}") from cleanup_error
    return entry

def undo(txid=None) -> dict:
    with _engine_lock():
        return _undo_locked(txid)


def history():
    with _engine_lock():
        return list(reversed(_load()))
