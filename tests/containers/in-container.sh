#!/bin/sh
# Runs inside the container: make sure python3 exists, then run matrix.py.
set -eu
pm="$1"
if ! command -v python3 >/dev/null 2>&1; then
    case "$pm" in
        apt)    apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 >/dev/null ;;
        pacman) pacman -Sy --noconfirm --disable-sandbox python >/dev/null ;;
        zypper) zypper -n --quiet install python3 >/dev/null ;;
        dnf)    dnf -y -q install python3 >/dev/null ;;
    esac
fi
cd /src
exec python3 tests/containers/matrix.py "$pm"
