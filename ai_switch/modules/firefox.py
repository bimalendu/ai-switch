"""Firefox: the AIControls enterprise policy.

Mozilla documents ``AIControls`` (Firefox 149.0.2+, ESR 153+). The policy file
location depends on how Firefox was packaged, so we find the installed builds
and write to the right file for each:

* Fedora / RHEL / openSUSE / Arch / Mozilla tarball: ``<install dir>/distribution/policies.json``
  (Fedora proven in quill-os docs/no-ai-policy.md).
* Debian / Ubuntu: ``/etc/firefox/policies/policies.json`` (``/etc/firefox-esr/policies``
  for the ESR package). Only user reports back this; see docs/catalog.md.
* Snap on Ubuntu: same ``/etc/firefox/policies`` file, path unverified: experimental.
* Flatpak: reported as unknown, never written (no verified mechanism).
"""

from __future__ import annotations

import glob
import json
import zipfile
from typing import Any, Dict, List, Optional

from ..actions.policyjson import PolicyJsonMerge, leaves_match
from ..context import Context
from ..model import (OBS_OFF, OBS_OFF_LOCKED, OBS_ON, OBS_UNKNOWN, Action,
                     ActionError, Module, Observation)

INSTALL_DIRS = (
    "/usr/lib64/firefox",
    "/usr/lib/firefox",
    "/usr/lib/firefox-esr",
    "/opt/firefox",
)
SCHEMA_IN_OMNI = "modules/policies/policies-schema.json"
FEATURES = ("Translations", "PDFAltText", "SmartTabGroups", "LinkPreviewKeyPoints",
            "SidebarChatbot", "SmartWindow", "SpeechRecognition")


def off_payload(lock: bool) -> Dict[str, Any]:
    return {"policies": {"AIControls": {"Default": {"Value": "blocked", "Locked": bool(lock)}}}}


def on_payload(_lock: bool) -> Dict[str, Any]:
    return {"policies": {"AIControls": {"Default": {"Value": "available", "Locked": False}}}}


def read_policy(data: Dict[str, Any]) -> Observation:
    if leaves_match(data, off_payload(True)):
        return Observation(OBS_OFF_LOCKED, "AI blocked and locked")
    if leaves_match(data, off_payload(False)):
        return Observation(OBS_OFF, "AI blocked (users can change it)")
    return Observation(OBS_ON, "no AIControls block")


def load_schema(ctx: Context, install_dir: str) -> Optional[Dict[str, Any]]:
    """The policies schema of the installed Firefox, or None if unreadable."""
    for omni in ("%s/browser/omni.ja" % install_dir, "%s/omni.ja" % install_dir):
        try:
            with zipfile.ZipFile(str(ctx.path(omni))) as z:
                return json.loads(z.read(SCHEMA_IN_OMNI).decode("utf-8"))["properties"]
        except (OSError, KeyError, ValueError, zipfile.BadZipFile):
            continue
    return None


def validate(ctx: Context, install_dir: str, payload: Dict[str, Any]) -> List[str]:
    """Check the AIControls payload against the schema inside the installed
    Firefox. Returns a list of problems; empty means fine or not checkable.
    Ported from quill-os tests/check-no-ai.sh."""
    schema = load_schema(ctx, install_dir)
    if schema is None:
        return []  # cannot check (snap, no omni.ja): the caller notes this
    if "AIControls" not in schema:
        return ["this Firefox does not know the AIControls policy "
                "(needs Firefox 149.0.2 or newer, ESR 153 or newer)"]
    feats = schema["AIControls"]["properties"]
    problems = []
    for feat, cfg in payload["policies"]["AIControls"].items():
        if feat not in feats:
            problems.append("AIControls has no feature %s in this Firefox" % feat)
            continue
        props = feats[feat]["properties"]
        allowed = [o["const"] for o in props["Value"]["oneOf"]]
        if cfg.get("Value") not in allowed:
            problems.append("AIControls.%s.Value %r not in %s" % (feat, cfg.get("Value"), allowed))
        extra = set(cfg) - set(props)
        if extra:
            problems.append("AIControls.%s has unknown keys %s" % (feat, sorted(extra)))
    return problems


def policy_path(ctx: Context, install_dir: str) -> str:
    if ctx.is_distro("debian", "ubuntu"):
        name = "firefox-esr" if install_dir.endswith("esr") else "firefox"
        return "/etc/%s/policies/policies.json" % name
    return "%s/distribution/policies.json" % install_dir


class FirefoxPolicy(PolicyJsonMerge):
    def __init__(self, ctx: Context, path: str, label: str, install_dir: Optional[str],
                 unverified: bool = False):
        super().__init__(
            key="policy:" + path, label=label, path=path,
            installed=lambda c: True,  # only constructed for installed builds
            off=off_payload, on=on_payload, reader=read_policy,
            precheck=self._check)
        self.install_dir = install_dir
        self.experimental = unverified

    def _check(self, ctx: Context) -> None:
        if self.install_dir is None:
            return
        problems = validate(ctx, self.install_dir, off_payload(False))
        if problems:
            raise ActionError("; ".join(problems))


class FirefoxFlatpak(Action):
    """Detected, reported as unknown, never written: no verified mechanism."""

    kind = "unsupported"
    experimental = True

    def __init__(self) -> None:
        super().__init__("flatpak:org.mozilla.firefox", "Firefox (Flatpak)")

    def present(self, ctx: Context) -> bool:
        return ctx.path("/var/lib/flatpak/app/org.mozilla.firefox").exists()

    def read(self, ctx: Context) -> Observation:
        return Observation(OBS_UNKNOWN, "Flatpak policy location not verified; not managed")

    def describe_off(self, lock: bool) -> str:
        return "(skipped) Firefox Flatpak is not supported yet"

    def describe_on(self) -> str:
        return self.describe_off(False)

    def to_off(self, ctx: Context, lock: bool) -> Dict[str, Any]:
        raise ActionError("Firefox Flatpak is not supported yet (see docs/catalog.md)")

    to_on = to_off  # type: ignore[assignment]

    def undo(self, ctx: Context, before: Dict[str, Any]) -> str:
        return "nothing to undo"


class Firefox(Module):
    def __init__(self) -> None:
        super().__init__(
            "firefox", "Firefox AI features",
            "Blocks Firefox's built-in AI (chatbot sidebar, link previews, smart tab groups, "
            "translations, PDF alt text, ...) with the AIControls enterprise policy.")

    def control(self, ctx: Context) -> str:
        return "AIControls policy in policies.json"

    def actions(self, ctx: Context) -> List[Action]:
        out: List[Action] = []
        seen = set()
        for d in INSTALL_DIRS:
            if not any(ctx.path(d + "/" + f).exists()
                       for f in ("firefox", "firefox-esr", "browser/omni.ja", "omni.ja")):
                continue
            path = policy_path(ctx, d)
            if path in seen:
                continue
            seen.add(path)
            out.append(FirefoxPolicy(ctx, path, "Firefox (%s)" % d, d))
        if glob.glob(str(ctx.path("/var/lib/snapd/snaps/firefox_*.snap"))) \
                or ctx.path("/snap/firefox").exists():
            path = "/etc/firefox/policies/policies.json"
            if path not in seen:
                seen.add(path)
                out.append(FirefoxPolicy(ctx, path, "Firefox snap (unverified path)", None,
                                         unverified=True))
        out.append(FirefoxFlatpak())
        return out
