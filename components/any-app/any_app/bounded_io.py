#!/usr/bin/env python3
"""Small helpers for reading untrusted files under a strict byte budget."""
from pathlib import Path
import hashlib


class LimitedReader:
    """Wrap a binary stream and reject reads exceeding a cumulative byte limit."""
    def __init__(self, stream, max_bytes, description="file"):
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 0:
            raise ValueError("max_bytes must be a non-negative integer")
        self.stream = stream
        self.max_bytes = max_bytes
        self.description = description
        self.consumed = 0

    def read(self, size=-1):
        if size is None or size < 0:
            raise ValueError("unbounded reads are not supported")
        remaining = self.max_bytes - self.consumed
        data = self.stream.read(min(size, remaining + 1))
        self.consumed += len(data)
        if self.consumed > self.max_bytes:
            raise ValueError(f"{self.description} exceeds {self.max_bytes} bytes")
        return data


def sha256_file(path, max_bytes, description="file"):
    """Hash a file in chunks while enforcing a limit on bytes actually read.

    Returns (hex digest, bytes consumed); a stale stat cannot bypass the cap.
    """
    digest = hashlib.sha256()
    with Path(path).open("rb") as raw:
        stream = LimitedReader(raw, max_bytes, description)
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                return digest.hexdigest(), stream.consumed
            digest.update(chunk)


def read_bytes(path, max_bytes, description="file"):
    """Read at most max_bytes plus one sentinel byte, rejecting oversized files.

    A prior stat() is not a bound: the file can grow between the check and read.
    This helper enforces the limit on the actual bytes consumed.
    """
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 0:
        raise ValueError("max_bytes must be a non-negative integer")
    with Path(path).open("rb") as stream:
        data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError(f"{description} exceeds {max_bytes} bytes")
    return data


def read_text(path, max_bytes, description="file"):
    """Read bounded bytes then decode strict UTF-8."""
    return read_bytes(path, max_bytes, description).decode("utf-8")
