"""Read ``.env`` into the environment, without a dependency and without overwriting.

Secrets in this project come from the environment and nowhere else - ``KITE_API_KEY``,
``KITE_API_SECRET``, ``FB_LLM_ENABLED`` and the rest are read with ``os.environ.get`` at
the point of use, so nothing is ever passed around or stored in a config object. That is
the right design, and it has one practical gap: on a desktop you have to get the values
*into* the environment first, every session, in every terminal.

``.env`` closes that gap. It is gitignored, it is read at CLI start-up, and this module is
twenty lines rather than a dependency because the format needed here is trivial.

Two rules that matter more than the parsing:

* **Real environment variables win.** A value already exported takes precedence over the
  file, so a one-off ``KITE_ACCESS_TOKEN=... fb kite quote`` does what it looks like, and
  CI never picks up a developer's local file.
* **Nothing here logs a value.** Errors name the line number and the variable, never what
  it was set to. ``loaded()`` reports which names were found, not their contents.
"""
from __future__ import annotations

import os
from pathlib import Path


def find(start: Path | None = None) -> Path | None:
    """The nearest ``.env`` at or above ``start`` (default: the repository root)."""
    here = start or Path(__file__).resolve().parents[2]
    for directory in (here, *here.parents):
        candidate = directory / ".env"
        if candidate.is_file():
            return candidate
    return None


def parse(text: str) -> dict[str, str]:
    """``KEY=value`` per line. Blank lines and ``#`` comments ignored.

    Surrounding quotes are stripped, because editors add them and Kite keys never contain
    them. Everything after the first ``=`` is the value, so a secret containing ``=``
    survives intact.
    """
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            out[key] = value
    return out


def load(path: Path | None = None, *, override: bool = False) -> list[str]:
    """Put ``.env`` into ``os.environ``. Returns the names set, never the values.

    An empty value in the file is treated as absent rather than as an empty string: a
    half-filled template should read as "not configured", which is what every credential
    check in this project tests for.
    """
    env_file = path or find()
    if env_file is None:
        return []
    try:
        pairs = parse(env_file.read_text(encoding="utf-8"))
    except OSError:
        return []
    applied = []
    for key, value in pairs.items():
        if not value:
            continue
        if override or not os.environ.get(key):
            os.environ[key] = value
            applied.append(key)
    return applied


def status(path: Path | None = None) -> dict:
    """Which names the file defines and whether each has a value. No values returned."""
    env_file = path or find()
    if env_file is None:
        return {"path": None, "set": [], "blank": []}
    pairs = parse(env_file.read_text(encoding="utf-8"))
    return {"path": str(env_file),
            "set": sorted(k for k, v in pairs.items() if v),
            "blank": sorted(k for k, v in pairs.items() if not v)}
