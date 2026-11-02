#!/bin/sh
# Inside a container: build the distro's package(s) from dist/ai-switch-VERSION.tar.gz,
# install them, and check the installed result end to end:
#   files in the right places, the hook of this package manager only, units not enabled,
#   `ai-switch disable` + a real package transaction (the hook restores a deleted mask),
#   and a clean removal. Needs `make dist` on the host first.
set -u
pm="$1"
src=/src
fail=0
ok()   { echo "PASS  $1"; }
bad()  { echo "FAIL  $1"; fail=1; }
info() { echo "info  $1"; }
check() { if eval "$2"; then ok "$1"; else bad "$1  [$2]"; fi; }

. /etc/os-release
echo "== $ID $VERSION_ID pm=$pm"

tarball=$(ls "$src"/dist/ai-switch-*.tar.gz 2>/dev/null | head -1)
[ -n "$tarball" ] || { echo "FAIL  no dist/ai-switch-*.tar.gz (run make dist)"; exit 1; }
b=/tmp/build; rm -rf $b; mkdir -p $b; cd $b
tar xzf "$tarball"; srcdir=$(ls -d $b/ai-switch-*/)

# --- build ---------------------------------------------------------------------
case "$pm" in
dnf|zypper)
    if [ "$pm" = dnf ]; then
        dnf -y -q install rpm-build make python3-devel systemd-rpm-macros >/dev/null 2>&1 || { bad "build deps"; exit 1; }
        ginstall() { dnf -y -q install "$@" 2>&1 | tail -5; }
    else
        zypper -n --quiet install rpm-build make python3-devel systemd-rpm-macros >/dev/null 2>&1 || { bad "build deps"; exit 1; }
        ginstall() { zypper -n --quiet --no-gpg-checks install --allow-unsigned-rpm "$@" 2>&1 | tail -5; }
    fi
    mkdir -p $b/rpm/SOURCES; cp "$tarball" $b/rpm/SOURCES/
    if rpmbuild -ba --define "_topdir $b/rpm" "$srcdir/packaging/ai-switch.spec" >$b/build.log 2>&1; then
        ok "rpmbuild"
    else
        tail -30 $b/build.log; bad "rpmbuild"; exit 1
    fi
    grep -E "^Ran [0-9]+ tests?|^OK$|^FAILED" $b/build.log | head -3 | sed "s/^/info  build check: /"
    pkgs=$(ls $b/rpm/RPMS/noarch/*.rpm); info "built: $(basename -a $pkgs | tr '\n' ' ')"
    core_pkg=$(ls $b/rpm/RPMS/noarch/ai-switch-0*.rpm); gui_pkg=$(ls $b/rpm/RPMS/noarch/ai-switch-gui-*.rpm)
    inst() { ginstall "$@"; }
    erase() { if [ "$pm" = dnf ]; then dnf -y remove "$@"; else zypper -n remove "$@"; fi; }
    pkg_other() { if [ "$pm" = dnf ]; then dnf -y -q install "$1" >/dev/null 2>&1; else zypper -n --quiet install "$1" >/dev/null 2>&1; fi; }
    ;;
apt)
    apt-get update -qq >/dev/null 2>&1
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq build-essential debhelper dh-python python3 fakeroot >/dev/null 2>&1 || { bad "build deps"; exit 1; }
    cp -r "$srcdir/packaging/debian" "$srcdir/debian"
    if (cd "$srcdir" && dpkg-buildpackage -us -uc -b) >$b/build.log 2>&1; then ok "dpkg-buildpackage"; else tail -30 $b/build.log; bad "dpkg-buildpackage"; exit 1; fi
    grep -E "^Ran [0-9]+ tests?|^OK$|^FAILED" $b/build.log | head -3 | sed "s/^/info  build check: /"
    info "built: $(ls $b/*.deb | xargs -n1 basename | tr '\n' ' ')"
    core_pkg=$(ls $b/ai-switch_*.deb); gui_pkg=$(ls $b/ai-switch-gui_*.deb)
    inst() { DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$@" 2>&1 | tail -5; }
    erase() { DEBIAN_FRONTEND=noninteractive apt-get purge -y -qq "$@" 2>&1; }
    pkg_other() { DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$1" >/dev/null 2>&1; }
    ;;
pacman)
    pacman -Sy --noconfirm --disable-sandbox make python fakeroot binutils sudo >/dev/null 2>&1 || { bad "build deps"; exit 1; }
    useradd -m builder 2>/dev/null
    mkdir -p $b/arch; cp "$srcdir"/packaging/arch/* $b/arch/; cp "$tarball" $b/arch/ai-switch-0.1.0.tar.gz
    chown -R builder $b/arch
    if su builder -c "cd $b/arch && makepkg -f --nodeps" >$b/build.log 2>&1; then ok "makepkg"; else tail -30 $b/build.log; bad "makepkg"; exit 1; fi
    grep -E "^Ran [0-9]+ tests?|^OK$|^FAILED" $b/build.log | head -3 | sed "s/^/info  build check: /"
    info "built: $(ls $b/arch/*.pkg.tar.* | xargs -n1 basename | tr '\n' ' ')"
    core_pkg=$(ls $b/arch/ai-switch-0*.pkg.tar.*); gui_pkg=$(ls $b/arch/ai-switch-gui-*.pkg.tar.*)
    inst() { pacman -U --noconfirm --disable-sandbox "$@" 2>&1 | tail -5; }
    erase() { pacman -R --noconfirm --disable-sandbox "$@" 2>&1; }
    pkg_other() { pacman -Sy --noconfirm --disable-sandbox "$1" >/dev/null 2>&1; }
    ;;
esac

# --- install the core package first (the GUI one is heavy) ----------------------
inst "$core_pkg" >/dev/null
check "ai-switch installed" "[ -x /usr/bin/ai-switch ]"
case "$pm" in dnf|zypper) check "rpm scriptlets have no unexpanded macros" "! rpm -q --scripts ai-switch | grep -q '%'" ;; esac
check "ai-switch status runs from the package" "ai-switch status >/dev/null"
check "ai-switch --json status is valid JSON" "ai-switch --json status | python3 -c 'import json,sys; json.load(sys.stdin)'"
check "shebang kept the isolated flag" "head -1 /usr/bin/ai-switch | grep -q 'python3'"
check "hook helper installed" "[ -x /usr/libexec/ai-switch-hook ]"
check "reconcile service installed" "[ -f /usr/lib/systemd/system/ai-switch-reconcile.service ] || [ -f /lib/systemd/system/ai-switch-reconcile.service ]"
check "reconcile timer installed" "[ -f /usr/lib/systemd/system/ai-switch-reconcile.timer ] || [ -f /lib/systemd/system/ai-switch-reconcile.timer ]"
check "timer and service are NOT enabled" "! ls /etc/systemd/system/*.wants/ai-switch-reconcile.* >/dev/null 2>&1"
check "no GUI files from the core package" "[ ! -e /usr/bin/ai-switch-gui ] && [ ! -e /usr/libexec/ai-switch-helper ]"

# Which hooks are present: this package manager's only.
case "$pm:$ID" in
    dnf:fedora) mine=/etc/dnf/libdnf5-plugins/actions.d/ai-switch.actions ;;
    dnf:*)      mine=$(ls /usr/lib/python3*/site-packages/dnf-plugins/ai_switch.py 2>/dev/null | head -1) ;;
    apt:*)      mine=/etc/apt/apt.conf.d/99ai-switch ;;
    pacman:*)   mine=/usr/share/libalpm/hooks/ai-switch.hook ;;
    zypper:*)   mine=/usr/lib/zypp/plugins/commit/ai-switch ;;
esac
check "this distro's hook is installed ($mine)" "[ -n '$mine' ] && [ -e '$mine' ]"
others=0
for f in /etc/dnf/libdnf5-plugins/actions.d/ai-switch.actions /etc/apt/apt.conf.d/99ai-switch /usr/share/libalpm/hooks/ai-switch.hook /usr/lib/zypp/plugins/commit/ai-switch /etc/dnf/plugins/ai-switch.conf; do
    [ -e "$f" ] && [ "$f" != "$mine" ] && [ "$f" != /etc/dnf/plugins/ai-switch.conf ] && others=$((others+1)) && info "unexpected: $f"
done
check "no other package manager's hook is installed" "[ $others = 0 ]"

# --- end to end: disable, lose the mask, a real transaction brings it back --------
mkdir -p /usr/lib/systemd/system /etc/systemd/system
printf '[Unit]\nDescription=fixture\n' > /usr/lib/systemd/system/ollama.service
out=$(ai-switch disable local-llm-services --yes 2>&1); r=$?
check "ai-switch disable local-llm-services" "[ $r = 0 ]"
check "ollama is masked" "[ \"\$(readlink /etc/systemd/system/ollama.service)\" = /dev/null ]"
rm -f /etc/systemd/system/ollama.service
check "mask removed by hand (simulating an update)" "[ ! -e /etc/systemd/system/ollama.service ] && [ ! -L /etc/systemd/system/ollama.service ]"
pkg_other tree
check "a real package transaction re-applied the mask via the hook" "[ \"\$(readlink /etc/systemd/system/ollama.service)\" = /dev/null ]"

# --- GUI package ----------------------------------------------------------------
out=$(inst "$gui_pkg" "$core_pkg" 2>&1); r=$?
[ $r = 0 ] || echo "$out"
check "ai-switch-gui installed" "[ -x /usr/bin/ai-switch-gui ] && [ -x /usr/libexec/ai-switch-helper ]"
check "polkit policy installed" "[ -f /usr/share/polkit-1/actions/org.aiswitch.apply.policy ]"
check "desktop file installed" "[ -f /usr/share/applications/org.aiswitch.AiSwitch.desktop ]"
check "polkit policy is valid XML" "python3 -c 'import xml.dom.minidom as m; m.parse(\"/usr/share/polkit-1/actions/org.aiswitch.apply.policy\")'"
adw=$(python3 -c 'import gi; gi.require_version("Gtk","4.0"); gi.require_version("Adw","1"); from gi.repository import Adw; print("%d.%d.%d" % (Adw.get_major_version(), Adw.get_minor_version(), Adw.get_micro_version()))' 2>&1)
check "GTK 4 and libadwaita import from the packaged dependencies (libadwaita $adw)" "echo '$adw' | grep -Eq '^[0-9]+\\.[0-9]+\\.[0-9]+$'"
check "ai_switch.gui imports" "python3 -c 'import ai_switch.gui'"
check "helper refuses --all as root" "! /usr/libexec/ai-switch-helper disable --all --yes >/dev/null 2>&1"

# --- removal ----------------------------------------------------------------------
case "$pm" in
    dnf|zypper) rmnames="ai-switch-gui ai-switch" ;;
    apt)        rmnames="ai-switch-gui ai-switch" ;;
    pacman)     rmnames="ai-switch-gui ai-switch" ;;
esac
out=$(erase $rmnames 2>&1); r=$?
[ $r = 0 ] || echo "$out"
check "packages removed cleanly" "[ $r = 0 ]"
check "removal warned that the changes stay" "echo \"\$out\" | grep -q 'keeps the changes it made'"
check "no package files left behind" "[ ! -e /usr/bin/ai-switch ] && [ ! -e /usr/libexec/ai-switch-hook ] && [ ! -e /usr/bin/ai-switch-gui ] && [ ! -e $mine ]"
check "the user's change (mask) is left in place" "[ \"\$(readlink /etc/systemd/system/ollama.service)\" = /dev/null ]"

echo; echo "fail=$fail"
exit $fail
