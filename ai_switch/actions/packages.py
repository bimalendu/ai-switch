"""Package and Flatpak removal. Only used by --purge, never reversible."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..context import Context
from ..model import OBS_ON, Action, ActionError, Observation

_REMOVE = {
    "dnf": ["dnf", "-y", "remove"],
    "apt": ["apt-get", "-y", "purge"],
    "zypper": ["zypper", "--non-interactive", "remove"],
    "pacman": ["pacman", "-R", "--noconfirm"],
}
_INSTALL_HINT = {
    "dnf": "dnf install",
    "apt": "apt-get install",
    "zypper": "zypper install",
    "pacman": "pacman -S",
}


class PackageRemove(Action):
    kind = "package-remove"
    irreversible = True

    def __init__(self, name: str):
        super().__init__("pkg:" + name, "remove package " + name)
        self.name = name

    def present(self, ctx: Context) -> bool:
        return self.name in ctx.installed_packages()

    def read(self, ctx: Context) -> Observation:
        return Observation(OBS_ON, "installed")

    def _hint(self, ctx: Context) -> str:
        return "%s %s" % (_INSTALL_HINT.get(ctx.pkg_manager or "", "install"), self.name)

    def describe_off(self, lock: bool) -> str:
        return "remove package %s  (IRREVERSIBLE; reinstall it yourself later)" % self.name

    def describe_on(self) -> str:
        return "(not possible) reinstall with your package manager: %s" % self.name

    def to_off(self, ctx: Context, lock: bool) -> Dict[str, Any]:
        pm: Optional[str] = ctx.pkg_manager
        if pm not in _REMOVE:
            raise ActionError("unknown package manager; cannot remove %s" % self.name)
        if ctx.offline:
            raise ActionError("package removal needs a running system; not possible with --root")
        res = ctx.run(_REMOVE[pm] + [self.name], timeout=600)
        if not res.ok:
            raise ActionError("removing %s failed: %s" % (self.name, (res.stderr or res.stdout).strip()))
        ctx._packages = None
        return {"reinstall": self._hint(ctx)}

    def to_on(self, ctx: Context) -> Dict[str, Any]:
        raise ActionError("removed; reinstall with: " + self._hint(ctx))

    def undo(self, ctx: Context, before: Dict[str, Any]) -> str:
        raise ActionError("cannot undo removal; reinstall with: %s" % before.get("reinstall", self._hint(ctx)))


class FlatpakRemove(Action):
    kind = "flatpak-remove"
    irreversible = True

    def __init__(self, app_id: str):
        super().__init__("flatpak:" + app_id, "remove flatpak " + app_id)
        self.app_id = app_id

    @staticmethod
    def installed_ids(ctx: Context) -> List[str]:
        if "flatpak-system" in ctx.cache:
            return ctx.cache["flatpak-system"]  # type: ignore[return-value]
        ids: List[str] = []
        if ctx.can_run_services:
            res = ctx.run(["flatpak", "list", "--system", "--app", "--columns=application"])
            if res.ok:
                ids = [l.strip() for l in res.stdout.splitlines() if l.strip()]
        ctx.cache["flatpak-system"] = ids
        return ids

    def present(self, ctx: Context) -> bool:
        return self.app_id in self.installed_ids(ctx)

    def read(self, ctx: Context) -> Observation:
        return Observation(OBS_ON, "installed (system-wide)")

    def describe_off(self, lock: bool) -> str:
        return "flatpak uninstall --system %s  (IRREVERSIBLE)" % self.app_id

    def describe_on(self) -> str:
        return "(not possible) reinstall with: flatpak install %s" % self.app_id

    def to_off(self, ctx: Context, lock: bool) -> Dict[str, Any]:
        if not ctx.can_run_services:
            raise ActionError("flatpak removal needs a running system; not possible with --root")
        res = ctx.run(["flatpak", "uninstall", "--system", "-y", self.app_id], timeout=600)
        if not res.ok:
            raise ActionError("flatpak uninstall %s failed: %s" % (self.app_id, res.stderr.strip()))
        ctx.cache.pop("flatpak-system", None)
        return {"reinstall": "flatpak install " + self.app_id}

    def to_on(self, ctx: Context) -> Dict[str, Any]:
        raise ActionError("removed; reinstall with: flatpak install " + self.app_id)

    def undo(self, ctx: Context, before: Dict[str, Any]) -> str:
        raise ActionError("cannot undo removal; reinstall with: %s" % before.get("reinstall", "flatpak install " + self.app_id))
