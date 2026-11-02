"""GUI logic without any GTK: what to show, and what to ask the helper to do.

The window (gui.py) only renders this. Keeping it GTK-free means it is unit
tested everywhere, and the GUI holds no policy of its own.
"""

from __future__ import annotations

import json
import os
import shlex
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .model import State

DEFAULT_CLI = ["ai-switch"]
DEFAULT_HELPER = ["pkexec", "/usr/libexec/ai-switch-helper"]

PKEXEC_NOT_AUTHORIZED = 127
PKEXEC_DISMISSED = 126


def command_from_env(var: str, default: Sequence[str]) -> List[str]:
    """Tests (and developers running from a checkout) can swap a command."""
    raw = os.environ.get(var)
    return shlex.split(raw) if raw else list(default)


@dataclass
class Row:
    id: str
    title: str
    description: str
    state: str
    detail: str = ""
    control: str = ""
    experimental: bool = False
    report_only: bool = False

    @property
    def can_toggle(self) -> bool:
        return not self.report_only and self.state in (State.ON, State.OFF, State.OFF_LOCKED)

    @property
    def is_on(self) -> bool:
        return self.state == State.ON

    @property
    def visible(self) -> bool:
        """Modules that find nothing are listed greyed out, not hidden."""
        return True

    @property
    def badge(self) -> str:
        return {State.ON: "AI on", State.OFF: "AI off", State.OFF_LOCKED: "AI off, locked",
                State.NOT_PRESENT: "not present", State.UNKNOWN: "unknown"}.get(self.state, self.state)

    @property
    def tooltip(self) -> str:
        if self.report_only:
            return "Report-only: lists installed software. Remove it with your package manager."
        if self.state == State.NOT_PRESENT:
            return "Nothing to control on this system."
        if self.state == State.UNKNOWN:
            return "Found, but ai-switch cannot tell its state or does not manage it."
        return self.detail


def parse_status(text: str) -> Tuple[str, List[Row]]:
    doc = json.loads(text)
    rows = [Row(m["id"], m["title"], m["description"], m["state"], m.get("detail", ""),
                m.get("control", ""), bool(m.get("experimental")), bool(m.get("report_only")))
            for m in doc["modules"]]
    return ("%s %s" % (doc.get("distro", ""), doc.get("version", ""))).strip(), rows


def pending_changes(rows: Sequence[Row], wanted: Dict[str, bool]) -> Tuple[List[str], List[str]]:
    """wanted maps module id -> True (AI on) / False (AI off), only for switches
    the user touched. Returns (ids to turn off, ids to turn on), skipping
    anything already in that state or not switchable."""
    off: List[str] = []
    on: List[str] = []
    for r in rows:
        if r.id not in wanted or not r.can_toggle:
            continue
        if wanted[r.id] and not r.is_on:
            on.append(r.id)
        elif not wanted[r.id] and r.is_on:
            off.append(r.id)
    return off, on


def apply_argv(helper: Sequence[str], off: Sequence[str], on: Sequence[str], lock: bool) -> List[str]:
    argv = list(helper) + ["apply"]
    if lock and off:
        argv.append("--lock")
    if off:
        argv += ["--off"] + list(off)
    if on:
        argv += ["--on"] + list(on)
    return argv


def revert_argv(helper: Sequence[str], ids: Sequence[str] = ()) -> List[str]:
    return list(helper) + ["revert"] + list(ids)


@dataclass
class Outcome:
    ok: bool
    cancelled: bool = False
    messages: List[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        if self.cancelled:
            return "Cancelled. Nothing was changed."
        if self.ok:
            return "Done."
        return "Some changes failed."


def parse_outcome(returncode: int, stdout: str, stderr: str = "") -> Outcome:
    """Turn a finished helper process into something to show the user."""
    if returncode in (PKEXEC_DISMISSED, PKEXEC_NOT_AUTHORIZED) and not stdout.strip():
        return Outcome(False, cancelled=True)
    try:
        doc = json.loads(stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        detail = stderr.strip() or "the helper produced no output (exit %d)" % returncode
        return Outcome(False, messages=[detail])
    msgs = [("%s: %s" % (r.get("module", ""), r.get("message", ""))).strip(": ")
            for r in doc.get("results", [])]
    msgs += list(doc.get("notes", []))
    return Outcome(bool(doc.get("ok")) and returncode == 0, messages=msgs)
