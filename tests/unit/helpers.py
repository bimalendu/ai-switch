"""Shared test helpers: fake root trees and tree snapshots."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Dict, Optional

from ai_switch.context import Context, FakeRunner

JOURNAL_REL = "var/lib/ai-switch"

OS_RELEASES = {
    "fedora": 'ID=fedora\nVERSION_ID=44\n',
    "rhel": 'ID="rhel"\nID_LIKE="fedora"\nVERSION_ID="10.0"\n',
    "rocky": 'ID="rocky"\nID_LIKE="rhel centos fedora"\nVERSION_ID="9.4"\n',
    "ubuntu": 'ID=ubuntu\nID_LIKE=debian\nVERSION_ID="26.04"\n',
    "debian": 'ID=debian\nVERSION_ID="13"\n',
    "mint": 'ID=linuxmint\nID_LIKE="ubuntu debian"\nVERSION_ID="22"\n',
    "opensuse": 'ID="opensuse-tumbleweed"\nID_LIKE="opensuse suse"\n',
    "sles": 'ID="sles"\nID_LIKE="suse"\nVERSION_ID="16.0"\n',
    "arch": 'ID=arch\n',
    "deepin": 'ID=deepin\nID_LIKE=debian\nVERSION_ID="23"\n',
}


class FakeRoot:
    """A temporary directory laid out like a Linux root."""

    def __init__(self, distro: str = "fedora"):
        self.dir = Path(tempfile.mkdtemp(prefix="ai-switch-test-"))
        self.write("/etc/os-release", OS_RELEASES[distro])

    def cleanup(self) -> None:
        shutil.rmtree(str(self.dir), ignore_errors=True)

    def path(self, absolute: str) -> Path:
        return self.dir / absolute.lstrip("/")

    def write(self, absolute: str, text: str) -> Path:
        p = self.path(absolute)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def mkdir(self, absolute: str) -> Path:
        p = self.path(absolute)
        p.mkdir(parents=True, exist_ok=True)
        return p

    def unit(self, name: str, where: str = "/usr/lib/systemd/system") -> Path:
        return self.write("%s/%s" % (where, name), "[Unit]\nDescription=test\n")

    def context(self, runner: Optional[FakeRunner] = None) -> Context:
        return Context(str(self.dir), runner=runner or FakeRunner())

    def snapshot(self, ignore_journal: bool = True) -> Dict[str, str]:
        """Map every path under the root to a content hash, link target or 'dir'."""
        out: Dict[str, str] = {}
        for dirpath, dirnames, filenames in os.walk(str(self.dir)):
            rel_dir = os.path.relpath(dirpath, str(self.dir))
            if ignore_journal and rel_dir.startswith(JOURNAL_REL):
                continue
            if rel_dir != ".":
                out[rel_dir] = "dir"
            for f in filenames:
                full = os.path.join(dirpath, f)
                rel = os.path.relpath(full, str(self.dir))
                if os.path.islink(full):
                    out[rel] = "link:" + os.readlink(full)
                else:
                    with open(full, "rb") as fh:
                        out[rel] = hashlib.sha256(fh.read()).hexdigest()
            for d in dirnames:
                full = os.path.join(dirpath, d)
                if os.path.islink(full):
                    out[os.path.relpath(full, str(self.dir))] = "link:" + os.readlink(full)
        if ignore_journal:
            # The (possibly empty) journal directory itself is bookkeeping.
            for k in [k for k in out if k.startswith("var") and JOURNAL_REL.startswith(k)]:
                out.pop(k, None)
        return out


class RootTestCase(unittest.TestCase):
    distro = "fedora"

    def setUp(self) -> None:
        self.root = FakeRoot(self.distro)
        self.addCleanup(self.root.cleanup)


class LiveContextMixin:
    """Make a fake-root Context behave as a live system for command tests."""

    @staticmethod
    def live(ctx):
        class Live(type(ctx)):
            offline = property(lambda self: False)
        ctx.__class__ = Live
        return ctx
