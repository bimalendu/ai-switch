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

A package update can replace a policy file or unit. `ai-switch reconcile` re-applies every "off" choice that was undone. Three things run it for you, once installed (`make install-hooks`, or the packages in a later phase):

| Trigger | Where |
|---|---|
| right after every package transaction | dnf 5 actions (Fedora 41+), dnf 4 plugin (RHEL 9/10), apt `DPkg::Post-Invoke`, pacman hook, zypper commit plugin |
| daily | `ai-switch-reconcile.timer` |
| at boot | `ai-switch-reconcile.service` |

All of them go through `/usr/libexec/ai-switch-hook`, which does nothing unless ai-switch has changed something on the machine, never fails the package transaction, and logs to the journal under the tag `ai-switch`.

Installing the hooks does not switch the timer or boot service on. That is a system change, so you do it: `sudo systemctl enable --now ai-switch-reconcile.timer ai-switch-reconcile.service`.

Reconcile only re-applies "off" choices for modules you disabled. If you later install a new browser, the module's choice applies to it too.

## Offline images

```
sudo ai-switch --root /mnt/image disable --all --yes
```

Only file-based changes work offline (policy files and unit masks). Snap and package operations need a running system and report that clearly.

## Machine output

`--json` prints JSON for `status`, plans and results, and never prompts, so it needs `--yes` for changes.
