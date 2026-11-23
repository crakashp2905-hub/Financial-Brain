"""Canonical hashes for text artifacts checked on Windows and Unix hosts."""
from __future__ import annotations

import hashlib


def sha256_lf_normalized(data: bytes) -> str:
    """Hash text after normalizing CRLF checkout endings to Git's canonical LF."""
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()
