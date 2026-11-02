"""Command line entry point."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional, Sequence

from . import __version__, engine, registry
from .context import Context
from .journal import Journal, JournalError
from .model import Module

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_NEEDS_ROOT = 3


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ai-switch",
        description="Explicitly turn system-level AI features on or off.",
    )
    p.add_argument("--version", action="version", version="ai-switch " + __version__)
    p.add_argument("--root", default="/", metavar="DIR",
                   help="operate on an offline image or directory tree instead of /")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    sub = p.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    st = sub.add_parser("status", help="show the state of every AI feature (read-only)")
    st.add_argument("ids", nargs="*", metavar="ID", help="limit to these modules")

    for name, help_ in (("disable", "turn AI features off"), ("enable", "turn AI features on")):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("ids", nargs="*", metavar="ID")
        sp.add_argument("--all", action="store_true", help="every module present on this system")
        sp.add_argument("--dry-run", action="store_true", help="show the plan, change nothing")
        sp.add_argument("--yes", "-y", action="store_true", help="do not ask for confirmation")
        if name == "disable":
            sp.add_argument("--lock", action="store_true",
                            help="where supported (Firefox), stop users re-enabling the feature")
            sp.add_argument("--purge", action="store_true",
                            help="also REMOVE the packages/snaps/flatpaks (cannot be reverted)")

    rv = sub.add_parser("revert", help="restore what the system looked like before ai-switch")
    rv.add_argument("ids", nargs="*", metavar="ID", help="default: everything ai-switch changed")
    rv.add_argument("--yes", "-y", action="store_true")

    sub.add_parser("reconcile",
                   help="re-apply 'off' choices that a package update undid (used by hooks)")
    return p


def _status_rows(ctx: Context, mods: Sequence[Module]) -> List[dict]:
    rows = []
    for m in mods:
        s = m.status(ctx)
        rows.append({
            "id": m.id,
            "title": m.title,
            "description": m.description,
            "state": s.state,
            "detail": s.detail,
            "control": s.control,
            "experimental": m.experimental,
            "report_only": m.report_only,
        })
    return rows


def _print_table(rows: List[dict]) -> None:
    if not rows:
        print("No AI features known for this system.")
        return
    idw = max(len(r["id"]) for r in rows + [{"id": "MODULE"}])
    stw = max(len(r["state"]) for r in rows + [{"state": "STATE"}])
    print("%-*s  %-*s  %s" % (idw, "MODULE", stw, "STATE", "DETAIL"))
    for r in rows:
        flags = []
        if r["experimental"]:
            flags.append("experimental")
        if r["report_only"]:
            flags.append("report-only")
        tail = r["detail"] or r["title"]
        if flags:
            tail = "%s [%s]" % (tail, ", ".join(flags))
        print("%-*s  %-*s  %s" % (idw, r["id"], stw, r["state"], tail))


def cmd_status(args: argparse.Namespace, ctx: Context, mods: Sequence[Module]) -> int:
    if args.ids:
        try:
            mods = registry.resolve(args.ids, mods)
        except KeyError as e:
            print("ai-switch: unknown module: %s" % e.args[0], file=sys.stderr)
            return EXIT_USAGE
    rows = _status_rows(ctx, mods)
    if args.json:
        json.dump({"distro": ctx.distro_id, "version": ctx.version_id, "modules": rows},
                  sys.stdout, indent=2)
        print()
    else:
        _print_table(rows)
    return EXIT_OK


def _err(msg: str) -> None:
    print("ai-switch: " + msg, file=sys.stderr)


def _needs_root(ctx: Context) -> bool:
    return not ctx.offline and hasattr(os, "geteuid") and os.geteuid() != 0


def _emit(args: argparse.Namespace, payload: dict, lines: List[str]) -> None:
    if args.json:
        json.dump(payload, sys.stdout, indent=2)
        print()
    else:
        for l in lines:
            print(l)


def _print_results(args: argparse.Namespace, results: List[engine.Result]) -> int:
    ok = all(r.ok for r in results)
    _emit(args, {"ok": ok, "results": [r.__dict__ for r in results]},
          [("  ok    " if r.ok else "  FAIL  ") + "[%s] %s" % (r.module, r.message) for r in results])
    return EXIT_OK if ok else EXIT_FAILED


def _confirm(args: argparse.Namespace, question: str) -> bool:
    if getattr(args, "yes", False):
        return True
    if args.json or not sys.stdin.isatty():
        _err("refusing to change anything without --yes (not an interactive terminal)")
        return False
    return input(question + " [y/N] ").strip().lower() in ("y", "yes")


def cmd_change(args: argparse.Namespace, ctx: Context, mods: Sequence[Module]) -> int:
    op = engine.OP_OFF if args.command == "disable" else engine.OP_ON
    purge = bool(getattr(args, "purge", False))
    lock = bool(getattr(args, "lock", False))
    if args.all and args.ids:
        _err("give module ids or --all, not both")
        return EXIT_USAGE
    if not args.all and not args.ids:
        _err("name at least one module, or use --all (see: ai-switch status)")
        return EXIT_USAGE
    try:
        chosen = list(mods) if args.all else registry.resolve(args.ids, mods)
    except KeyError as e:
        _err("unknown module: %s" % e.args[0])
        return EXIT_USAGE

    plan = engine.plan_change(ctx, chosen, op, lock=lock, purge=purge)
    lines = ["Plan (%s):" % args.command] + ["  [%s] %s" % (i.module.id, i.describe()) for i in plan.items]
    lines += ["  note: " + n for n in plan.notes]
    if plan.irreversible:
        lines.append("WARNING: removals cannot be reverted by 'ai-switch revert'.")
    seen = set()
    for i in plan.items:
        if (i.module.experimental or i.action.experimental) and i.module.id not in seen:
            seen.add(i.module.id)
            lines.append("  note: %s is experimental (not fully verified); see docs/catalog.md"
                         % i.module.id)
    if args.dry_run:
        _emit(args, {"dry_run": True, "plan": [{"module": i.module.id, "action": i.action.key,
                                                  "op": i.op, "what": i.describe()}
                                                 for i in plan.items], "notes": plan.notes},
              lines + ["Dry run: nothing was changed."])
        return EXIT_OK
    if not plan.items:
        _emit(args, {"ok": True, "results": [], "notes": plan.notes}, lines)
        return EXIT_OK
    if _needs_root(ctx):
        _err("this needs root. Re-run with sudo (or use --dry-run to preview).")
        return EXIT_NEEDS_ROOT
    if not args.json:
        for l in lines:
            print(l)
    if not _confirm(args, "Apply this plan?"):
        _err("cancelled; nothing was changed")
        return EXIT_USAGE
    journal = Journal(ctx)
    with journal.locked():
        results = engine.apply_plan(ctx, journal, plan, lock=lock)
    return _print_results(args, results)


def cmd_revert(args: argparse.Namespace, ctx: Context, mods: Sequence[Module]) -> int:
    journal = Journal(ctx)
    if args.ids:
        try:
            registry.resolve(args.ids, list(mods))
        except KeyError as e:
            _err("unknown module: %s" % e.args[0])
            return EXIT_USAGE
    targets = list(args.ids) or journal.modules()
    if not targets:
        _emit(args, {"ok": True, "results": []}, ["Nothing to revert: ai-switch has changed nothing."])
        return EXIT_OK
    if _needs_root(ctx):
        _err("this needs root. Re-run with sudo.")
        return EXIT_NEEDS_ROOT
    if not args.json:
        print("Will restore the original state of: " + ", ".join(targets))
    if not _confirm(args, "Revert?"):
        _err("cancelled; nothing was changed")
        return EXIT_USAGE
    with journal.locked():
        results = engine.revert(ctx, journal, mods, args.ids)
    return _print_results(args, results)


def cmd_reconcile(args: argparse.Namespace, ctx: Context, mods: Sequence[Module]) -> int:
    if _needs_root(ctx):
        _err("this needs root.")
        return EXIT_NEEDS_ROOT
    journal = Journal(ctx)
    with journal.locked():
        results = engine.reconcile(ctx, journal, mods)
    if not results:
        _emit(args, {"ok": True, "results": []}, ["Nothing to reconcile: all choices still in effect."])
        return EXIT_OK
    return _print_results(args, results)


def main(argv: Optional[Sequence[str]] = None, ctx: Optional[Context] = None,
         modules: Optional[Sequence[Module]] = None) -> int:
    """``ctx`` and ``modules`` can be injected by tests."""
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return EXIT_USAGE if e.code else EXIT_OK
    ctx = ctx or Context(args.root)
    mods = registry.applicable(ctx, modules)
    try:
        if args.command == "status":
            return cmd_status(args, ctx, mods)
        if args.command in ("disable", "enable"):
            return cmd_change(args, ctx, mods)
        if args.command == "revert":
            # Revert must find modules even if they no longer apply here.
            return cmd_revert(args, ctx, registry.all_modules() if modules is None else modules)
        if args.command == "reconcile":
            return cmd_reconcile(args, ctx, mods)
    except JournalError as e:
        _err(str(e))
        return EXIT_FAILED
    parser.error("unhandled command")  # pragma: no cover
    return EXIT_USAGE
