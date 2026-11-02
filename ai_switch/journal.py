"""The journal: what ai-switch changed, and the state it found before.

One entry per (module, action key). The ``before`` stored is the state at the
FIRST change, so ``revert`` always returns to what the system looked like
before ai-switch touched it, no matter how many times you toggled in between.

Also stores the user's *desired* state per module, which ``reconcile`` uses to
re-apply choices after a package update replaces a policy file or unit.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from .actions import fsutil
from .context import Context

JOURNAL_DIR = "/var/lib/ai-switch"
JOURNAL_FILE = JOURNAL_DIR + "/state.json"
LOCK_FILE = JOURNAL_DIR + "/lock"


class JournalError(Exception):
    pass


def _empty() -> Dict[str, Any]:
    return {"version": 1, "desired": {}, "changes": []}


class Journal:
    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.path = ctx.path(JOURNAL_FILE)
        self.data: Dict[str, Any] = _empty()
        self._created: List[str] = []  # dirs this run created (outermost first)
        self.load()

    # -- persistence --------------------------------------------------------

    def load(self) -> None:
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            self.data = _empty()
            return
        try:
            data = json.loads(text)
        except ValueError as e:
            raise JournalError("%s is not valid JSON (%s); refusing to continue" % (self.path, e))
        if not isinstance(data, dict) or data.get("version") != 1:
            raise JournalError("%s has an unsupported format" % self.path)
        data.setdefault("desired", {})
        data.setdefault("changes", [])
        self.data = data

    def _ensure_dir(self) -> None:
        if not self.path.parent.exists():
            self._created.extend(fsutil.make_dirs(self.path.parent))

    def save(self) -> None:
        self._ensure_dir()
        if self._created and "created_dirs" not in self.data:
            self.data["created_dirs"] = list(self._created)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(str(tmp), str(self.path))

    @contextlib.contextmanager
    def locked(self) -> Iterator[None]:
        """Serialise concurrent ai-switch runs with an exclusive flock."""
        import fcntl

        lock = self.ctx.path(LOCK_FILE)
        self._ensure_dir()
        fd = os.open(str(lock), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            self.load()  # pick up changes made while we waited
            yield
        finally:
            os.close(fd)
            if not self.path.exists():
                # Nothing was journaled: leave no trace of the lock either.
                with contextlib.suppress(FileNotFoundError):
                    lock.unlink()
                fsutil.remove_created_dirs(self._created)

    # -- changes ------------------------------------------------------------

    def find(self, module: str, key: str) -> Optional[Dict[str, Any]]:
        for c in self.data["changes"]:
            if c["module"] == module and c["key"] == key:
                return c
        return None

    def record(self, module: str, key: str, kind: str, before: Dict[str, Any],
               irreversible: bool = False, note: str = "") -> bool:
        """Remember ``before`` unless this action was already journaled.
        Returns True if a new entry was written."""
        if self.find(module, key) is not None:
            return False
        self.data["changes"].append({
            "module": module,
            "key": key,
            "kind": kind,
            "before": before,
            "irreversible": irreversible,
            "note": note,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })
        self.save()
        return True

    def remove(self, module: str, key: str) -> None:
        self.data["changes"] = [
            c for c in self.data["changes"] if not (c["module"] == module and c["key"] == key)
        ]
        self.save()

    def entries(self, module: Optional[str] = None) -> List[Dict[str, Any]]:
        return [c for c in self.data["changes"] if module is None or c["module"] == module]

    def modules(self) -> List[str]:
        seen: List[str] = []
        for c in self.data["changes"]:
            if c["module"] not in seen:
                seen.append(c["module"])
        return seen

    # -- desired state ------------------------------------------------------

    def set_desired(self, module: str, state: str, lock: bool = False) -> None:
        self.data["desired"][module] = {"state": state, "lock": bool(lock)}
        self.save()

    def clear_desired(self, module: str) -> None:
        if self.data["desired"].pop(module, None) is not None:
            self.save()

    def desired(self) -> Dict[str, Dict[str, Any]]:
        return dict(self.data["desired"])

    def is_empty(self) -> bool:
        return not self.data["changes"] and not self.data["desired"]

    def delete_if_empty(self) -> None:
        """Leave no trace once everything is reverted."""
        if not self.is_empty():
            return
        for p in (self.path, self.ctx.path(LOCK_FILE)):
            with contextlib.suppress(FileNotFoundError):
                p.unlink()
        with contextlib.suppress(OSError):
            self.path.parent.rmdir()
        fsutil.remove_created_dirs(self.data.get("created_dirs", [])[:-1]
                                   if self.data.get("created_dirs") else [])
