"""Feature modules. Add one by appending an instance to MODULES."""

from __future__ import annotations

from typing import List

from ..model import Module
from .chromium import ChromiumFamily
from .firefox import Firefox
from .known_apps import KnownAiApps
from .services import DEEPIN_AI, LOCAL_LLM, RHEL_LIGHTSPEED, SUSE_AI, UbuntuInference

# Order is the order shown by `status`: browsers first (widest reach).
MODULES: List[Module] = [
    Firefox(),
    ChromiumFamily(),
    RHEL_LIGHTSPEED,
    SUSE_AI,
    UbuntuInference(),
    DEEPIN_AI,
    LOCAL_LLM,
    KnownAiApps(),
]
