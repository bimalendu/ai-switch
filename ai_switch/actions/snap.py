"""Disable and enable snaps. Disabling keeps the snap's data; removal does not."""

from __future__ import annotations

import glob
from typing import Any, Dict, Optional

from ..context import Context
from ..model import (OBS_OFF, OBS_ON, OBS_UNKNOWN, Action, ActionError, Observation)


def _snap_table(ctx: Context) -> Optional[Dict[str, bool]]:
    """{snap name: is_disabled} from `snap list`, or None if snapd can't be asked."""
    if "snap-table" in ctx.cache:
        return ctx.cache["snap-table"]  # type: ignore[return-value]
    table: Optional[Dict[str, bool]] = None
    if ctx.can_run_services:
        res = ctx.run(["snap", "list"])
        if res.ok:
            table = {}
            for line in res.stdout.splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 2:
                    table[parts[0]] = "disabled" in parts[-1].split(",")
    ctx.cache["snap-table"] = table
    return table


def snap_installed(ctx: Context, name: str) -> bool:
    table = _snap_table(ctx)
    if table is not None:
        return name in table
    # Offline (or snapd unavailable): look for the snap file on disk.
    return bool(glob.glob(str(ctx.path("/var/lib/snapd/snaps/%s_*.snap" % name))))


class SnapDisable(Action):
    kind = "snap-disable"

    def __init__(self, name: str):
        super().__init__("snap:" + name, "snap " + name)
        self.name = name

    def present(self, ctx: Context) -> bool:
        return snap_installed(ctx, self.name)

    def _disabled(self, ctx: Context) -> Optional[bool]:
        table = _snap_table(ctx)
        return None if table is None else table.get(self.name)

    def read(self, ctx: Context) -> Observation:
        d = self._disabled(ctx)
        if d is None:
            return Observation(OBS_UNKNOWN, "cannot query snapd (offline root or snapd not running)")
        return Observation(OBS_OFF, "disabled") if d else Observation(OBS_ON, "enabled")

    def describe_off(self, lock: bool) -> str:
        return "snap disable %s (keeps its data)" % self.name

    def describe_on(self) -> str:
        return "snap enable %s" % self.name

    def _run(self, ctx: Context, verb: str) -> None:
        if not ctx.can_run_services:
            raise ActionError("snap %s needs a running system; not possible with --root" % verb)
        res = ctx.run(["snap", verb, self.name])
        if not res.ok:
            raise ActionError("snap %s %s failed: %s" % (verb, self.name, res.stderr.strip()))
        ctx.cache.pop("snap-table", None)

    def to_off(self, ctx: Context, lock: bool) -> Dict[str, Any]:
        was = self._disabled(ctx)
        if was is None:
            raise ActionError("cannot query snapd; not changing snap %s" % self.name)
        if not was:
            self._run(ctx, "disable")
        return {"was_disabled": was}

    def to_on(self, ctx: Context) -> Dict[str, Any]:
        was = self._disabled(ctx)
        if was is None:
            raise ActionError("cannot query snapd; not changing snap %s" % self.name)
        if was:
            self._run(ctx, "enable")
        return {"was_disabled": was}

    def undo(self, ctx: Context, before: Dict[str, Any]) -> str:
        now = self._disabled(ctx)
        if now is None:
            raise ActionError("cannot query snapd; leaving snap %s as it is" % self.name)
        want = bool(before.get("was_disabled"))
        if now != want:
            self._run(ctx, "disable" if want else "enable")
        return "snap %s is %s again" % (self.name, "disabled" if want else "enabled")


class SnapRemove(Action):
    """Used only by --purge. Not reversible."""

    kind = "snap-remove"
    irreversible = True

    def __init__(self, name: str):
        super().__init__("snap-remove:" + name, "remove snap " + name)
        self.name = name

    def present(self, ctx: Context) -> bool:
        return snap_installed(ctx, self.name)

    def read(self, ctx: Context) -> Observation:
        return Observation(OBS_ON, "installed")

    def describe_off(self, lock: bool) -> str:
        return "snap remove %s  (IRREVERSIBLE; snapd keeps an automatic snapshot of its data)" % self.name

    def describe_on(self) -> str:
        return "(not possible) reinstall with: snap install %s" % self.name

    def to_off(self, ctx: Context, lock: bool) -> Dict[str, Any]:
        if not ctx.can_run_services:
            raise ActionError("snap remove needs a running system; not possible with --root")
        res = ctx.run(["snap", "remove", self.name])
        if not res.ok:
            raise ActionError("snap remove %s failed: %s" % (self.name, res.stderr.strip()))
        ctx.cache.pop("snap-table", None)
        return {"reinstall": "snap install " + self.name}

    def to_on(self, ctx: Context) -> Dict[str, Any]:
        raise ActionError("removed; reinstall with: snap install " + self.name)

    def undo(self, ctx: Context, before: Dict[str, Any]) -> str:
        raise ActionError("cannot undo removal; reinstall with: %s" % before.get("reinstall", "snap install " + self.name))
