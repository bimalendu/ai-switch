#!/bin/sh
# Host side: run tests/containers/matrix.py in one container per distro.
# Usage: run-matrix.sh [name...]   (default: all)
# Env:   SCRIPT=hooks.sh runs the package-hook check instead of the main matrix.
#        PODMAN="podman --connection foo" to pick a machine.
set -u
here=$(cd "$(dirname "$0")/../.." && pwd)
PODMAN=${PODMAN:-podman}

# name|image|package manager|extra podman args
# archlinux publishes no arm64 image, so it runs under amd64 emulation (pacman's
# sandbox needs --disable-sandbox there, set in in-container.sh).
MATRIX="fedora|registry.fedoraproject.org/fedora:latest|dnf|
rocky9|docker.io/library/rockylinux:9|dnf|
ubuntu|docker.io/library/ubuntu:24.04|apt|
debian|docker.io/library/debian:stable|apt|
opensuse|registry.opensuse.org/opensuse/tumbleweed:latest|zypper|
arch|docker.io/library/archlinux:latest|pacman|--platform=linux/amd64"

want="$*"
rc=0
for row in $(echo "$MATRIX" | tr ' ' '_'); do
    row=$(echo "$row" | tr '_' ' ')
    name=${row%%|*}; rest=${row#*|}; image=${rest%%|*}; rest=${rest#*|}; pm=${rest%%|*}; extra=${rest#*|}
    if [ -n "$want" ] && ! echo " $want " | grep -q " $name "; then continue; fi
    echo "=================== $name ($image)"
    if $PODMAN run --rm $extra -v "$here:/src:ro,Z" -e PYTHONDONTWRITEBYTECODE=1 "$image" \
            sh "/src/tests/containers/${SCRIPT:-in-container.sh}" "$pm"; then
        echo ">>> $name: OK"
    else
        echo ">>> $name: FAILED"; rc=1
    fi
done
exit $rc
