"""Chromium family: managed-policy drop-ins that turn off built-in generative AI.

Policy names and the value that disables each one were read from Chromium's
policy definitions on 2026-11-02 (docs/catalog.md). Browsers write
``<managed dir>/ai-switch.json``; managed policies are mandatory, so a matching
file shows as 'off (locked)'.

GenAiDefaultSettings is deliberately not written: it is documented as
cloud-only, and Chrome would flag it as an error when set from a file.
"""

from __future__ import annotations

from typing import Any, Dict, List, NamedTuple, Sequence

from ..actions.files import OwnedJsonFile
from ..context import Context
from ..model import Action, Module

FILENAME = "ai-switch.json"

# value that DISABLES each feature (0/1/2 enums differ per policy).
CHROME_POLICIES: Dict[str, Any] = {
    "GeminiSettings": 1,
    "AIModeSettings": 1,
    "HelpMeWriteSettings": 2,
    "TabCompareSettings": 2,
    "CreateThemesSettings": 2,
    "DevToolsGenAiSettings": 2,
    "GenAILocalFoundationalModelSettings": 1,
    "SearchContentSharingSettings": 1,
    "GeminiActOnWebSettings": 1,
    "ThirdPartyAiChatSettings": 1,
}
EDGE_POLICIES: Dict[str, Any] = {
    "GenAILocalFoundationalModelSettings": 1,
    "HubsSidebarEnabled": False,
    "CopilotPageContext": False,
    "Microsoft365CopilotChatIconEnabled": False,
}
BRAVE_POLICIES: Dict[str, Any] = {
    "BraveAIChatEnabled": False,
    "GenAILocalFoundationalModelSettings": 1,
    "DevToolsGenAiSettings": 2,
}


class Browser(NamedTuple):
    id: str
    name: str
    markers: Sequence[str]
    managed_dir: str
    policies: Dict[str, Any]
    experimental: bool


BROWSERS = (
    Browser("chrome", "Google Chrome",
            ("/opt/google/chrome/chrome",), "/etc/opt/chrome/policies/managed",
            CHROME_POLICIES, False),
    Browser("chromium", "Chromium",
            ("/usr/lib64/chromium-browser/chromium-browser", "/usr/lib/chromium-browser/chromium-browser",
             "/usr/lib/chromium/chromium", "/usr/lib64/chromium/chromium"),
            "/etc/chromium/policies/managed", CHROME_POLICIES, False),
    Browser("edge", "Microsoft Edge",
            ("/opt/microsoft/msedge/msedge",), "/etc/opt/edge/policies/managed",
            EDGE_POLICIES, True),
    Browser("brave", "Brave",
            ("/opt/brave.com/brave/brave",), "/etc/brave/policies/managed",
            BRAVE_POLICIES, True),
)


class ChromiumPolicy(OwnedJsonFile):
    def __init__(self, b: Browser):
        path = "%s/%s" % (b.managed_dir, FILENAME)
        super().__init__(
            key="managed:" + path,
            label=b.name + (" (experimental)" if b.experimental else ""),
            path=path,
            installed=lambda ctx, b=b: any(ctx.path(m).exists() for m in b.markers),
            content=dict(b.policies))
        self.experimental = b.experimental


class ChromiumFamily(Module):
    def __init__(self) -> None:
        super().__init__(
            "chromium-family", "Chrome, Chromium, Edge and Brave AI features",
            "Turns off Gemini, AI Mode, Help me write, Tab compare, on-device models and similar "
            "with managed policy files. Edge and Brave are experimental.")

    def control(self, ctx: Context) -> str:
        return "managed policy file ai-switch.json"

    def actions(self, ctx: Context) -> List[Action]:
        return [ChromiumPolicy(b) for b in BROWSERS]
