"""Merge keys into a JSON policy file that other tools or admins also use.

Used for Firefox, whose only policy file is shared (``policies.json``). Only
the leaf keys we set are touched. Undo restores each of those leaves to what it
was, and when nothing else changed the file since, restores the original bytes.
"""

from __future__ import annotations

import copy
import json
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..context import Context
from ..model import (OBS_OFF, OBS_OFF_LOCKED, OBS_ON, OBS_UNKNOWN, Action,
                     ActionError, Observation)
from . import fsutil

Path_ = Tuple[str, ...]
_MISSING = object()


def leaf_paths(obj: Any, prefix: Path_ = ()) -> List[Path_]:
    """Paths to every non-dict value in a nested dict."""
    if isinstance(obj, dict) and obj:
        out: List[Path_] = []
        for k, v in obj.items():
            out.extend(leaf_paths(v, prefix + (k,)))
        return out
    return [prefix]


def get_path(obj: Any, path: Path_) -> Any:
    for k in path:
        if not isinstance(obj, dict) or k not in obj:
            return _MISSING
        obj = obj[k]
    return obj


def set_path(obj: Dict[str, Any], path: Path_, value: Any) -> None:
    for k in path[:-1]:
        nxt = obj.get(k)
        if nxt is None:
            nxt = obj[k] = {}
        if not isinstance(nxt, dict):
            raise ActionError("policy key %s is not an object in the existing file" % "/".join(path))
        obj = nxt
    obj[path[-1]] = value


def delete_path(obj: Dict[str, Any], path: Path_) -> None:
    parents = []
    for k in path[:-1]:
        nxt = obj.get(k) if isinstance(obj, dict) else None
        if not isinstance(nxt, dict):
            return
        parents.append((obj, k))
        obj = nxt
    obj.pop(path[-1], None)
    # Prune objects we emptied.
    for parent, k in reversed(parents):
        if parent[k] == {}:
            del parent[k]
        else:
            break


def _dump(data: Dict[str, Any]) -> str:
    return json.dumps(data, indent=2) + "\n"


Reader = Callable[[Dict[str, Any]], Observation]
Payload = Callable[[bool], Dict[str, Any]]


class PolicyJsonMerge(Action):
    kind = "policy-json"

    def __init__(self, key: str, label: str, path: str,
                 installed: Callable[[Context], bool],
                 off: Payload, on: Payload, reader: Reader):
        super().__init__(key, label)
        self.path = path
        self._installed = installed
        self._off, self._on, self._reader = off, on, reader

    def _file(self, ctx: Context):
        return ctx.path(self.path)

    def present(self, ctx: Context) -> bool:
        return self._installed(ctx)

    def _load(self, ctx: Context):
        text = fsutil.read_text_or_none(self._file(ctx))
        if text is None:
            return None, {}
        try:
            data = json.loads(text)
        except ValueError as e:
            raise ActionError("%s is not valid JSON (%s); fix or move it first" % (self._file(ctx), e))
        if not isinstance(data, dict):
            raise ActionError("%s does not contain a JSON object" % self._file(ctx))
        return text, data

    def read(self, ctx: Context) -> Observation:
        try:
            _, data = self._load(ctx)
        except ActionError as e:
            return Observation(OBS_UNKNOWN, str(e))
        return self._reader(data)

    def describe_off(self, lock: bool) -> str:
        return "merge %s into %s" % (json.dumps(self._off(lock), sort_keys=True), self.path)

    def describe_on(self) -> str:
        return "merge %s into %s" % (json.dumps(self._on(False), sort_keys=True), self.path)

    def _all_paths(self) -> List[Path_]:
        seen: List[Path_] = []
        for payload in (self._off(False), self._off(True), self._on(False)):
            for p in leaf_paths(payload):
                if p not in seen:
                    seen.append(p)
        return seen

    def _apply(self, ctx: Context, payload: Dict[str, Any]) -> Dict[str, Any]:
        f = self._file(ctx)
        orig_text, data = self._load(ctx)
        paths = self._all_paths()
        prior = []
        for p in paths:
            v = get_path(data, p)
            if v is not _MISSING:
                prior.append({"path": list(p), "value": copy.deepcopy(v)})
        new = copy.deepcopy(data)
        for p in leaf_paths(payload):
            set_path(new, p, get_path(payload, p))
        created = fsutil.make_dirs(f.parent) if orig_text is None else []
        text = _dump(new)
        fsutil.write_atomic(f, text)
        return {"existed": orig_text is not None, "orig_text": orig_text,
                "written_text": text, "prior": prior,
                "paths": [list(p) for p in paths], "created_dirs": created}

    def to_off(self, ctx: Context, lock: bool) -> Dict[str, Any]:
        return self._apply(ctx, self._off(lock))

    def to_on(self, ctx: Context) -> Dict[str, Any]:
        return self._apply(ctx, self._on(False))

    def undo(self, ctx: Context, before: Dict[str, Any]) -> str:
        f = self._file(ctx)
        current_text = fsutil.read_text_or_none(f)
        if current_text is None:
            return "%s already gone" % self.path
        # Fast path: nobody touched the file after us, so restore it exactly.
        if current_text == before.get("written_text"):
            if before["existed"]:
                fsutil.write_atomic(f, before["orig_text"])
            else:
                f.unlink()
                fsutil.remove_created_dirs(before.get("created_dirs", []))
            return "restored %s" % self.path
        # Someone edited it since. Revert only our leaves.
        try:
            data = json.loads(current_text)
        except ValueError:
            raise ActionError("%s is no longer valid JSON; not touching it" % f)
        prior = {tuple(e["path"]): e["value"] for e in before.get("prior", [])}
        for p in (tuple(x) for x in before.get("paths", [])):
            if p in prior:
                set_path(data, p, prior[p])
            else:
                delete_path(data, p)
        if not before["existed"] and data in ({}, {"policies": {}}):
            f.unlink()
            fsutil.remove_created_dirs(before.get("created_dirs", []))
        else:
            fsutil.write_atomic(f, _dump(data))
        return "reverted our keys in %s (file had other edits, kept them)" % self.path


def leaves_match(data: Dict[str, Any], payload: Dict[str, Any]) -> Optional[bool]:
    """True if every leaf in payload equals the same path in data."""
    for p in leaf_paths(payload):
        if get_path(data, p) != get_path(payload, p):
            return False
    return True
