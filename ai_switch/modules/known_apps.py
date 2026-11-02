"""Report-only: installed AI applications from a denylist.

Seeded from quill-os policy/ai-denylist.txt. Never changes anything, except
that ``disable --purge`` removes them (irreversible).
"""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import List, Optional, Tuple

from ..actions.packages import FlatpakRemove, PackageRemove
from ..context import Context
from ..model import Action, Module, State, Status

DATA = Path(__file__).resolve().parent.parent / "data" / "known-ai-apps.txt"
RPM_FAMILY = ("dnf", "zypper")


def parse_list(text: str) -> List[Tuple[str, str]]:
    """[(kind, glob)] with kind in 'any', 'rpm', 'flatpak'. Inline # comments allowed."""
    out = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        kind, value = (x.strip() for x in line.split(":", 1))
        if kind in ("any", "rpm", "flatpak") and value:
            out.append((kind, value))
    return out


class KnownAiApps(Module):
    def __init__(self, data: Optional[Path] = None) -> None:
        super().__init__(
            "known-ai-apps", "Installed AI applications",
            "Lists installed AI assistants and local LLM runtimes. Report-only: remove them "
            "yourself, or run 'disable known-ai-apps --purge'.",
            report_only=True)
        self.entries = parse_list((data or DATA).read_text(encoding="utf-8"))

    def control(self, ctx: Context) -> str:
        return "report-only"

    def _pkg_globs(self, ctx: Context) -> List[str]:
        globs = [v for k, v in self.entries if k == "any"]
        if ctx.pkg_manager in RPM_FAMILY:
            globs += [v for k, v in self.entries if k == "rpm"]
        return globs

    def found(self, ctx: Context) -> Tuple[List[str], List[str]]:
        pkgs = ctx.packages_matching(self._pkg_globs(ctx))
        flat = [i for i in FlatpakRemove.installed_ids(ctx)
                if any(fnmatch.fnmatchcase(i, v) for k, v in self.entries if k == "flatpak")]
        return pkgs, flat

    def status(self, ctx: Context) -> Status:
        pkgs, flat = self.found(ctx)
        if not pkgs and not flat:
            return Status(State.NOT_PRESENT, "", self.control(ctx))
        detail = "installed: " + ", ".join(pkgs + ["flatpak " + f for f in flat])
        return Status(State.ON, detail, self.control(ctx))

    def purge_actions(self, ctx: Context) -> List[Action]:
        pkgs, flat = self.found(ctx)
        return [PackageRemove(p) for p in pkgs] + [FlatpakRemove(f) for f in flat]
