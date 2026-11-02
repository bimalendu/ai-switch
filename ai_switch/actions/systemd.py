"""Mask and unmask systemd units.

Masking is a symlink /etc/systemd/system/<unit> -> /dev/null, which is exactly
what ``systemctl mask`` creates. Writing it ourselves means it also works on an
offline image (--root) where systemctl cannot run.
"""

from __future__ import annotations

import os
from typing import Any, Dict

from ..context import Context
from ..model import (OBS_OFF, OBS_ON, Action, ActionError, Observation)
from . import fsutil

MASK_DIR = "/etc/systemd/system"


class SystemdMask(Action):
    kind = "systemd-mask"

    def __init__(self, unit: str):
        super().__init__("systemd:" + unit, unit)
        self.unit = unit

    def _link(self, ctx: Context):
        return ctx.path(MASK_DIR) / self.unit

    def _is_mask(self, ctx: Context) -> bool:
        link = self._link(ctx)
        return link.is_symlink() and os.readlink(str(link)) == "/dev/null"

    def present(self, ctx: Context) -> bool:
        return ctx.unit_installed(self.unit) or self._is_mask(ctx)

    def read(self, ctx: Context) -> Observation:
        if self._is_mask(ctx):
            return Observation(OBS_OFF, "masked")
        return Observation(OBS_ON, "installed, not masked")

    def describe_off(self, lock: bool) -> str:
        return "mask %s (symlink to /dev/null; stop it if running)" % self.unit

    def describe_on(self) -> str:
        return "unmask %s (does not start or enable it)" % self.unit

    def _systemctl(self, ctx: Context, *args: str) -> str:
        """Best effort on a live system; offline trees have no running systemd."""
        if not ctx.can_run_services:
            return ""
        res = ctx.run(["systemctl", *args])
        return "" if res.ok else "systemctl %s failed: %s" % (" ".join(args), res.stderr.strip())

    def to_off(self, ctx: Context, lock: bool) -> Dict[str, Any]:
        link = self._link(ctx)
        was_masked = self._is_mask(ctx)
        if not was_masked and (link.exists() or link.is_symlink()):
            raise ActionError(
                "%s already exists and is not a mask; refusing to overwrite it" % link)
        created = []
        if not was_masked:
            created = fsutil.make_dirs(link.parent)
            os.symlink("/dev/null", str(link))
        note = self._systemctl(ctx, "daemon-reload")
        stop = self._systemctl(ctx, "stop", self.unit)
        return {"was_masked": was_masked, "created_dirs": created,
                "note": "; ".join(n for n in (note, stop) if n)}

    def to_on(self, ctx: Context) -> Dict[str, Any]:
        link = self._link(ctx)
        was_masked = self._is_mask(ctx)
        if not was_masked and (link.exists() or link.is_symlink()):
            raise ActionError("%s is not a mask symlink; leaving it alone" % link)
        if was_masked:
            link.unlink()
        note = self._systemctl(ctx, "daemon-reload")
        return {"was_masked": was_masked, "created_dirs": [], "note": note}

    def undo(self, ctx: Context, before: Dict[str, Any]) -> str:
        link = self._link(ctx)
        masked_now = self._is_mask(ctx)
        if before.get("was_masked"):
            if not masked_now:
                if link.exists() or link.is_symlink():
                    raise ActionError("%s changed since ai-switch ran; not touching it" % link)
                fsutil.make_dirs(link.parent)
                os.symlink("/dev/null", str(link))
            msg = "kept %s masked (it was masked before ai-switch)" % self.unit
        else:
            if masked_now:
                link.unlink()
            elif link.exists() or link.is_symlink():
                raise ActionError("%s changed since ai-switch ran; not touching it" % link)
            fsutil.remove_created_dirs(before.get("created_dirs", []))
            msg = "unmasked %s" % self.unit
        self._systemctl(ctx, "daemon-reload")
        return msg
