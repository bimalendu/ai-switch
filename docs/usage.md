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

A package update can replace a policy file or unit. `ai-switch reconcile` re-applies every "off" choice that was undone. Three things run it for you, once installed (`make install-hooks`, or the distro packages, which install only the hook for their own package manager):

| Trigger | Where |
|---|---|
| right after every package transaction | dnf 5 actions (Fedora 41+), dnf 4 plugin (RHEL 9/10), apt `DPkg::Post-Invoke`, pacman hook, zypper commit plugin |
| daily | `ai-switch-reconcile.timer` |
| at boot | `ai-switch-reconcile.service` |

All of them go through `/usr/libexec/ai-switch-hook`, which does nothing unless ai-switch has changed something on the machine, never fails the package transaction, and logs to the journal under the tag `ai-switch`.

Installing the hooks does not switch the timer or boot service on. That is a system change, so you do it: `sudo systemctl enable --now ai-switch-reconcile.timer ai-switch-reconcile.service`.

Reconcile only re-applies "off" choices for modules you disabled. If you later install a new browser, the module's choice applies to it too.

## The window

`ai-switch-gui` opens a window with one row per feature: a switch (on means the AI feature is on), a badge with its state, and a note when the module is experimental. Rows for features that are not installed, that ai-switch cannot read, or that are report-only have no working switch. Nothing changes until you press **Apply**. The menu has **Revert all changes…**. "Lock where possible" applies to Firefox only.

The window runs unprivileged and only reads `ai-switch --json status`. To change anything it runs `pkexec /usr/libexec/ai-switch-helper`, and polkit asks for an administrator password. The helper is not the full CLI: it takes module ids only, and refuses `--purge`, `--root`, `--all` and anything else, so authorising the GUI cannot remove software or touch other trees. Install the `ai-switch-gui` package, or the pieces with `make install-gui`; it needs GTK 4, libadwaita 1.2 or newer and PyGObject from your distro.

## Installing and removing

Build the packages locally with `make dist` and the files in `packaging/`: `rpmbuild -ba packaging/ai-switch.spec` with the tarball in `SOURCES/`, `dpkg-buildpackage -us -uc -b` after copying `packaging/debian` to `debian/`, or `makepkg` in `packaging/arch/` with the tarball beside the PKGBUILD. `make test-packages` does all of that in containers.

Installing never turns anything off, and never enables the timer or boot service. **Removing the package keeps what ai-switch changed** (masked units, policy files, the journal in `/var/lib/ai-switch`) and prints a notice. Run `sudo ai-switch revert` before removing it if you want everything back.

## Offline images

```
sudo ai-switch --root /mnt/image disable --all --yes
```

Only file-based changes work offline (policy files and unit masks). Snap and package operations need a running system and report that clearly.

## Machine output

`--json` prints JSON for `status`, plans and results, and never prompts, so it needs `--yes` for changes.
