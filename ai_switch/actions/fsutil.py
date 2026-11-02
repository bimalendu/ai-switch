"""Small filesystem helpers shared by the file-based actions."""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional


def make_dirs(path: Path) -> List[str]:
    """mkdir -p, returning the directories that did not exist, outermost first,
    as strings, so undo can remove exactly those (if still empty)."""
    missing: List[Path] = []
    p = path
    while not p.exists():
        missing.append(p)
        if p.parent == p:
            break
        p = p.parent
    for d in reversed(missing):
        d.mkdir(exist_ok=True)
        os.chmod(str(d), 0o755)
    return [str(d) for d in reversed(missing)]


def remove_created_dirs(dirs: List[str]) -> None:
    """Remove directories we created, innermost first, only if empty."""
    for d in reversed(dirs):
        try:
            os.rmdir(d)
        except OSError:
            break  # not empty or gone: stop climbing


def write_atomic(path: Path, text: str, mode: Optional[int] = None) -> None:
    tmp = path.with_name(path.name + ".ai-switch-tmp")
    tmp.write_text(text, encoding="utf-8")
    if mode is None:
        try:
            mode = os.stat(str(path)).st_mode & 0o7777
        except FileNotFoundError:
            mode = 0o644
    os.chmod(str(tmp), mode)
    os.replace(str(tmp), str(path))


def read_text_or_none(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
