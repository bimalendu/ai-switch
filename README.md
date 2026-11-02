# ai-switch

Explicitly turn system-level AI features on or off on major Linux distributions, and put things back exactly as they were.

Status: Phases 0 to 3 of the plan are done (catalog, status, reversible disable/enable/revert/reconcile, modules). The GUI, package-manager hooks, container test matrix and packaging are not built yet. See `docs/catalog.md` for what was verified and what is still experimental.

```
ai-switch status                       # read-only table of every AI feature
ai-switch disable firefox chromium-family --dry-run
sudo ai-switch disable --all --lock    # --lock: users cannot re-enable (Firefox)
sudo ai-switch enable firefox          # actively turn it on again
sudo ai-switch revert                  # put back what was there before ai-switch
sudo ai-switch reconcile               # re-apply choices after a package update
```

Global options: `--root DIR` works on an offline image or any directory tree, `--json` gives machine-readable output, `--yes` skips the confirmation prompt. Exit codes: 0 ok, 1 a change failed, 2 usage error, 3 needs root.

## What it controls

| Module | What it does |
|---|---|
| `firefox` | `AIControls` enterprise policy, checked against the installed Firefox's own schema |
| `chromium-family` | managed policy files for Chrome and Chromium (Edge and Brave experimental) |
| `rhel-lightspeed` | masks the `clad` daemon of the RHEL command-line assistant |
| `suse-ai` | masks SUSE's MCP host services (experimental) |
| `ubuntu-inference` | `snap disable` for Canonical's inference snaps |
| `deepin-uos-ai` | masks the services of the UOS AI packages (experimental) |
| `local-llm-services` | masks ollama, llama.cpp, LocalAI and Open WebUI services |
| `known-ai-apps` | report-only list of installed AI apps; `--purge` removes them |

A module that finds nothing on your system shows `not present`.

## Guarantees

- Nothing changes without a command. `status` never writes.
- Disabling is non-destructive: units are masked, policy files written, snaps disabled. Packages are removed only with `--purge`, which says so and cannot be reverted.
- Every change is journaled in `/var/lib/ai-switch/state.json` with the state before the first change. `revert` restores that state, even after many enable/disable toggles. Unit tests check that a full disable and revert leaves the file tree byte-identical.
- Shared policy files are merged, never replaced, and an existing admin file or unit that ai-switch cannot safely change is left alone with a clear error.

## Develop

Python 3.9+, standard library only.

```
make test        # compile check + unit tests against fake root trees (runs on macOS)
```

Adding a module: `docs/adding-a-module.md`.
