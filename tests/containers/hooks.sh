#!/bin/sh
# Inside a container: install the real package-manager hook for this distro,
# run a real install, and check the hook fired (and stays quiet without a journal).
# Uses a fake /usr/bin/ai-switch that only records its arguments.
set -u
pm="$1"
src=/src
log=/tmp/ai-switch-calls.log
fail=0
ok()  { echo "PASS  $1"; }
bad() { echo "FAIL  $1"; fail=1; }
calls() { [ -f "$log" ] && wc -l < "$log" | tr -d ' ' || echo 0; }

. /etc/os-release
echo "== $ID $VERSION_ID pm=$pm"

install -Dm755 "$src/hooks/ai-switch-hook" /usr/libexec/ai-switch-hook
printf '#!/bin/sh\necho "$@" >> %s\n' "$log" > /usr/bin/ai-switch
chmod 755 /usr/bin/ai-switch

case "$pm" in
    dnf)
        if dnf -y -q install libdnf5-plugin-actions >/dev/null 2>&1; then
            install -Dm644 "$src/hooks/dnf5/ai-switch.actions" /etc/dnf/libdnf5-plugins/actions.d/ai-switch.actions
        else
            # dnf 4: Python plugin in dnf's plugin directory.
            plugdir=$(python3 -c 'import dnf.plugin,os;print(os.path.join(os.path.dirname(os.path.dirname(dnf.plugin.__file__)),"dnf-plugins"))' 2>/dev/null)
            [ -n "$plugdir" ] || { echo "FAIL  cannot find the dnf 4 plugin directory"; exit 1; }
            install -Dm644 "$src/hooks/dnf4/ai_switch.py" "$plugdir/ai_switch.py"
            install -Dm644 "$src/hooks/dnf4/ai-switch.conf" /etc/dnf/plugins/ai-switch.conf
            echo "info  dnf 4 plugin installed to $plugdir"
        fi
        pkg_install() { dnf -y -q install "$1" >/dev/null 2>&1; } ;;
    apt)
        apt-get update -qq >/dev/null 2>&1
        install -Dm644 "$src/hooks/apt/99ai-switch" /etc/apt/apt.conf.d/99ai-switch
        pkg_install() { DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$1" >/dev/null 2>&1; } ;;
    pacman)
        install -Dm644 "$src/hooks/pacman/ai-switch.hook" /usr/share/libalpm/hooks/ai-switch.hook
        pkg_install() { pacman -Sy --noconfirm --disable-sandbox "$1" >/dev/null 2>&1; } ;;
    zypper)
        install -Dm755 "$src/hooks/zypper/ai-switch" /usr/lib/zypp/plugins/commit/ai-switch
        pkg_install() { zypper -n --quiet install "$1" >/dev/null 2>&1; } ;;
esac

rm -f "$log"
mkdir -p /var/lib/ai-switch && : > /var/lib/ai-switch/state.json

if pkg_install tree; then ok "real install of 'tree' succeeded with the hook present"; else bad "install failed"; fi
if [ "$(calls)" = 1 ] && grep -qx 'reconcile --quiet' "$log"; then
    ok "hook ran 'ai-switch reconcile --quiet' exactly once"
else
    bad "hook calls: $(calls) -> $(cat "$log" 2>/dev/null)"
fi

before=$(calls)
rm -f /var/lib/ai-switch/state.json
if pkg_install jq; then ok "real install of 'jq' succeeded"; else bad "second install failed"; fi
if [ "$(calls)" = "$before" ]; then ok "hook stays silent without a journal"; else bad "hook ran without a journal"; fi

# A failing ai-switch must not fail the transaction.
printf '#!/bin/sh\nexit 9\n' > /usr/bin/ai-switch
: > /var/lib/ai-switch/state.json
case "$pm" in
    dnf) dnf -y -q remove tree >/dev/null 2>&1; r=$? ;;
    apt) apt-get remove -y -qq tree >/dev/null 2>&1; r=$? ;;
    pacman) pacman -R --noconfirm --disable-sandbox tree >/dev/null 2>&1; r=$? ;;
    zypper) zypper -n --quiet remove tree >/dev/null 2>&1; r=$? ;;
esac
if [ "$r" = 0 ]; then ok "transaction succeeds even when ai-switch fails"; else bad "transaction failed ($r)"; fi

if command -v systemd-analyze >/dev/null 2>&1; then
    install -Dm644 "$src/systemd/ai-switch-reconcile.service" /usr/lib/systemd/system/ai-switch-reconcile.service
    install -Dm644 "$src/systemd/ai-switch-reconcile.timer" /usr/lib/systemd/system/ai-switch-reconcile.timer
    out=$(systemd-analyze verify /usr/lib/systemd/system/ai-switch-reconcile.service /usr/lib/systemd/system/ai-switch-reconcile.timer 2>&1)
    if [ -z "$out" ]; then ok "systemd-analyze verify accepts the units"; else bad "systemd-analyze: $out"; fi
else
    echo "SKIP  systemd-analyze not in image"
fi

echo; echo "fail=$fail"
exit $fail
