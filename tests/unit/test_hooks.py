import configparser
import glob
import io
import os
import stat
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from ai_switch import cli, engine
from ai_switch.journal import Journal
from tests.unit.helpers import RootTestCase
from tests.unit.test_engine import UnitModule

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def path(*p):
    return os.path.join(HERE, *p)


class HookHelper(unittest.TestCase):
    """hooks/ai-switch-hook: calls reconcile only when it should, never fails."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ai-switch-hook-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.dir, ignore_errors=True))
        self.log = os.path.join(self.dir, "calls.log")
        self.state = os.path.join(self.dir, "state.json")
        self.fake = os.path.join(self.dir, "fake-ai-switch")
        self.make_fake("exit 0")

    def make_fake(self, tail):
        with open(self.fake, "w") as f:
            f.write('#!/bin/sh\necho "$@" >> "%s"\necho noise-on-stdout\n%s\n' % (self.log, tail))
        os.chmod(self.fake, 0o755)

    def run_hook(self, bin_=None):
        env = dict(os.environ, AI_SWITCH_BIN=bin_ or self.fake, AI_SWITCH_STATE=self.state)
        return subprocess.run(["sh", path("hooks", "ai-switch-hook")], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True)

    def calls(self):
        try:
            with open(self.log) as f:
                return f.read().splitlines()
        except FileNotFoundError:
            return []

    def test_does_nothing_without_a_journal(self):
        p = self.run_hook()
        self.assertEqual((p.returncode, self.calls()), (0, []))

    def test_runs_reconcile_quietly_when_journal_exists(self):
        open(self.state, "w").close()
        p = self.run_hook()
        self.assertEqual(p.returncode, 0)
        self.assertEqual(self.calls(), ["reconcile --quiet"])

    def test_never_writes_to_stdout(self):
        open(self.state, "w").close()
        self.assertEqual(self.run_hook().stdout, "")  # zypper's protocol channel

    def test_failing_reconcile_does_not_fail_the_hook(self):
        open(self.state, "w").close()
        self.make_fake("exit 7")
        self.assertEqual(self.run_hook().returncode, 0)

    def test_missing_binary_is_fine(self):
        open(self.state, "w").close()
        self.assertEqual(self.run_hook(bin_="/nonexistent/ai-switch").returncode, 0)


class ZypperPlugin(HookHelper):
    def run_plugin(self, frames):
        fake_libexec = os.path.join(self.dir, "libexec")
        os.makedirs(fake_libexec)
        # The plugin calls the installed helper path; point it at a test copy.
        with open(path("hooks", "zypper", "ai-switch")) as f:
            script = f.read().replace("/usr/libexec/ai-switch-hook", path("hooks", "ai-switch-hook"))
        plugin = os.path.join(self.dir, "plugin")
        with open(plugin, "w") as f:
            f.write(script)
        env = dict(os.environ, AI_SWITCH_BIN=self.fake, AI_SWITCH_STATE=self.state)
        return subprocess.run(["bash", plugin], input=frames.encode(), env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def test_acks_every_frame_and_reconciles_on_commitend(self):
        open(self.state, "w").close()
        frames = "PLUGINBEGIN\n\n\0COMMITBEGIN\n\n\0COMMITEND\n\n\0_DISMISS\n\n\0"
        p = self.run_plugin(frames)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.stdout, b"ACK\n\n\0" * 4)  # nothing else on stdout
        self.assertEqual(self.calls(), ["reconcile --quiet"])

    def test_no_reconcile_before_commit_end(self):
        open(self.state, "w").close()
        self.run_plugin("PLUGINBEGIN\n\n\0COMMITBEGIN\n\n\0")
        self.assertEqual(self.calls(), [])


class StaticFiles(unittest.TestCase):
    def test_all_hooks_call_the_helper(self):
        for rel in (("hooks", "dnf5", "ai-switch.actions"), ("hooks", "apt", "99ai-switch"),
                    ("hooks", "pacman", "ai-switch.hook"), ("hooks", "zypper", "ai-switch")):
            with open(path(*rel)) as f:
                self.assertIn("/usr/libexec/ai-switch-hook", f.read(), rel)

    def test_dnf5_actions_line_is_well_formed(self):
        with open(path("hooks", "dnf5", "ai-switch.actions")) as f:
            lines = [l for l in f if l.strip() and not l.startswith("#")]
        self.assertEqual(len(lines), 1)
        hook, _, rest = lines[0].partition(":::")
        self.assertEqual(hook, "post_transaction")
        self.assertTrue(rest.startswith("enabled=host-only:/"))

    def test_pacman_hook_sections(self):
        cp = configparser.ConfigParser(strict=False)
        cp.optionxform = str
        cp.read(path("hooks", "pacman", "ai-switch.hook"))
        self.assertEqual(cp["Action"]["When"], "PostTransaction")
        self.assertEqual(cp["Trigger"]["Type"], "Package")

    def test_apt_conf_never_fails_transaction(self):
        with open(path("hooks", "apt", "99ai-switch")) as f:
            self.assertIn("|| true", f.read())

    def test_units(self):
        for name in ("service", "timer"):
            cp = configparser.ConfigParser(strict=False)
            cp.optionxform = str
            cp.read(path("systemd", "ai-switch-reconcile." + name))
            self.assertIn("Install", cp)
        svc = configparser.ConfigParser(strict=False)
        svc.optionxform = str
        svc.read(path("systemd", "ai-switch-reconcile.service"))
        self.assertEqual(svc["Service"]["Type"], "oneshot")
        self.assertEqual(svc["Service"]["ExecStart"], "/usr/bin/ai-switch reconcile --quiet")
        self.assertEqual(svc["Unit"]["ConditionPathExists"], "/var/lib/ai-switch/state.json")

    def test_scripts_are_executable(self):
        for rel in (("hooks", "ai-switch-hook"), ("hooks", "zypper", "ai-switch")):
            self.assertTrue(os.stat(path(*rel)).st_mode & stat.S_IXUSR, rel)


class InstallTarget(unittest.TestCase):
    def test_every_source_file_named_by_install_hooks_exists(self):
        out = subprocess.run(["make", "-n", "install-hooks", "DESTDIR=/nonexistent"], cwd=HERE,
                             stdout=subprocess.PIPE, universal_newlines=True).stdout
        sources = [w for w in out.split() if w.startswith(("hooks/", "systemd/"))]
        self.assertGreaterEqual(len(sources), 8, out)
        for src in sources:
            self.assertTrue(os.path.exists(path(src)), src)

    def test_every_source_file_named_by_install_core_and_gui_exists(self):
        for target in ("install-core", "install-gui"):
            out = subprocess.run(["make", "-n", target, "DESTDIR=/nonexistent", "PYLIB=/py"], cwd=HERE,
                                 stdout=subprocess.PIPE, universal_newlines=True).stdout
            for word in out.split():
                if word.startswith(("bin/", "data/")) or word.endswith(("/*.py", "/*.txt")):
                    self.assertTrue(glob.glob(path(word)), (target, word))

    def test_packaging_files_agree_on_the_version(self):
        from ai_switch import __version__
        with open(path("packaging", "ai-switch.spec")) as f:
            self.assertIn("Version:        %s\n" % __version__, f.read())
        with open(path("packaging", "arch", "PKGBUILD")) as f:
            self.assertIn("pkgver=%s\n" % __version__, f.read())
        with open(path("packaging", "debian", "changelog")) as f:
            self.assertTrue(f.readline().startswith("ai-switch (%s)" % __version__))

    def test_dnf4_plugin_compiles_and_names_the_helper(self):
        with open(path("hooks", "dnf4", "ai_switch.py")) as f:
            code = f.read()
        compile(code, "ai_switch.py", "exec")
        self.assertIn("/usr/libexec/ai-switch-hook", code)


class ReconcileQuiet(RootTestCase):
    def setUp(self):
        super().setUp()
        self.root.unit("a.service")
        self.mod = UnitModule("m", ["a.service"])

    def run_cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(argv, ctx=self.root.context(), modules=[self.mod])
        return code, out.getvalue()

    def test_quiet_prints_nothing_when_nothing_to_do(self):
        self.assertEqual(self.run_cli(["reconcile", "--quiet"]), (0, ""))
        self.assertIn("Nothing to reconcile", self.run_cli(["reconcile"])[1])

    def test_quiet_still_reports_what_it_re_applied(self):
        self.assertEqual(self.run_cli(["disable", "m", "--yes"])[0], 0)
        os.remove(str(self.root.path("/etc/systemd/system/a.service")))
        code, out = self.run_cli(["reconcile", "--quiet"])
        self.assertEqual(code, 0)
        self.assertIn("a.service", out)
        self.assertTrue(self.root.context().unit_masked("a.service"))


if __name__ == "__main__":
    unittest.main()
