"""Modules that mask systemd units: distro AI assistants and local LLM servers.

Unit names come from two places: names we know (candidate list) and units
discovered from the package database for packages we know. A unit is only
acted on if it exists on the system.
"""

from __future__ import annotations

from typing import List, Sequence

from ..actions.packages import PackageRemove
from ..actions.systemd import SystemdMask
from ..actions.snap import SnapDisable, SnapRemove
from ..context import Context
from ..model import Action, Module


class ServiceModule(Module):
    """Mask units; --purge removes packages (and snaps)."""

    def __init__(self, id: str, title: str, description: str, *,
                 distros: Sequence[str] = (), packages: Sequence[str] = (),
                 units: Sequence[str] = (), experimental: bool = False):
        super().__init__(id, title, description, experimental=experimental,
                         purge_globs=tuple(packages))
        self.distros = tuple(distros)
        self.packages = tuple(packages)
        self.units = tuple(units)

    def applies(self, ctx: Context) -> bool:
        return not self.distros or ctx.is_distro(*self.distros)

    def control(self, ctx: Context) -> str:
        return "systemd unit mask"

    def _unit_names(self, ctx: Context) -> List[str]:
        names = list(self.units)
        for u in ctx.units_of_packages(ctx.packages_matching(self.packages)):
            if u not in names:
                names.append(u)
        return names

    def actions(self, ctx: Context) -> List[Action]:
        out: List[Action] = []
        for u in self._unit_names(ctx):
            a = SystemdMask(u)
            a.experimental = self.experimental
            out.append(a)
        return out

    def purge_actions(self, ctx: Context) -> List[Action]:
        return [PackageRemove(p) for p in ctx.packages_matching(self.packages)]


RHEL_LIGHTSPEED = ServiceModule(
    "rhel-lightspeed", "RHEL Lightspeed command-line assistant",
    "Masks the clad daemon of the command-line assistant. --purge removes the package.",
    distros=("rhel", "fedora", "centos"),
    packages=("command-line-assistant",),
    units=("clad.service", "clad.socket"))

SUSE_AI = ServiceModule(
    "suse-ai", "SUSE AI (MCP host)",
    "Masks the services of SUSE's MCP host. Package and unit names are not confirmed by SUSE "
    "documentation yet, so this only acts on what it finds.",
    distros=("suse", "opensuse", "sles"),
    packages=("mcphost",),
    units=("mcphost.service",),
    experimental=True)

DEEPIN_AI = ServiceModule(
    "deepin-uos-ai", "deepin UOS AI",
    "Masks services shipped by the UOS AI packages. Unit names come from the package database, "
    "because deepin does not document them.",
    distros=("deepin",),
    packages=("uos-ai", "uos-ai-rag", "deepin-modelhub"),
    experimental=True)

LOCAL_LLM = ServiceModule(
    "local-llm-services", "Local LLM servers",
    "Masks system services of local LLM servers: ollama, llama.cpp, LocalAI, Open WebUI.",
    packages=("ollama", "llama-cpp", "llama-cpp-*", "open-webui", "local-ai", "localai"),
    units=("ollama.service", "llama-cpp.service", "llama-server.service",
           "local-ai.service", "localai.service", "open-webui.service"))


# Canonical inference snaps, 2026-11-02 (docs/catalog.md). The list grows; a
# snap we do not know about simply is not touched.
INFERENCE_SNAPS = (
    "clef-flash", "deepseek-r1", "gemma3", "gemma4", "glm-4-7-flash", "glm-ocr",
    "nemotron-3-nano", "nemotron-3-nano-omni", "nemotron-3-5-bolt",
    "nemotron-3-5-lightning", "nemotron-3-5-super-vl", "nomic-embed-text-v1-5",
    "openjev", "phi4", "qwen-vl", "qwen3", "qwen3-coder", "qwen3-5", "qwen3-6",
    "qwen3-8", "smollm2",
)


class UbuntuInference(Module):
    def __init__(self) -> None:
        super().__init__(
            "ubuntu-inference", "Ubuntu inference snaps",
            "Disables Canonical's local-model inference snaps (gemma3, deepseek-r1, qwen, ...). "
            "Disabling keeps the data; --purge removes the snaps.")

    def applies(self, ctx: Context) -> bool:
        return ctx.is_distro("ubuntu") or ctx.path("/var/lib/snapd").exists()

    def control(self, ctx: Context) -> str:
        return "snap disable"

    def actions(self, ctx: Context) -> List[Action]:
        return [SnapDisable(n) for n in INFERENCE_SNAPS]

    def purge_actions(self, ctx: Context) -> List[Action]:
        return [SnapRemove(n) for n in INFERENCE_SNAPS]
