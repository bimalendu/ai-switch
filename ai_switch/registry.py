"""Finds the modules and filters them for the current system."""

from __future__ import annotations

from typing import List, Optional, Sequence

from .context import Context
from .model import Module


def all_modules() -> List[Module]:
    from . import modules

    return list(modules.MODULES)


def applicable(ctx: Context, mods: Optional[Sequence[Module]] = None) -> List[Module]:
    mods = all_modules() if mods is None else mods
    return [m for m in mods if m.applies(ctx)]


def resolve(ids: Sequence[str], mods: Sequence[Module]) -> List[Module]:
    """Map module ids to modules. Raises KeyError naming the first unknown id."""
    by_id = {m.id: m for m in mods}
    out = []
    for i in ids:
        if i not in by_id:
            raise KeyError(i)
        out.append(by_id[i])
    return out
