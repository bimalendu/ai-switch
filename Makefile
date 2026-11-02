PYTHON ?= python3
PODMAN ?= podman
VERSION := $(shell $(PYTHON) -c 'import re;print(re.search(r"__version__ = .(.+?).$$", open("ai_switch/__init__.py").read(), re.M).group(1))')

.PHONY: test lint

test: lint
	$(PYTHON) -m unittest discover -s tests/unit -t . -v

lint:
	$(PYTHON) -m compileall -q ai_switch tests

# Needs podman. PODMAN="podman --connection NAME" selects a machine;
# CONTAINERS="fedora ubuntu" limits the distros (default: all six).
.PHONY: test-containers test-hooks
test-containers:
	PODMAN="$(PODMAN)" sh tests/containers/run-matrix.sh $(CONTAINERS)

# Real package-manager hooks: installs a package in each container and checks the hook fired.
test-hooks:
	PODMAN="$(PODMAN)" SCRIPT=hooks.sh sh tests/containers/run-matrix.sh $(or $(CONTAINERS),fedora rocky9 ubuntu debian opensuse arch)

# Install the reconcile hooks and units. HOOKS picks the package managers
# ("dnf5 dnf4 apt pacman zypper"); each distro package installs only its own.
# DNF4_PLUGDIR overrides the dnf 4 plugin directory (default: asked from dnf itself).
HOOKS ?= dnf5 dnf4 apt pacman zypper
.PHONY: install-hooks
install-hooks:
	install -Dm755 hooks/ai-switch-hook $(DESTDIR)/usr/libexec/ai-switch-hook
	install -Dm644 systemd/ai-switch-reconcile.service $(DESTDIR)/usr/lib/systemd/system/ai-switch-reconcile.service
	install -Dm644 systemd/ai-switch-reconcile.timer $(DESTDIR)/usr/lib/systemd/system/ai-switch-reconcile.timer
	for h in $(HOOKS); do case $$h in \
	  dnf5)   install -Dm644 hooks/dnf5/ai-switch.actions $(DESTDIR)/etc/dnf/libdnf5-plugins/actions.d/ai-switch.actions ;; \
	  dnf4)   install -Dm644 hooks/dnf4/ai_switch.py $(DESTDIR)$${DNF4_PLUGDIR:-$$(python3 -c 'import dnf.plugin,os;print(os.path.join(os.path.dirname(os.path.dirname(dnf.plugin.__file__)),"dnf-plugins"))')}/ai_switch.py; \
	          install -Dm644 hooks/dnf4/ai-switch.conf $(DESTDIR)/etc/dnf/plugins/ai-switch.conf ;; \
	  apt)    install -Dm644 hooks/apt/99ai-switch $(DESTDIR)/etc/apt/apt.conf.d/99ai-switch ;; \
	  pacman) install -Dm644 hooks/pacman/ai-switch.hook $(DESTDIR)/usr/share/libalpm/hooks/ai-switch.hook ;; \
	  zypper) install -Dm755 hooks/zypper/ai-switch $(DESTDIR)/usr/lib/zypp/plugins/commit/ai-switch ;; \
	  *) echo "unknown hook set $$h"; exit 1 ;; esac; done

# The GUI's privileged helper, polkit policy and launcher. The GUI runs the helper
# through pkexec; polkit only allows that one executable (data/org.aiswitch.policy).
.PHONY: install-gui
install-gui:
	install -Dm755 bin/ai-switch-gui $(DESTDIR)/usr/bin/ai-switch-gui
	install -Dm755 bin/ai-switch-helper $(DESTDIR)/usr/libexec/ai-switch-helper
	install -Dm644 data/org.aiswitch.policy $(DESTDIR)/usr/share/polkit-1/actions/org.aiswitch.apply.policy
	install -Dm644 data/org.aiswitch.AiSwitch.desktop $(DESTDIR)/usr/share/applications/org.aiswitch.AiSwitch.desktop

# The command line tool: the Python package (GUI and helper modules included, they
# only import GTK when launched) and /usr/bin/ai-switch. PYLIB is where the distro
# keeps pure-Python packages (rpm: %{python3_sitelib}, Debian: /usr/lib/python3/dist-packages).
PYLIB ?= $(shell $(PYTHON) -c 'import sysconfig;print(sysconfig.get_path("purelib"))')
.PHONY: install-core install
install-core:
	install -d $(DESTDIR)$(PYLIB)/ai_switch/actions $(DESTDIR)$(PYLIB)/ai_switch/modules $(DESTDIR)$(PYLIB)/ai_switch/data
	install -m644 ai_switch/*.py $(DESTDIR)$(PYLIB)/ai_switch/
	install -m644 ai_switch/actions/*.py $(DESTDIR)$(PYLIB)/ai_switch/actions/
	install -m644 ai_switch/modules/*.py $(DESTDIR)$(PYLIB)/ai_switch/modules/
	install -m644 ai_switch/data/*.txt $(DESTDIR)$(PYLIB)/ai_switch/data/
	install -Dm755 bin/ai-switch $(DESTDIR)/usr/bin/ai-switch

# Everything, for a manual install: make install HOOKS=apt
install: install-core install-hooks install-gui

# Source tarball for the packages (working tree, so uncommitted changes are tried too):
# dist/ai-switch-VERSION.tar.gz with a top-level ai-switch-VERSION/.
.PHONY: dist
dist:
	$(PYTHON) packaging/mkdist.py

# Build and install the three packages in containers (needs podman; see tests/containers/packages.sh).
.PHONY: test-packages
test-packages: dist
	PODMAN="$(PODMAN)" SCRIPT=packages.sh sh tests/containers/run-matrix.sh $(or $(CONTAINERS),fedora rocky9 ubuntu debian opensuse arch)
