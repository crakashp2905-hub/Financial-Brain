"""Architecture guards over the kernel package (FB-0051, FB-0057, FB-0078, FB-0166).

* Kernel modules import only the standard library and other kernel modules (FB-0051 dependency rule).
* Kernel modules import no network libraries (FB-0078 no network code).
* Kernel modules read no wall clock; time enters as explicit arguments (FB-0057, FB-0166).
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

KERNEL = Path(__file__).resolve().parents[1] / "src" / "financial_brain" / "kernel"
NETWORK = {"socket", "urllib", "http", "requests", "httpx", "ssl", "ftplib", "smtplib", "aiohttp"}
WALL_CLOCK_CALLS = {("datetime", "now"), ("datetime", "utcnow"), ("time", "time"), ("date", "today")}
STDLIB = set(sys.stdlib_module_names)


def _modules() -> list[Path]:
    return sorted(p for p in KERNEL.glob("*.py"))


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def test_kernel_imports_only_stdlib_and_kernel() -> None:
    offenders = {}
    for path in _modules():
        bad = {n for n in _imports(path) if n not in STDLIB and n != "financial_brain"}
        if bad:
            offenders[path.name] = sorted(bad)
    assert offenders == {}


def test_kernel_has_no_network_imports() -> None:
    offenders = {p.name: sorted(_imports(p) & NETWORK) for p in _modules() if _imports(p) & NETWORK}
    assert offenders == {}


def test_kernel_reads_no_wall_clock() -> None:
    offenders: dict[str, list[str]] = {}
    for path in _modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        hits = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                owner = node.func.value
                if isinstance(owner, ast.Name) and (owner.id, node.func.attr) in WALL_CLOCK_CALLS:
                    hits.append(f"{owner.id}.{node.func.attr}")
        if hits:
            offenders[path.name] = hits
    assert offenders == {}
