"""Planning and executing changes. No printing here: the CLI formats results."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence

from .context import Context
from .journal import Journal
from .model import (OBS_ON, Action, ActionError, Module, State)

OP_OFF = "off"
OP_ON = "on"


@dataclass
class PlanItem:
    module: Module
    action: Action
    op: str
    lock: bool = False
    purge: bool = False  # this item is a --purge removal

    def describe(self) -> str:
        return self.action.describe_off(self.lock) if self.op == OP_OFF else self.action.describe_on()


@dataclass
class Plan:
    items: List[PlanItem] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)  # modules skipped, and why

    @property
    def irreversible(self) -> bool:
        return any(i.action.irreversible for i in self.items)


@dataclass
class Result:
    module: str
    action: str
    ok: bool
    message: str


def plan_change(ctx: Context, mods: Sequence[Module], op: str, lock: bool = False,
                purge: bool = False) -> Plan:
    plan = Plan()
    for m in mods:
        items: List[PlanItem] = []
        if m.report_only and not (purge and op == OP_OFF):
            plan.notes.append("%s: report-only; it never changes anything%s" % (
                m.id, " (use --purge to remove packages)" if op == OP_OFF else ""))
            continue
        if not m.report_only:
            for a in m.actions(ctx):
                if a.present(ctx):
                    items.append(PlanItem(m, a, op, lock))
        if purge and op == OP_OFF:
            for a in m.purge_actions(ctx):
                if a.present(ctx):
                    items.append(PlanItem(m, a, OP_OFF, lock, purge=True))
        if not items:
            plan.notes.append("%s: not present on this system, nothing to do" % m.id)
            continue
        plan.items.extend(items)
    return plan


def apply_plan(ctx: Context, journal: Journal, plan: Plan, lock: bool = False) -> List[Result]:
    results: List[Result] = []
    failed_modules = set()
    modules: Dict[str, Module] = {}
    op_for: Dict[str, str] = {}
    for item in plan.items:
        mid = item.module.id
        modules[mid] = item.module
        op_for[mid] = item.op
        a = item.action
        try:
            before = a.to_off(ctx, item.lock) if item.op == OP_OFF else a.to_on(ctx)
        except ActionError as e:
            failed_modules.add(mid)
            results.append(Result(mid, a.key, False, str(e)))
            continue
        except OSError as e:
            failed_modules.add(mid)
            results.append(Result(mid, a.key, False, "%s" % e))
            continue
        journal.record(mid, a.key, a.kind, before, irreversible=a.irreversible,
                       note=before.get("note", "") if isinstance(before, dict) else "")
        verb = "done" if item.op == OP_OFF else "enabled"
        results.append(Result(mid, a.key, True, "%s: %s" % (verb, a.label)))
    for mid, m in modules.items():
        if mid in failed_modules:
            continue
        # A purge-only run still means the user wants the feature off.
        journal.set_desired(mid, op_for[mid], lock if op_for[mid] == OP_OFF else False)
    return results


def revert(ctx: Context, journal: Journal, mods: Sequence[Module],
           ids: Sequence[str]) -> List[Result]:
    """Undo journaled changes for ``ids`` (all journaled modules when empty)."""
    by_id = {m.id: m for m in mods}
    targets = list(ids) if ids else journal.modules()
    results: List[Result] = []
    for mid in targets:
        entries = journal.entries(mid)
        if not entries:
            results.append(Result(mid, "", True, "nothing to revert"))
            journal.clear_desired(mid)
            continue
        mod = by_id.get(mid)
        all_ok = True
        for entry in reversed(entries):
            key = entry["key"]
            if entry.get("irreversible"):
                hint = entry.get("before", {}).get("reinstall", "")
                results.append(Result(mid, key, True,
                                      "cannot restore %s (it was removed)%s" % (
                                          key, "; reinstall with: " + hint if hint else "")))
                journal.remove(mid, key)
                continue
            action = _find_action(ctx, mod, key)
            if action is None:
                all_ok = False
                results.append(Result(mid, key, False,
                                      "no longer known to this version of ai-switch; entry kept"))
                continue
            try:
                msg = action.undo(ctx, entry["before"])
            except (ActionError, OSError) as e:
                all_ok = False
                results.append(Result(mid, key, False, "%s; entry kept" % e))
                continue
            journal.remove(mid, key)
            results.append(Result(mid, key, True, msg))
        if all_ok:
            journal.clear_desired(mid)
    journal.delete_if_empty()
    return results


def _find_action(ctx: Context, mod: Any, key: str):
    if mod is None:
        return None
    for a in list(mod.actions(ctx)) + list(mod.purge_actions(ctx)):
        if a.key == key:
            return a
    return None


def reconcile(ctx: Context, journal: Journal, mods: Sequence[Module]) -> List[Result]:
    """Re-apply 'off' choices that a package update undid."""
    by_id = {m.id: m for m in mods}
    results: List[Result] = []
    for mid, want in sorted(journal.desired().items()):
        if want.get("state") != OP_OFF:
            continue
        mod = by_id.get(mid)
        if mod is None:
            results.append(Result(mid, "", False, "module unknown to this version; skipped"))
            continue
        status = mod.status(ctx)
        if status.state != State.ON:
            continue
        sub = plan_change(ctx, [mod], OP_OFF, lock=bool(want.get("lock")))
        # Only touch actions that are currently on.
        sub.items = [i for i in sub.items if i.action.read(ctx).state == OBS_ON]
        results.extend(apply_plan(ctx, journal, sub, lock=bool(want.get("lock"))))
    return results
