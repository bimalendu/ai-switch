# ai-switch

Explicitly turn system-level AI features on or off on major Linux distributions, and put things back exactly as they were.

Status: all phases of the plan are done (catalog, status, reversible disable/enable/revert/reconcile, modules, a six-distro container test matrix, package-manager hooks with a boot service and daily timer, a GTK4/libadwaita window with a polkit-guarded helper, and local rpm/deb/Arch packages built and installed in containers). Nothing is published or signed. See `docs/catalog.md` for what was verified and what is still experimental.

```
ai-switch status                       # read-only table of every AI feature
ai-switch disable firefox chromium-family --dry-run
sudo ai-switch disable --all --lock    # --lock: users cannot re-enable (Firefox)
sudo ai-switch enable firefox          # actively turn it on again
sudo ai-switch revert                  # put back what was there before ai-switch
sudo ai-switch reconcile               # re-apply choices after a package update
ai-switch-gui                          # the same, as a window (GTK4/libadwaita, asks for a password via polkit)
```

Global options: `--root DIR` works on an offline image or any directory tree, `--json` gives machine-readable output, `--yes` skips the confirmation prompt. Exit codes: 0 ok, 1 a change failed, 2 usage error, 3 needs root.

## Install

There is no package repository yet and nothing is signed, so you build the package from this source tree and install the file. Each distro gets two packages: `ai-switch` (CLI plus the hook for that distro's package manager) and `ai-switch-gui` (the window; optional, pulls in GTK 4 and libadwaita). The steps below are the ones `make test-packages` runs in containers.

Every route starts the same way. From a checkout of this repo, make the source tarball (only `python3` is needed):

```
make dist                      # writes dist/ai-switch-0.1.0.tar.gz
```

Installing never turns anything off, and never enables the reconcile timer or boot service. Removing the package keeps whatever ai-switch changed (run `sudo ai-switch revert` first to undo it).

### Fedora

Tested on Fedora 44. Uses the dnf 5 actions hook.

```
sudo dnf install rpm-build make python3-devel systemd-rpm-macros
mkdir -p ~/rpmbuild/SOURCES && cp dist/ai-switch-0.1.0.tar.gz ~/rpmbuild/SOURCES/
rpmbuild -ba packaging/ai-switch.spec
sudo dnf install ~/rpmbuild/RPMS/noarch/ai-switch-0.1.0-*.rpm            # CLI
sudo dnf install ~/rpmbuild/RPMS/noarch/ai-switch-gui-0.1.0-*.rpm        # optional window
```

### RHEL, CentOS Stream, Rocky, AlmaLinux

Tested on Rocky 9. Uses the dnf 4 plugin. The same spec file builds for all of them; the `ai-switch-0.1.0-*` glob matches the CLI package only, not the GUI one. RHEL 10 and the GUI on older libadwaita have not been tried.

```
sudo dnf install rpm-build make python3-devel systemd-rpm-macros
mkdir -p ~/rpmbuild/SOURCES && cp dist/ai-switch-0.1.0.tar.gz ~/rpmbuild/SOURCES/
rpmbuild -ba packaging/ai-switch.spec
sudo dnf install ~/rpmbuild/RPMS/noarch/ai-switch-0.1.0-*.rpm
sudo dnf install ~/rpmbuild/RPMS/noarch/ai-switch-gui-0.1.0-*.rpm        # optional window
```

### Ubuntu, Debian (and Mint, Pop!_OS)

Tested on Ubuntu 24.04 and Debian 13. Uses an apt `DPkg::Post-Invoke` hook. The `.deb` files are written to the directory above the source tree.

```
sudo apt install build-essential debhelper dh-python python3 fakeroot
tar xzf dist/ai-switch-0.1.0.tar.gz && cd ai-switch-0.1.0
cp -r packaging/debian debian
dpkg-buildpackage -us -uc -b
cd ..
sudo apt install ./ai-switch_*_all.deb
sudo apt install ./ai-switch-gui_*_all.deb                          # optional window
```

### openSUSE

Tested on Tumbleweed. Uses a zypper commit plugin. SLES and Leap have not been tried.

```
sudo zypper install rpm-build make python3-devel systemd-rpm-macros
mkdir -p ~/rpmbuild/SOURCES && cp dist/ai-switch-0.1.0.tar.gz ~/rpmbuild/SOURCES/
rpmbuild -ba packaging/ai-switch.spec
sudo zypper --no-gpg-checks install --allow-unsigned-rpm ~/rpmbuild/RPMS/noarch/ai-switch-0.1.0-*.rpm
sudo zypper --no-gpg-checks install --allow-unsigned-rpm ~/rpmbuild/RPMS/noarch/ai-switch-gui-0.1.0-*.rpm   # optional window
```

### Arch Linux

Tested on x86_64. Uses a pacman alpm hook.

```
sudo pacman -S --needed base-devel python
mkdir /tmp/ai-switch-build && cp packaging/arch/* /tmp/ai-switch-build/
cp dist/ai-switch-0.1.0.tar.gz /tmp/ai-switch-build/
cd /tmp/ai-switch-build && makepkg -f
sudo pacman -U ai-switch-0*.pkg.tar.*
sudo pacman -U ai-switch-gui-*.pkg.tar.*                    # optional window
```

`makepkg` runs the unit tests as part of the build, and it must not be run as root.

### Any other distro, or just trying it

Python 3.9+ is all the CLI needs, and it runs straight from the checkout without installing anything:

```
./bin/ai-switch status
```

To install the files by hand, pick the hook for your package manager (`dnf5`, `dnf4`, `apt`, `pacman` or `zypper`; leave `HOOKS` out to install all of them). The GUI also needs GTK 4, libadwaita and PyGObject from your distro.

```
sudo make install HOOKS=apt              # or: make install-core install-hooks, without the GUI
```

### After installing

```
ai-switch status                         # read-only, safe to run first
sudo systemctl enable --now ai-switch-reconcile.timer ai-switch-reconcile.service   # optional: re-apply your choices after updates and at boot
```

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
make test              # compile check + unit tests against fake root trees (runs on macOS)
make test-containers   # needs podman: Fedora, Rocky 9, Ubuntu, Debian, openSUSE, Arch
make test-hooks        # same containers: real package installs trigger the reconcile hook
                       #   PODMAN="podman --connection NAME" CONTAINERS="fedora arch"
```

```
make dist              # dist/ai-switch-VERSION.tar.gz from the working tree
make test-packages     # build + install the rpm / deb / Arch packages in the containers
```

Packages: `packaging/ai-switch.spec`, `packaging/debian/`, `packaging/arch/PKGBUILD`. Each distro gets `ai-switch` (CLI and its package-manager hook) and `ai-switch-gui`.

Adding a module: `docs/adding-a-module.md`.
