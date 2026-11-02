import os
import unittest

from ai_switch.context import Context, FakeRunner, Result, parse_os_release
from tests.unit.helpers import FakeRoot, RootTestCase


class ParseOsRelease(unittest.TestCase):
    def test_quotes_and_comments(self):
        d = parse_os_release('# c\nID="rhel"\nID_LIKE=\'fedora\'\nNAME=Foo Bar\n\n')
        self.assertEqual(d, {"ID": "rhel", "ID_LIKE": "fedora", "NAME": "Foo Bar"})


class DistroDetection(unittest.TestCase):
    def check(self, distro, pm, member):
        r = FakeRoot(distro)
        self.addCleanup(r.cleanup)
        ctx = r.context()
        self.assertEqual(ctx.pkg_manager, pm, distro)
        self.assertTrue(ctx.is_distro(member), distro)

    def test_matrix(self):
        self.check("fedora", "dnf", "fedora")
        self.check("rhel", "dnf", "rhel")
        self.check("rocky", "dnf", "rhel")
        self.check("ubuntu", "apt", "debian")
        self.check("mint", "apt", "ubuntu")
        self.check("debian", "apt", "debian")
        self.check("deepin", "apt", "deepin")
        self.check("opensuse", "zypper", "suse")
        self.check("sles", "zypper", "suse")
        self.check("arch", "pacman", "arch")

    def test_unknown_distro(self):
        r = FakeRoot("fedora")
        self.addCleanup(r.cleanup)
        r.write("/etc/os-release", "ID=plan9\n")
        self.assertIsNone(r.context().pkg_manager)

    def test_missing_os_release(self):
        r = FakeRoot("fedora")
        self.addCleanup(r.cleanup)
        os.remove(str(r.path("/etc/os-release")))
        ctx = r.context()
        self.assertEqual(ctx.distro_id, "")
        self.assertIsNone(ctx.pkg_manager)

    def test_usr_lib_fallback(self):
        r = FakeRoot("fedora")
        self.addCleanup(r.cleanup)
        os.remove(str(r.path("/etc/os-release")))
        r.write("/usr/lib/os-release", "ID=arch\n")
        self.assertEqual(r.context().distro_id, "arch")


class Units(RootTestCase):
    def test_installed_and_masked(self):
        ctx = self.root.context()
        self.assertFalse(ctx.unit_installed("x.service"))
        self.root.unit("x.service")
        self.assertTrue(ctx.unit_installed("x.service"))
        self.assertFalse(ctx.unit_masked("x.service"))
        self.root.mkdir("/etc/systemd/system")
        os.symlink("/dev/null", str(self.root.path("/etc/systemd/system/x.service")))
        self.assertTrue(ctx.unit_masked("x.service"))
        # Masked units still exist on disk in /usr/lib.
        self.assertTrue(ctx.unit_installed("x.service"))

    def test_mask_symlink_alone_is_not_installed(self):
        ctx = self.root.context()
        self.root.mkdir("/etc/systemd/system")
        os.symlink("/dev/null", str(self.root.path("/etc/systemd/system/gone.service")))
        self.assertFalse(ctx.unit_installed("gone.service"))


class Packages(RootTestCase):
    def test_rpm_offline_uses_root_flag(self):
        runner = FakeRunner({("rpm",): Result(0, "ollama\nbash\nllama-cpp-server\n")})
        ctx = self.root.context(runner)
        self.assertEqual(ctx.packages_matching(["ollama", "llama-cpp-*"]),
                         ["llama-cpp-server", "ollama"])
        self.assertEqual(runner.calls[0][:3], ["rpm", "--root", str(self.root.dir)])

    def test_cached(self):
        runner = FakeRunner({("rpm",): Result(0, "a\n")})
        ctx = self.root.context(runner)
        ctx.installed_packages()
        ctx.installed_packages()
        self.assertEqual(len(runner.calls), 1)

    def test_dpkg_skips_removed(self):
        self.root.write("/etc/os-release", "ID=debian\n")
        out = "ollama install ok installed\nold deinstall ok config-files\n"
        runner = FakeRunner({("dpkg-query",): Result(0, out)})
        ctx = self.root.context(runner)
        self.assertEqual(ctx.installed_packages(), {"ollama"})

    def test_query_failure_gives_empty_set(self):
        ctx = self.root.context(FakeRunner())
        self.assertEqual(ctx.installed_packages(), set())


if __name__ == "__main__":
    unittest.main()
