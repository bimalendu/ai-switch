"""Core types: feature state, the Action primitive and the Module base class.

A Module is a named AI feature (for example "firefox"). It owns a list of
Actions, each of which controls one concrete thing on the system (a systemd
unit, a policy file, a snap). Status, disable, enable and revert are all derived
from the actions, so adding a module is mostly declaring data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence

from .context import Context

# Observations an Action can report from read().
OBS_ON = "on"
OBS_OFF = "off"
OBS_OFF_LOCKED = "off-locked"
OBS_UNKNOWN = "unknown"


class State:
    ON = "on"
    OFF = "off"
    OFF_LOCKED = "off (locked)"
    NOT_PRESENT = "not present"
    UNKNOWN = "unknown"


class ActionError(Exception):
    """An action could not be applied. Nothing was changed by that action."""


@dataclass
class Observation:
    state: str  # one of OBS_*
    detail: str = ""


@dataclass
class Status:
    state: str  # one of State.*
    detail: str = ""
    control: str = ""  # how it is controlled, e.g. "policy file"


class Action:
    """One controllable thing. Subclasses implement the methods below.

    ``key`` must be stable across runs: the journal finds the action again by
    key when it needs to undo it.
    """

    kind = "action"
    irreversible = False  # True for package/snap removal

    def __init__(self, key: str, label: str):
        self.key = key
        self.label = label

    # -- read side ----------------------------------------------------------

    def present(self, ctx: Context) -> bool:
        """Is the thing this action controls on the system at all?"""
        raise NotImplementedError

    def read(self, ctx: Context) -> Observation:
        raise NotImplementedError

    # -- write side ---------------------------------------------------------

    def describe_off(self, lock: bool) -> str:
        raise NotImplementedError

    def describe_on(self) -> str:
        raise NotImplementedError

    def to_off(self, ctx: Context, lock: bool) -> Dict[str, Any]:
        """Turn the feature off. Return the JSON-serialisable ``before`` record
        needed by ``undo``. Raise ActionError before changing anything if the
        change cannot be made safely."""
        raise NotImplementedError

    def to_on(self, ctx: Context) -> Dict[str, Any]:
        """Actively turn the feature on. Same contract as ``to_off``."""
        raise NotImplementedError

    def undo(self, ctx: Context, before: Dict[str, Any]) -> str:
        """Restore the state described by ``before``. Return a one-line note."""
        raise NotImplementedError

    def snapshot(self, ctx: Context) -> Dict[str, Any]:
        """Baseline captured before the first change. Defaults to nothing;
        actions whose ``to_off`` already returns everything need not override."""
        return {}


@dataclass
class Module:
    id: str
    title: str
    description: str
    experimental: bool = False
    report_only: bool = False
    # Package names removed by --purge (irreversible). Matched as globs.
    purge_globs: Sequence[str] = field(default_factory=tuple)

    def applies(self, ctx: Context) -> bool:
        """Can this module exist on this distro? False hides it entirely;
        a module that applies but finds nothing is shown as 'not present'."""
        return True

    def actions(self, ctx: Context) -> List[Action]:
        return []

    def purge_actions(self, ctx: Context) -> List[Action]:
        return []

    def control(self, ctx: Context) -> str:
        return ""

    def status(self, ctx: Context) -> Status:
        acts = [a for a in self.actions(ctx) if a.present(ctx)]
        control = self.control(ctx)
        if not acts:
            return Status(State.NOT_PRESENT, "", control)
        obs = [(a, a.read(ctx)) for a in acts]
        on = [(a, o) for a, o in obs if o.state == OBS_ON]
        unknown = [(a, o) for a, o in obs if o.state == OBS_UNKNOWN]
        detail = "; ".join("%s: %s" % (a.label, o.detail or o.state) for a, o in obs)
        if on:
            return Status(State.ON, detail, control)
        if unknown:
            return Status(State.UNKNOWN, detail, control)
        if all(o.state == OBS_OFF_LOCKED for _, o in obs):
            return Status(State.OFF_LOCKED, detail, control)
        return Status(State.OFF, detail, control)
