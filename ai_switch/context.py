"""Everything a module needs to know about the system it runs on.

All filesystem access goes through ``Context.path`` so ``--root DIR`` can point
the tool at an offline image or a fake tree in tests. All commands go through
a ``Runner`` so tests can script their output.
"""

from __future__ import annotations

import fnmatch
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set

UNIT_DIRS = (
    "/etc/systemd/system",
    "/run/systemd/system",
    "/usr/local/lib/systemd/system",
    "/usr/lib/systemd/system",
    "/lib/systemd/system",
)

# Family ids as they appear in os-release ID / ID_LIKE.
_RPM_DNF = {"fedora", "rhel", "centos", "rocky", "almalinux", "ol", "amzn"}
_APT = {"debian", "ubuntu", "linuxmint", "pop", "deepin", "raspbian", "kali"}
_ZYPPER = {"opensuse", "opensuse-leap", "opensuse-tumbleweed", "sles", "sled", "suse"}
_PACMAN = {"arch", "archarm", "manjaro", "endeavouros", "cachyos"}


@dataclass
class Result:
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class Runner:
    """Runs external commands. Never raises for a missing binary."""

    def run(self, argv: Sequence[str], timeout: int = 120) -> Result:
        try:
            p = subprocess.run(
                list(argv),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=timeout,
            )
        except FileNotFoundError:
            return Result(127, "", "command not found: %s" % argv[0])
        except subprocess.TimeoutExpired:
            return Result(124, "", "timed out: %s" % " ".join(argv))
        return Result(p.returncode, p.stdout, p.stderr)

    def which(self, name: str) -> Optional[str]:
        return shutil.which(name)


class FakeRunner(Runner):
    """Test double. ``responses`` maps a command prefix (tuple) to a Result.

    The longest matching prefix wins. Unscripted commands return 127 so a test
    notices when code runs something it did not expect.
    """

    def __init__(self, responses: Optional[Dict[tuple, Result]] = None,
                 commands: Sequence[str] = ()):
        self.responses = dict(responses or {})
        self.commands = set(commands)
        self.calls: List[List[str]] = []

    def run(self, argv: Sequence[str], timeout: int = 120) -> Result:
        argv = list(argv)
        self.calls.append(argv)
        best = None
        for prefix in self.responses:
            if tuple(argv[: len(prefix)]) == prefix and (best is None or len(prefix) > len(best)):
                best = prefix
        if best is None:
            return Result(127, "", "unscripted: %s" % " ".join(argv))
        return self.responses[best]

    def which(self, name: str) -> Optional[str]:
        return "/usr/bin/" + name if name in self.commands else None


def parse_os_release(text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[k.strip()] = v
    return out


class Context:
    def __init__(self, root: str = "/", runner: Optional[Runner] = None):
        self.root = Path(root)
        self.runner = runner or Runner()
        self.os_release = self._load_os_release()
        self._packages: Optional[Set[str]] = None
        self.cache: Dict[str, object] = {}  # per-run memo for expensive queries

    # -- filesystem ---------------------------------------------------------

    @property
    def offline(self) -> bool:
        """True when operating on a tree that is not the running system."""
        return os.path.realpath(str(self.root)) != "/"

    def path(self, absolute: str) -> Path:
        return self.root / absolute.lstrip("/")

    def _load_os_release(self) -> Dict[str, str]:
        for p in ("/etc/os-release", "/usr/lib/os-release"):
            f = self.path(p)
            try:
                return parse_os_release(f.read_text(encoding="utf-8"))
            except OSError:
                continue
        return {}

    # -- distro -------------------------------------------------------------

    @property
    def distro_id(self) -> str:
        return self.os_release.get("ID", "").lower()

    @property
    def version_id(self) -> str:
        return self.os_release.get("VERSION_ID", "")

    @property
    def ids(self) -> Set[str]:
        like = self.os_release.get("ID_LIKE", "").lower().split()
        return {self.distro_id, *like} - {""}

    def is_distro(self, *names: str) -> bool:
        return bool(self.ids & set(names))

    @property
    def pkg_manager(self) -> Optional[str]:
        """'dnf', 'apt', 'zypper', 'pacman', or None when unknown."""
        ids = self.ids
        # Order matters: SLES/openSUSE list "suse" and sometimes "opensuse".
        if ids & _ZYPPER:
            return "zypper"
        if ids & _PACMAN:
            return "pacman"
        if ids & _APT:
            return "apt"
        if ids & _RPM_DNF:
            return "dnf"
        return None

    # -- commands -----------------------------------------------------------

    def run(self, argv: Sequence[str], timeout: int = 120) -> Result:
        return self.runner.run(argv, timeout=timeout)

    def which(self, name: str) -> Optional[str]:
        return self.runner.which(name)

    @property
    def can_run_services(self) -> bool:
        """Commands that talk to the live system (systemctl, snap) only make
        sense when we are not working on an offline tree."""
        return not self.offline

    # -- systemd ------------------------------------------------------------

    def unit_path(self, unit: str) -> Optional[Path]:
        """The real unit file (not a mask symlink) for ``unit``, if installed."""
        for d in UNIT_DIRS:
            p = self.path(d) / unit
            if p.is_symlink():
                if os.readlink(str(p)) == "/dev/null":
                    continue
                # An alias or enablement-style symlink: follow it if it resolves.
                if p.exists():
                    return p
                continue
            if p.exists():
                return p
        return None

    def unit_installed(self, unit: str) -> bool:
        return self.unit_path(unit) is not None

    def unit_masked(self, unit: str) -> bool:
        for d in ("/etc/systemd/system", "/run/systemd/system"):
            p = self.path(d) / unit
            if p.is_symlink() and os.readlink(str(p)) == "/dev/null":
                return True
        return False

    # -- packages -----------------------------------------------------------

    def installed_packages(self) -> Set[str]:
        """Names of installed packages, honouring --root. Cached per Context."""
        if self._packages is not None:
            return self._packages
        pm = self.pkg_manager
        root = str(self.root)
        if pm in ("dnf", "zypper"):
            argv = ["rpm", "-qa", "--qf", "%{NAME}\\n"]
            if self.offline:
                argv[1:1] = ["--root", root]
        elif pm == "apt":
            argv = ["dpkg-query", "-W", "-f", "${Package} ${Status}\\n"]
            if self.offline:
                argv[1:1] = ["--admindir", str(self.path("/var/lib/dpkg"))]
        elif pm == "pacman":
            argv = ["pacman", "-Qq"]
            if self.offline:
                argv[1:1] = ["--root", root]
        else:
            self._packages = set()
            return self._packages
        res = self.run(argv)
        names: Set[str] = set()
        if res.ok:
            for line in res.stdout.splitlines():
                parts = line.split()
                if not parts:
                    continue
                if pm == "apt":
                    # "name install ok installed"; skip removed-but-configured.
                    if "installed" not in parts[1:] or "not-installed" in parts[1:]:
                        continue
                names.add(parts[0])
        self._packages = names
        return names

    def package_files(self, name: str) -> List[str]:
        """Absolute paths a package installed (from the package database)."""
        key = "files:" + name
        if key in self.cache:
            return self.cache[key]  # type: ignore[return-value]
        pm, root = self.pkg_manager, str(self.root)
        if pm in ("dnf", "zypper"):
            argv = ["rpm", "-ql", name]
            if self.offline:
                argv[1:1] = ["--root", root]
        elif pm == "apt":
            argv = ["dpkg-query", "-L", name]
            if self.offline:
                argv[1:1] = ["--admindir", str(self.path("/var/lib/dpkg"))]
        elif pm == "pacman":
            argv = ["pacman", "-Qlq", name]
            if self.offline:
                argv[1:1] = ["--root", root]
        else:
            argv = []
        files: List[str] = []
        if argv:
            res = self.run(argv)
            if res.ok:
                files = [l.strip().split(" ", 1)[-1] if pm == "pacman" else l.strip()
                         for l in res.stdout.splitlines() if l.strip().startswith("/") or pm == "pacman"]
        self.cache[key] = files
        return files

    def units_of_packages(self, names: Sequence[str]) -> List[str]:
        """System unit names (.service/.socket/.timer) shipped by installed packages."""
        units: List[str] = []
        for name in names:
            for f in self.package_files(name):
                parts = f.split("/")
                if (len(parts) >= 2 and parts[-2] == "system" and "systemd" in parts
                        and parts[-1].endswith((".service", ".socket", ".timer"))
                        and "@" not in parts[-1]):
                    if parts[-1] not in units:
                        units.append(parts[-1])
        return units

    def packages_matching(self, globs: Sequence[str]) -> List[str]:
        found = set()
        for name in self.installed_packages():
            if any(fnmatch.fnmatchcase(name, g) for g in globs):
                found.add(name)
        return sorted(found)
