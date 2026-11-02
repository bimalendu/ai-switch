"""Command line entry point."""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional, Sequence

from . import __version__, registry
from .context import Context
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
    if args.command == "status":
        return cmd_status(args, ctx, mods)
    parser.error("unhandled command")  # pragma: no cover
    return EXIT_USAGE
