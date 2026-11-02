#!/bin/sh
# Inside a container: install GTK4/libadwaita + Xvfb, check the polkit policy
# with pkaction, then drive the real window (gui_test.py).
set -u
pm="$1"
. /etc/os-release
echo "== $ID $VERSION_ID pm=$pm"
if ! python3 -c 'import gi' 2>/dev/null; then
case "$pm" in
    dnf) dnf -y install python3 python3-gobject gtk4 libadwaita xorg-x11-server-Xvfb polkit 2>&1 | tail -4 ;;
    apt) apt-get update -qq >/dev/null 2>&1
         DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 xvfb polkitd pkexec >/dev/null 2>&1 ;;
esac
fi
python3 -c 'import gi; gi.require_version("Adw","1"); from gi.repository import Adw' || { echo "FAIL  libadwaita not importable"; exit 1; }

install -Dm644 /src/data/org.aiswitch.policy /usr/share/polkit-1/actions/org.aiswitch.apply.policy
# pkaction talks to polkitd over the system bus, so start a private one.
mkdir -p /run/dbus
rm -f /run/dbus/pid
if command -v dbus-daemon >/dev/null 2>&1 && dbus-daemon --system --fork 2>/dev/null; then
    for d in /usr/lib/polkit-1/polkitd /usr/libexec/polkitd; do
        [ -x "$d" ] && { "$d" --no-debug >/tmp/polkitd.log 2>&1 & break; }
    done
    sleep 3
fi
if out=$(timeout 30 pkaction --action-id org.aiswitch.apply --verbose 2>&1); then
    echo "$out" | grep -q 'implicit active:.*auth_admin_keep' && echo "PASS  polkitd loaded the policy (active session: auth_admin_keep)" || echo "FAIL  pkaction: $out"
    echo "$out" | grep -q 'org.freedesktop.policykit.exec.path -> /usr/libexec/ai-switch-helper' && echo "PASS  exec.path is the helper" || echo "FAIL  exec.path missing"
else
    echo "SKIP  polkitd not available in this container ($out)"
fi

export SHOTS=/out GSK_RENDERER=cairo GDK_BACKEND=x11 NO_AT_BRIDGE=1
Xvfb :99 -screen 0 1100x900x24 >/tmp/xvfb.log 2>&1 &
sleep 2
export DISPLAY=:99
exec python3 -u /src/tests/containers/gui_test.py
