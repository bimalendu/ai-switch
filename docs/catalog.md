# Feature catalog and evidence

Researched 2026-11-02 (Phase 0). Each module below records what was verified, from which source, and what is still unverified. Anything unverified ships as **experimental**: it only acts on things it can find on the system, and `status` flags it.

Evidence levels:
- **verified**: read in the vendor's own documentation or source on the research date.
- **partial**: some of the mechanism is confirmed, the rest is inferred.
- **unverified**: not confirmed. The module is `experimental`.

## firefox: verified

- Policy `AIControls`, per-feature `{"Value": "available" | "blocked", "Locked": bool}`.
- Keys: `Default`, `Translations`, `PDFAltText`, `SmartTabGroups`, `LinkPreviewKeyPoints`, `SidebarChatbot`, `SmartWindow`, `SpeechRecognition`.
- `blocked` disables and hides the feature; for on-device AI the downloaded models are removed.
- First version 149.0.2. The policy reaches ESR in 153 (Mozilla admin docs). An eWEEK piece disagrees on ESR timing, so ai-switch validates against the installed build instead of trusting a version number.
- Source: https://firefox-admin-docs.mozilla.org/reference/policies/aicontrols/
- Policy file paths per build are **partial**. Fedora reads `<install dir>/distribution/policies.json` (proven in `quill-os/docs/no-ai-policy.md`). Debian/Ubuntu `/etc/firefox/policies/policies.json` and `/etc/firefox-esr/policies/` are confirmed only by user reports (Mozilla support thread 1456607). Snap and Flatpak paths are not confirmed. The module looks for the existing files and directories, and on the unconfirmed layouts it reports `unknown` instead of writing.

## thunderbird: dropped from v1

No AI policy exists in the Thunderbird policy templates (ESR 68 to 128 checked). The only related item is Bugzilla 2068701, a `DisableChat` policy for the Chat feature, which is not AI. Revisit when Thunderbird ships an AI feature. See https://thunderbird.github.io/policy-templates/.

## chromium-family: verified for Chrome policy names, partial for the rest

Policy definitions read from `chromium/components/policy/resources/templates/policy_definitions/GenerativeAI/` on 2026-11-02. Value meanings (all integers):

| Policy | Disabling value | Note |
|---|---|---|
| `GenAiDefaultSettings` | 2 | **Not written.** Documented as cloud-only, so Chrome would flag it as an error when set from a file. The individual policies below cover the same features. |
| `GeminiSettings` | 1 | Chrome 137+ (Windows/macOS listed; Linux not named) |
| `AIModeSettings` | 1 | Chrome 138+, all platforms |
| `HelpMeWriteSettings` | 2 | Chrome 121+ |
| `TabCompareSettings` | 2 | Chrome 129+ |
| `CreateThemesSettings` | 2 | Chrome 121+ |
| `DevToolsGenAiSettings` | 2 | |
| `GenAILocalFoundationalModelSettings` | 1 | Chrome 124+. Stops the on-device model download and removes the existing copy |
| `SearchContentSharingSettings` | 1 | Chrome 144+ |
| `GeminiActOnWebSettings` | 1 | Chrome 143+ |
| `ThirdPartyAiChatSettings` | 1 | Chrome 151+ |

Skipped on purpose: `TabOrganizerSettings` (deprecated, sunset), `HistorySearchSettings` (deprecated, removed in M157), and `HelpMeReadSettings`, `GenAISmartGroupingSettings`, `GenAIWallpaperSettings` (ChromeOS only).

Unknown policies are ignored by Chrome, so writing a key an older build doesn't know is harmless. Chrome's `chrome.*` platform wildcard probably covers Linux, but Linux is not named in the source. The Chrome entry is therefore verified on names and values, and partial on Linux scope.

Managed policy directories on Linux (partial, from vendor convention): Chrome `/etc/opt/chrome/policies/managed/`, Chromium `/etc/chromium/policies/managed/`, Edge `/etc/opt/edge/policies/managed/`, Brave `/etc/brave/policies/managed/` (Brave help article confirms the Brave path). Vivaldi is **not in v1**: its managed-policy path was not confirmed.

Edge (**experimental**): `GenAILocalFoundationalModelSettings` 1 (documented, Windows/macOS only), `HubsSidebarEnabled` false (Windows/macOS documented), `CopilotPageContext` false, `Microsoft365CopilotChatIconEnabled` false (the only toolbar Copilot control since Edge 141). The Edge pages do not document Linux.

Brave (**experimental**): `BraveAIChatEnabled` (Brave's table says 0 or 1; a community report says `0` did not hide Leo, so the module writes the boolean `false`), plus `GenAILocalFoundationalModelSettings` 1 and `DevToolsGenAiSettings` 2. Check `brave://policy`.

Sources:
- https://chromium.googlesource.com/chromium/src/+/main/components/policy/resources/templates/policy_definitions/GenerativeAI/ (GitHub mirror read)
- https://learn.microsoft.com/en-us/deployedge/microsoft-edge-policies/GenAILocalFoundationalModelSettings
- https://learn.microsoft.com/en-ca/DeployEdge/microsoft-edge-policies/hubssidebarenabled
- https://support.brave.app/hc/en-us/articles/360039248271 (search result; page returned 403 to fetch)
- https://justthebrowser.com/brave

Implementation: `modules/chromium.py` writes `<managed dir>/ai-switch.json` only for browsers whose binary is found. A matching file is reported as `off (locked)` because managed policies are mandatory. Other files in the same directory that set the same keys are not evaluated.

## rhel-lightspeed: partial

- Package `command-line-assistant`, daemon `clad`, unit `clad.service` (Copr spec files for `command-line-assistant` 0.1.0 to 0.3.0). A later changelog says the units became socket-activated, so a `clad.socket` may exist. The module masks `clad.service` and `clad.socket` if present, plus any units the package database says `command-line-assistant` ships.
- Red Hat docs: `systemctl restart clad`, config in `/etc/xdg/command-line-assistant/config.toml`.
- The offline variant (`rhel-cla`, `rhel-lightspeed` command) runs containers and can install an optional systemd service. Its unit name was not found. Not covered in v1.
- Sources: https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/9/html/interacting_with_the_command-line_assistant_powered_by_rhel_lightspeed/ and the Copr spec at https://copr-be.cloud.fedoraproject.org/results/%40rhel-lightspeed/command-line-assistant/epel-9-x86_64/08702030-command-line-assistant/command-line-assistant.spec

## suse-ai: unverified (experimental)

SLES 16 (GA 2025-11-04) ships MCP host and server components as a technology preview, with AI-assisted management in Cockpit. No official page names the packages or units. A third-party Trento guide installs `mcphost` with zypper. The module therefore only acts on `mcphost.service` and units shipped by an installed `mcphost` package, and only if they exist. Sources: https://www.suse.com/products/server/ai, https://emaksy.github.io/docs/mcp-server/Trento%20MCP%20Server%20documentation/integration-sles.html. Must be confirmed with `rpm -ql` on a real SLES 16 system.

## ubuntu-inference: verified for snap names, unverified for services

Canonical's inference snaps (announced 2025-10-23, 21 snaps listed on 2026-11-02): clef-flash, deepseek-r1, gemma3, gemma4, glm-4-7-flash, glm-ocr, nemotron-3-nano, nemotron-3-nano-omni, nemotron-3-5-bolt, nemotron-3-5-lightning, nemotron-3-5-super-vl, nomic-embed-text-v1-5, openjev, phi4, qwen-vl, qwen3, qwen3-coder, qwen3-5, qwen3-6, qwen3-8, smollm2. The list changes often, so the module keeps its own snap list and reads the repo list only in docs. Disabling is `snap disable <name>`, which stops the snap's services and keeps its data. Service names and ports were not documented. Source: https://github.com/canonical/inference-snaps

## deepin-uos-ai: partial (experimental)

Package names `uos-ai`, `deepin-modelhub`, `uos-ai-rag` (guide at kitemetric.com). No systemd unit or autostart entry name was found. The module masks the system units that the package database lists for those three packages (`dpkg-query -L`), so it does not guess names. Autostart entries are **not** handled yet. Must be confirmed with `dpkg -L uos-ai` on deepin.

## local-llm-services: partial

System units for ollama (`ollama.service`, the unit shipped by the upstream installer and Fedora/Arch packages), llama.cpp (`llama-cpp.service`, `llama-server.service`), LocalAI (`local-ai.service`), and open-webui (`open-webui.service`). Unit names come from common packaging and are not verified against a specific package. The module masks only units that exist, plus units shipped by installed packages of those names. Known gap: if an admin or the upstream installer wrote the unit as a real file in `/etc/systemd/system` (the ollama install script does), ai-switch refuses to replace it with a mask and says so. Use `systemctl disable --now` yourself in that case.

## known-ai-apps: seeded from quill-os

Denylist `data/known-ai-apps.txt` is seeded from `quill-os/policy/ai-denylist.txt` (decided 2026-10-30) plus the inference snap names above. Report-only. Removal needs `--purge`.

## Phase 4 results (2026-11-02)

`make test-containers` ran `tests/containers/matrix.py` in one container per distro, driving the real CLI against synthetic installs (fake unit files, a fake Firefox with a policy schema, a fake Chrome) with the distro's real package manager answering queries. All six passed: Fedora 44, Rocky 9.3 (Python 3.9.18, the minimum supported), Ubuntu 24.04, Debian 13, openSUSE Tumbleweed, Arch (amd64 emulation, as Arch publishes no arm64 image).

What this confirms: distro and package-manager detection, real `rpm`/`dpkg-query`/`pacman` queries, status, dry-run, disable, reconcile after a simulated update, enable, and revert leaving `/etc`, `/opt`, `/usr/lib/systemd` and the Firefox dirs byte-identical. On Arch, the real `systemctl --root=/ is-enabled` reported the masked unit as `masked`, and `pacman -Ql` unit discovery worked. The matrix found one real bug (a pre-existing Firefox policy file was not restored byte-for-byte after a toggle with a different `--lock`), fixed with a unit test.

What it does **not** confirm: that real Firefox or Chrome honour the files (the Marionette and `chrome://policy` probes are not built), real `clad`, `ollama`, `mcphost` or `uos-ai` packages, a booted systemd (the containers have none, so `systemctl stop` and `daemon-reload` are not exercised), and `snap` behaviour. Those need a VM or real hosts.

## Gaps for a later run

- Real packages and booted systemd/snapd (containers cannot cover them), and browser probes.
- SUSE, deepin and Edge-on-Linux need a real install to confirm.
- Firefox snap policy path (written to `/etc/firefox/policies/` as an experimental best guess) and Flatpak (detected, reported `unknown`, never written).
- deepin autostart entries; `/etc`-resident vendor units (see local-llm-services).
