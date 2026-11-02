# Usage

## See what is there

```
$ ai-switch status
MODULE              STATE        DETAIL
firefox             on           Firefox (/usr/lib64/firefox): no AIControls block
chromium-family     not present  Chrome, Chromium, Edge and Brave AI features
local-llm-services  on           ollama.service: installed, not masked
known-ai-apps       on           installed: ollama [report-only]
```

States: `on`, `off`, `off (locked)` (users cannot change it), `not present`, `unknown` (found, but ai-switch cannot tell or does not manage it, for example Firefox as a Flatpak).

## Turn things off

```
ai-switch disable firefox --dry-run     # show the plan, change nothing
sudo ai-switch disable firefox
sudo ai-switch disable --all --lock
```

The plan is always printed first. Without a terminal you must pass `--yes`.

`--lock` makes the Firefox policy `Locked`. Browser managed policies (Chromium family) are always enforced.

`--purge` additionally removes the packages, snaps and Flatpaks the module knows about. It cannot be reverted by ai-switch; `revert` prints the command that reinstalls them.

## Turn things on, or put things back

```
sudo ai-switch enable firefox     # actively sets AI to 'available' / unmasks the units
sudo ai-switch revert firefox     # restores the state before ai-switch touched it
sudo ai-switch revert             # everything
```

`enable` is a choice ("I want it on"). `revert` is an undo ("as if ai-switch was never run"), and removes ai-switch's files and directories again.

## After updates

A package update can replace a policy file or unit. `ai-switch reconcile` re-applies every "off" choice that was undone. Package-manager hooks that run it automatically are planned for a later phase; until then run it yourself or from a cron job or systemd timer.

## Offline images

```
sudo ai-switch --root /mnt/image disable --all --yes
```

Only file-based changes work offline (policy files and unit masks). Snap and package operations need a running system and report that clearly.

## Machine output

`--json` prints JSON for `status`, plans and results, and never prompts, so it needs `--yes` for changes.
