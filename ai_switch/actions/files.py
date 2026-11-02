"""A whole file that ai-switch owns, such as a Chromium managed-policy drop-in."""

from __future__ import annotations

import json
from typing import Any, Callable, Dict

from ..context import Context
from ..model import (OBS_OFF_LOCKED, OBS_ON, OBS_UNKNOWN, Action, Observation)
from . import fsutil


class OwnedJsonFile(Action):
    """Disable = write ``content`` to ``path``. Enable = remove the file.

    Browser managed policies are mandatory (users cannot override them), so a
    matching file reports 'off (locked)'.
    """

    kind = "owned-json-file"

    def __init__(self, key: str, label: str, path: str,
                 installed: Callable[[Context], bool], content: Dict[str, Any]):
        super().__init__(key, label)
        self.path = path
        self._installed = installed
        self.content = content

    def _file(self, ctx: Context):
        return ctx.path(self.path)

    def present(self, ctx: Context) -> bool:
        return self._installed(ctx)

    def read(self, ctx: Context) -> Observation:
        text = fsutil.read_text_or_none(self._file(ctx))
        if text is None:
            return Observation(OBS_ON, "no managed policy file")
        try:
            data = json.loads(text)
        except ValueError:
            return Observation(OBS_UNKNOWN, "%s is not valid JSON" % self.path)
        missing = [k for k, v in self.content.items() if data.get(k) != v]
        if missing:
            return Observation(OBS_ON, "policy file present but %d key(s) differ" % len(missing))
        return Observation(OBS_OFF_LOCKED, "managed policy in %s" % self.path)

    def describe_off(self, lock: bool) -> str:
        return "write %d AI policy key(s) to %s" % (len(self.content), self.path)

    def describe_on(self) -> str:
        return "remove %s" % self.path

    def _before(self, ctx: Context) -> Dict[str, Any]:
        return {"orig_text": fsutil.read_text_or_none(self._file(ctx)), "created_dirs": []}

    def to_off(self, ctx: Context, lock: bool) -> Dict[str, Any]:
        f = self._file(ctx)
        before = self._before(ctx)
        before["created_dirs"] = fsutil.make_dirs(f.parent)
        text = json.dumps(self.content, indent=2, sort_keys=True) + "\n"
        fsutil.write_atomic(f, text, mode=0o644)
        before["written_text"] = text
        return before

    def to_on(self, ctx: Context) -> Dict[str, Any]:
        f = self._file(ctx)
        before = self._before(ctx)
        if before["orig_text"] is not None:
            f.unlink()
        return before

    def undo(self, ctx: Context, before: Dict[str, Any]) -> str:
        f = self._file(ctx)
        if before["orig_text"] is None:
            if f.exists():
                f.unlink()
            fsutil.remove_created_dirs(before.get("created_dirs", []))
            return "removed %s" % self.path
        fsutil.write_atomic(f, before["orig_text"], mode=0o644)
        return "restored previous %s" % self.path
