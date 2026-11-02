"""The privileged entry point behind the GUI's pkexec call.

polkit lets a user run exactly one executable as root. If that executable were
the full CLI, "authorise the GUI" would mean "authorise anything ai-switch can
do". This helper is deliberately smaller:

    ai-switch-helper apply [--lock] [--off ID...] [--on ID...]
    ai-switch-helper revert [ID...]

* module ids only, validated against the registry; no --purge, no --root,
  no --all, no free-form paths, so it can never remove software or touch an
  offline tree;
* always the live system (root "/"), never an environment-supplied root;
* one JSON document on stdout: {"ok": bool, "results": [...], "notes": [...]}.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import sys
from typing import Dict, List, Optional, Sequence

from . import cli, registry
from .context import Context
from .model import Module

ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")


class Refusal(Exception):
    pass


def _ids(args: Sequence[str], known: Dict[str, Module]) -> List[str]:
    out = []
    for a in args:
        if not ID_RE.match(a) or a not in known:
            raise Refusal("not a known module id: %r" % a)
        if known[a].report_only:
            raise Refusal("%s is report-only and cannot be switched" % a)
        out.append(a)
    return out


def parse(argv: Sequence[str], known: Dict[str, Module]):
    """Returns ('apply', off, on, lock) or ('revert', ids)."""
    if not argv:
        raise Refusal("usage: apply [--lock] [--off ID...] [--on ID...] | revert [ID...]")
    verb, rest = argv[0], list(argv[1:])
    if verb == "revert":
        return ("revert", _ids(rest, known))
    if verb != "apply":
        raise Refusal("unknown command %r" % verb)
    lock, bucket = False, None
    off: List[str] = []
    on: List[str] = []
    for a in rest:
        if a == "--lock":
            lock = True
        elif a == "--off":
            bucket = off
        elif a == "--on":
            bucket = on
        elif a.startswith("-"):
            raise Refusal("option not allowed: %s" % a)
        elif bucket is None:
            raise Refusal("ids must follow --off or --on")
        else:
            bucket.append(a)
    off, on = _ids(off, known), _ids(on, known)
    if set(off) & set(on):
        raise Refusal("a module cannot be both on and off")
    if not off and not on:
        raise Refusal("nothing to do")
    return ("apply", off, on, lock)


def _call(argv: List[str], ctx: Optional[Context], mods) -> dict:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        code = cli.main(argv, ctx=ctx, modules=mods)
    try:
        doc = json.loads(buf.getvalue())
    except ValueError:
        why = "needs root" if code == cli.EXIT_NEEDS_ROOT else "no output from ai-switch"
        doc = {"ok": False, "results": [], "notes": ["%s (exit %d)" % (why, code)]}
    doc["exit"] = code
    return doc


def run(argv: Sequence[str], ctx: Optional[Context] = None, modules=None) -> dict:
    """Execute a validated request. ``ctx`` / ``modules`` are for tests."""
    mods = list(modules) if modules is not None else registry.all_modules()
    known = {m.id: m for m in mods}
    try:
        req = parse(argv, known)
    except Refusal as e:
        return {"ok": False, "results": [], "notes": [str(e)], "exit": cli.EXIT_USAGE}
    calls: List[List[str]] = []
    if req[0] == "revert":
        calls.append(["--json", "revert", "--yes"] + req[1])
    else:
        _, off, on, lock = req
        if off:
            calls.append(["--json", "disable", "--yes"] + (["--lock"] if lock else []) + off)
        if on:
            calls.append(["--json", "enable", "--yes"] + on)
    merged = {"ok": True, "results": [], "notes": [], "exit": 0}
    for argv_ in calls:
        doc = _call(argv_, ctx, mods)
        merged["ok"] = merged["ok"] and bool(doc.get("ok", False))
        merged["results"] += doc.get("results", [])
        merged["notes"] += doc.get("notes", [])
        merged["exit"] = merged["exit"] or doc["exit"]
    return merged


def main(argv: Optional[Sequence[str]] = None) -> int:
    doc = run(sys.argv[1:] if argv is None else argv)
    json.dump(doc, sys.stdout)
    sys.stdout.write("\n")
    return int(doc["exit"])


if __name__ == "__main__":  # python3 -m ai_switch.helper (tests, checkouts)
    sys.exit(main())
