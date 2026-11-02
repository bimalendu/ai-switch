import io
import json
import os
import re
import unittest
import xml.etree.ElementTree as ET
from contextlib import redirect_stdout

from ai_switch import gui_model as gm
from ai_switch import helper
from ai_switch.model import State
from tests.unit.helpers import RootTestCase
from tests.unit.test_engine import UnitModule

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

STATUS = json.dumps({"distro": "fedora", "version": "44", "modules": [
    {"id": "firefox", "title": "Firefox", "description": "d", "state": "on", "detail": "x",
     "control": "c", "experimental": False, "report_only": False},
    {"id": "chromium-family", "title": "Chrome", "description": "d", "state": "off (locked)",
     "detail": "", "control": "", "experimental": False, "report_only": False},
    {"id": "suse-ai", "title": "SUSE", "description": "d", "state": "not present",
     "experimental": True, "report_only": False},
    {"id": "deepin", "title": "deepin", "description": "d", "state": "unknown",
     "report_only": False},
    {"id": "known-ai-apps", "title": "Apps", "description": "d", "state": "on",
     "report_only": True},
]})


class Model(unittest.TestCase):
    def setUp(self):
        self.distro, self.rows = gm.parse_status(STATUS)
        self.by = {r.id: r for r in self.rows}

    def test_parse(self):
        self.assertEqual(self.distro, "fedora 44")
        self.assertEqual(len(self.rows), 5)
        self.assertTrue(self.by["suse-ai"].experimental)

    def test_toggleability(self):
        self.assertTrue(self.by["firefox"].can_toggle)
        self.assertTrue(self.by["chromium-family"].can_toggle)
        for i in ("suse-ai", "deepin", "known-ai-apps"):
            self.assertFalse(self.by[i].can_toggle, i)
        self.assertTrue(self.by["firefox"].is_on)
        self.assertFalse(self.by["chromium-family"].is_on)

    def test_pending_only_real_differences(self):
        off, on = gm.pending_changes(self.rows, {"firefox": False, "chromium-family": True})
        self.assertEqual((off, on), (["firefox"], ["chromium-family"]))
        # Setting a switch to the state it already has is not a change.
        self.assertEqual(gm.pending_changes(self.rows, {"firefox": True, "chromium-family": False}), ([], []))

    def test_pending_ignores_unswitchable(self):
        wanted = {"suse-ai": False, "deepin": False, "known-ai-apps": False, "nope": True}
        self.assertEqual(gm.pending_changes(self.rows, wanted), ([], []))

    def test_badges_and_tooltips(self):
        self.assertEqual(self.by["firefox"].badge, "AI on")
        self.assertEqual(self.by["chromium-family"].badge, "AI off, locked")
        self.assertIn("Report-only", self.by["known-ai-apps"].tooltip)
        self.assertIn("cannot tell", self.by["deepin"].tooltip)

    def test_argv(self):
        h = ["pkexec", "/usr/libexec/ai-switch-helper"]
        self.assertEqual(gm.apply_argv(h, ["a", "b"], ["c"], True),
                         h + ["apply", "--lock", "--off", "a", "b", "--on", "c"])
        self.assertEqual(gm.apply_argv(h, [], ["c"], True), h + ["apply", "--on", "c"])  # lock only with --off
        self.assertEqual(gm.revert_argv(h), h + ["revert"])
        self.assertEqual(gm.revert_argv(h, ["a"]), h + ["revert", "a"])

    def test_command_from_env(self):
        os.environ["AI_SWITCH_TEST_CMD"] = "python3 -m ai_switch --root '/a b'"
        self.addCleanup(os.environ.pop, "AI_SWITCH_TEST_CMD")
        self.assertEqual(gm.command_from_env("AI_SWITCH_TEST_CMD", ["x"]),
                         ["python3", "-m", "ai_switch", "--root", "/a b"])
        self.assertEqual(gm.command_from_env("AI_SWITCH_UNSET_VAR", ["x"]), ["x"])

    def test_outcome_cancelled(self):
        for rc in (126, 127):
            o = gm.parse_outcome(rc, "")
            self.assertTrue(o.cancelled)
            self.assertFalse(o.ok)
            self.assertIn("Nothing was changed", o.summary)

    def test_outcome_ok_and_failed(self):
        good = json.dumps({"ok": True, "results": [{"module": "firefox", "message": "done"}], "notes": ["n"]})
        o = gm.parse_outcome(0, good + "\n")
        self.assertTrue(o.ok)
        self.assertEqual(o.messages, ["firefox: done", "n"])
        bad = json.dumps({"ok": False, "results": [{"module": "m", "message": "boom"}], "notes": []})
        o = gm.parse_outcome(1, bad)
        self.assertFalse(o.ok)
        self.assertFalse(o.cancelled)
        self.assertIn("m: boom", o.messages)

    def test_outcome_garbage_shows_stderr(self):
        o = gm.parse_outcome(1, "", "pkexec: not found")
        self.assertEqual(o.messages, ["pkexec: not found"])


class Helper(RootTestCase):
    def setUp(self):
        super().setUp()
        self.root.unit("a.service")
        self.root.unit("b.service")
        self.mods = [UnitModule("alpha", ["a.service"]), UnitModule("beta", ["b.service"]),
                     UnitModule("apps", [], report_only=True)]

    def run_helper(self, *argv):
        return helper.run(list(argv), ctx=self.root.context(), modules=self.mods)

    def test_apply_off_and_on_in_one_call(self):
        doc = self.run_helper("apply", "--off", "alpha", "beta")
        self.assertTrue(doc["ok"], doc)
        self.assertTrue(self.root.context().unit_masked("a.service"))
        doc = self.run_helper("apply", "--off", "alpha", "--on", "beta")
        self.assertTrue(doc["ok"], doc)

    def test_revert(self):
        before = self.root.snapshot(ignore_journal=False)
        self.run_helper("apply", "--off", "alpha")
        doc = self.run_helper("revert")
        self.assertTrue(doc["ok"], doc)
        self.assertEqual(self.root.snapshot(ignore_journal=False), before)

    def test_refuses_anything_dangerous(self):
        before = self.root.snapshot(ignore_journal=False)
        for argv in (["apply", "--purge", "--off", "alpha"],
                     ["apply", "--all"],
                     ["apply", "--root", "/", "--off", "alpha"],
                     ["apply", "--off", "../etc"],
                     ["apply", "--off", "alpha; rm -rf /"],
                     ["apply", "--off", "nope"],
                     ["apply", "--off", "apps"],          # report-only
                     ["apply", "--off", "alpha", "--on", "alpha"],
                     ["apply"],
                     ["apply", "alpha"],                  # id without --off/--on
                     ["disable", "alpha"],                # not an allowed verb
                     ["revert", "--yes"],
                     []):
            doc = self.run_helper(*argv)
            self.assertFalse(doc["ok"], argv)
            self.assertEqual(doc["exit"], 2, argv)
        self.assertEqual(self.root.snapshot(ignore_journal=False), before)

    def test_lock_is_accepted_only_as_flag(self):
        doc = self.run_helper("apply", "--lock", "--off", "alpha")
        self.assertTrue(doc["ok"], doc)

    def test_partial_failure_is_reported(self):
        self.root.write("/etc/systemd/system/b.service", "[Service]\n")
        doc = self.run_helper("apply", "--off", "alpha", "beta")
        self.assertFalse(doc["ok"])
        self.assertEqual(doc["exit"], 1)
        self.assertTrue(any("refusing to overwrite" in r["message"] for r in doc["results"]))

    def test_main_prints_one_json_line(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = helper.main(["bogus"])
        self.assertEqual(code, 2)
        self.assertFalse(json.loads(buf.getvalue())["ok"])


class PolkitPolicy(unittest.TestCase):
    def setUp(self):
        self.tree = ET.parse(os.path.join(HERE, "data", "org.aiswitch.policy"))

    def test_one_action_bound_to_the_helper_only(self):
        actions = self.tree.getroot().findall("action")
        self.assertEqual([a.get("id") for a in actions], ["org.aiswitch.apply"])
        ann = {a.get("key"): a.text for a in actions[0].findall("annotate")}
        self.assertEqual(ann["org.freedesktop.policykit.exec.path"], "/usr/libexec/ai-switch-helper")

    def test_requires_admin_authentication_for_everyone(self):
        d = self.tree.getroot().find("action/defaults")
        self.assertEqual(d.find("allow_any").text, "auth_admin")
        self.assertEqual(d.find("allow_inactive").text, "auth_admin")
        self.assertEqual(d.find("allow_active").text, "auth_admin_keep")

    def test_helper_script_is_isolated_python(self):
        with open(os.path.join(HERE, "bin", "ai-switch-helper")) as f:
            first = f.readline().strip()
        self.assertTrue(re.match(r"#!/usr/bin/python3 -I$", first), first)


if __name__ == "__main__":
    unittest.main()
