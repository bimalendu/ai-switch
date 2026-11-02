"""Apply/undo round trips for every action primitive."""

import json
import os
import unittest

from ai_switch.actions.files import OwnedJsonFile
from ai_switch.actions.packages import FlatpakRemove, PackageRemove
from ai_switch.actions.policyjson import PolicyJsonMerge, leaves_match
from ai_switch.actions.snap import SnapDisable, SnapRemove
from ai_switch.actions.systemd import SystemdMask
from ai_switch.context import FakeRunner, Result
from ai_switch.model import (OBS_OFF, OBS_OFF_LOCKED, OBS_ON, OBS_UNKNOWN,
                             ActionError, Observation)
from tests.unit.helpers import LiveContextMixin, RootTestCase


class SystemdMaskTests(RootTestCase):
    def setUp(self):
        super().setUp()
        self.root.unit("clad.service")
        self.ctx = self.root.context()
        self.act = SystemdMask("clad.service")

    def test_round_trip_is_byte_identical(self):
        before_tree = self.root.snapshot()
        self.assertEqual(self.act.read(self.ctx).state, OBS_ON)
        rec = self.act.to_off(self.ctx, False)
        self.assertEqual(self.act.read(self.ctx).state, OBS_OFF)
        self.assertTrue(self.ctx.unit_masked("clad.service"))
        self.act.undo(self.ctx, rec)
        self.assertEqual(self.root.snapshot(), before_tree)

    def test_creates_and_removes_systemd_dir(self):
        self.assertFalse(self.root.path("/etc/systemd/system").exists())
        rec = self.act.to_off(self.ctx, False)
        self.assertTrue(self.root.path("/etc/systemd/system").is_dir())
        self.act.undo(self.ctx, rec)
        self.assertFalse(self.root.path("/etc/systemd/system").exists())

    def test_idempotent(self):
        self.act.to_off(self.ctx, False)
        rec2 = self.act.to_off(self.ctx, False)
        self.assertTrue(rec2["was_masked"])
        self.act.undo(self.ctx, rec2)  # was already masked: stays masked
        self.assertTrue(self.ctx.unit_masked("clad.service"))

    def test_refuses_to_clobber_admin_file(self):
        self.root.write("/etc/systemd/system/clad.service", "[Service]\n")
        with self.assertRaises(ActionError):
            self.act.to_off(self.ctx, False)
        self.assertEqual(self.root.path("/etc/systemd/system/clad.service").read_text(), "[Service]\n")

    def test_enable_unmasks(self):
        rec = self.act.to_off(self.ctx, False)
        self.act.to_on(self.ctx)
        self.assertFalse(self.ctx.unit_masked("clad.service"))
        self.act.undo(self.ctx, rec)  # baseline: unmasked
        self.assertFalse(self.ctx.unit_masked("clad.service"))
        self.assertFalse(self.root.path("/etc/systemd/system").exists())

    def test_online_runs_systemctl(self):
        runner = FakeRunner({("systemctl",): Result(0)})
        ctx = LiveContextMixin.live(self.root.context(runner))
        self.act.to_off(ctx, False)
        self.assertIn(["systemctl", "daemon-reload"], runner.calls)
        self.assertIn(["systemctl", "stop", "clad.service"], runner.calls)

    def test_systemctl_failure_is_a_note_not_an_error(self):
        runner = FakeRunner({("systemctl",): Result(1, "", "no bus")})
        ctx = LiveContextMixin.live(self.root.context(runner))
        rec = self.act.to_off(ctx, False)
        self.assertTrue(ctx.unit_masked("clad.service"))
        self.assertIn("no bus", rec["note"])

    def test_present(self):
        self.assertTrue(self.act.present(self.ctx))
        self.assertFalse(SystemdMask("other.service").present(self.ctx))


FF_PATH = "/usr/lib64/firefox/distribution/policies.json"


def ff_off(lock):
    return {"policies": {"AIControls": {"Default": {"Value": "blocked", "Locked": lock}}}}


def ff_on(_lock):
    return {"policies": {"AIControls": {"Default": {"Value": "available", "Locked": False}}}}


def ff_reader(data):
    if leaves_match(data, ff_off(True)):
        return Observation(OBS_OFF_LOCKED, "blocked")
    if leaves_match(data, ff_off(False)):
        return Observation(OBS_OFF, "blocked")
    return Observation(OBS_ON, "available")


class PolicyJsonTests(RootTestCase):
    def setUp(self):
        super().setUp()
        self.ctx = self.root.context()
        self.act = PolicyJsonMerge("ff", "firefox", FF_PATH, lambda c: True, ff_off, ff_on, ff_reader)
        self.file = self.root.path(FF_PATH)

    def test_creates_file_and_dirs_then_removes_them(self):
        before = self.root.snapshot()
        rec = self.act.to_off(self.ctx, False)
        self.assertEqual(json.loads(self.file.read_text()), ff_off(False))
        self.assertEqual(self.act.read(self.ctx).state, OBS_OFF)
        self.act.undo(self.ctx, rec)
        self.assertEqual(self.root.snapshot(), before)

    def test_lock(self):
        self.act.to_off(self.ctx, True)
        self.assertEqual(self.act.read(self.ctx).state, OBS_OFF_LOCKED)

    def test_merges_with_existing_admin_policy_byte_identical_revert(self):
        original = '{"policies": {"DisableTelemetry": true,\n   "Homepage": {"URL": "x"}}}\n'
        self.root.write(FF_PATH, original)
        before = self.root.snapshot()
        rec = self.act.to_off(self.ctx, False)
        data = json.loads(self.file.read_text())
        self.assertTrue(data["policies"]["DisableTelemetry"])
        self.assertEqual(data["policies"]["Homepage"], {"URL": "x"})
        self.assertEqual(data["policies"]["AIControls"]["Default"]["Value"], "blocked")
        self.act.undo(self.ctx, rec)
        self.assertEqual(self.file.read_text(), original)
        self.assertEqual(self.root.snapshot(), before)

    def test_existing_aicontrols_value_is_restored(self):
        original = {"policies": {"AIControls": {"Default": {"Value": "available", "Locked": True},
                                                 "Translations": {"Value": "available"}}}}
        self.root.write(FF_PATH, json.dumps(original))
        rec = self.act.to_off(self.ctx, False)
        # An admin edits another key after us, so byte restore is impossible.
        data = json.loads(self.file.read_text())
        data["policies"]["Extra"] = 1
        self.file.write_text(json.dumps(data))
        self.act.undo(self.ctx, rec)
        out = json.loads(self.file.read_text())
        self.assertEqual(out["policies"]["AIControls"], original["policies"]["AIControls"])
        self.assertEqual(out["policies"]["Extra"], 1)

    def test_edited_file_we_created_is_kept_but_our_keys_go(self):
        rec = self.act.to_off(self.ctx, False)
        data = json.loads(self.file.read_text())
        data["policies"]["Other"] = True
        self.file.write_text(json.dumps(data))
        self.act.undo(self.ctx, rec)
        self.assertEqual(json.loads(self.file.read_text()), {"policies": {"Other": True}})

    def test_edited_file_with_only_our_keys_is_deleted(self):
        rec = self.act.to_off(self.ctx, False)
        self.file.write_text(json.dumps(ff_off(False)))  # same content, new bytes
        self.act.undo(self.ctx, rec)
        self.assertFalse(self.file.exists())
        self.assertFalse(self.root.path("/usr/lib64/firefox").exists())

    def test_enable_writes_available_and_revert_still_baseline(self):
        before = self.root.snapshot()
        rec = self.act.to_off(self.ctx, False)
        self.act.to_on(self.ctx)
        self.assertEqual(self.act.read(self.ctx).state, OBS_ON)
        self.act.undo(self.ctx, rec)
        self.assertEqual(self.root.snapshot(), before)

    def test_invalid_json_is_refused_untouched(self):
        self.root.write(FF_PATH, "{not json")
        with self.assertRaises(ActionError):
            self.act.to_off(self.ctx, False)
        self.assertEqual(self.file.read_text(), "{not json")
        self.assertEqual(self.act.read(self.ctx).state, OBS_UNKNOWN)

    def test_non_object_aicontrols_is_refused(self):
        self.root.write(FF_PATH, json.dumps({"policies": {"AIControls": "yes"}}))
        with self.assertRaises(ActionError):
            self.act.to_off(self.ctx, False)

    def test_file_mode_preserved(self):
        self.root.write(FF_PATH, "{}")
        os.chmod(str(self.file), 0o640)
        self.act.to_off(self.ctx, False)
        self.assertEqual(os.stat(str(self.file)).st_mode & 0o777, 0o640)


class OwnedFileTests(RootTestCase):
    CONTENT = {"GeminiSettings": 1, "AIModeSettings": 1}
    PATH = "/etc/opt/chrome/policies/managed/ai-switch.json"

    def setUp(self):
        super().setUp()
        self.ctx = self.root.context()
        self.act = OwnedJsonFile("chrome", "chrome", self.PATH, lambda c: True, self.CONTENT)

    def test_round_trip(self):
        before = self.root.snapshot()
        self.assertEqual(self.act.read(self.ctx).state, OBS_ON)
        rec = self.act.to_off(self.ctx, False)
        self.assertEqual(self.act.read(self.ctx).state, OBS_OFF_LOCKED)
        self.assertEqual(oct(os.stat(str(self.root.path(self.PATH))).st_mode & 0o777), "0o644")
        self.act.undo(self.ctx, rec)
        self.assertEqual(self.root.snapshot(), before)

    def test_existing_file_is_restored(self):
        self.root.write(self.PATH, '{"x": 1}\n')
        before = self.root.snapshot()
        rec = self.act.to_off(self.ctx, False)
        self.act.undo(self.ctx, rec)
        self.assertEqual(self.root.snapshot(), before)

    def test_partial_policy_reports_on(self):
        self.root.write(self.PATH, json.dumps({"GeminiSettings": 1}))
        self.assertEqual(self.act.read(self.ctx).state, OBS_ON)

    def test_enable_removes_file(self):
        rec = self.act.to_off(self.ctx, False)
        self.act.to_on(self.ctx)
        self.assertFalse(self.root.path(self.PATH).exists())
        self.act.undo(self.ctx, rec)
        self.assertFalse(self.root.path("/etc/opt").exists())

    def test_bad_json_is_unknown(self):
        self.root.write(self.PATH, "nope")
        self.assertEqual(self.act.read(self.ctx).state, OBS_UNKNOWN)


SNAP_LIST = "Name Version Rev Tracking Publisher Notes\ngemma3 1 5 latest canonical -\nqwen3 1 2 latest canonical disabled\ncore22 1 1 latest canonical base\n"


class SnapTests(RootTestCase, LiveContextMixin):
    def make(self, listing=SNAP_LIST):
        self.runner = FakeRunner({("snap", "list"): Result(0, listing),
                                  ("snap", "disable"): Result(0),
                                  ("snap", "enable"): Result(0),
                                  ("snap", "remove"): Result(0)})
        return self.live(self.root.context(self.runner))

    def test_read(self):
        ctx = self.make()
        self.assertEqual(SnapDisable("gemma3").read(ctx).state, OBS_ON)
        self.assertEqual(SnapDisable("qwen3").read(ctx).state, OBS_OFF)
        self.assertFalse(SnapDisable("nope").present(ctx))

    def test_disable_and_undo(self):
        ctx = self.make()
        act = SnapDisable("gemma3")
        rec = act.to_off(ctx, False)
        self.assertEqual(rec, {"was_disabled": False})
        self.assertIn(["snap", "disable", "gemma3"], self.runner.calls)
        # snapd now reports it disabled
        self.runner.responses[("snap", "list")] = Result(0, SNAP_LIST.replace("gemma3 1 5 latest canonical -", "gemma3 1 5 latest canonical disabled"))
        ctx.cache.clear()
        act.undo(ctx, rec)
        self.assertIn(["snap", "enable", "gemma3"], self.runner.calls)

    def test_already_disabled_is_not_reenabled_on_undo(self):
        ctx = self.make()
        act = SnapDisable("qwen3")
        rec = act.to_off(ctx, False)
        self.assertEqual(rec, {"was_disabled": True})
        self.assertNotIn(["snap", "disable", "qwen3"], self.runner.calls)
        act.undo(ctx, rec)
        self.assertNotIn(["snap", "enable", "qwen3"], self.runner.calls)

    def test_offline_root_finds_snap_file_but_cannot_change(self):
        self.root.write("/var/lib/snapd/snaps/gemma3_5.snap", "x")
        ctx = self.root.context()
        act = SnapDisable("gemma3")
        self.assertTrue(act.present(ctx))
        self.assertEqual(act.read(ctx).state, OBS_UNKNOWN)
        with self.assertRaises(ActionError):
            act.to_off(ctx, False)

    def test_remove_is_irreversible(self):
        ctx = self.make()
        act = SnapRemove("gemma3")
        rec = act.to_off(ctx, False)
        self.assertIn(["snap", "remove", "gemma3"], self.runner.calls)
        self.assertIn("snap install gemma3", rec["reinstall"])
        with self.assertRaises(ActionError):
            act.undo(ctx, rec)

    def test_failed_command_raises(self):
        ctx = self.make()
        self.runner.responses[("snap", "disable")] = Result(1, "", "boom")
        with self.assertRaises(ActionError):
            SnapDisable("gemma3").to_off(ctx, False)


class PackageTests(RootTestCase, LiveContextMixin):
    def test_remove_uses_pm_and_is_irreversible(self):
        runner = FakeRunner({("rpm",): Result(0, "ollama\n"), ("dnf",): Result(0)})
        ctx = self.live(self.root.context(runner))
        act = PackageRemove("ollama")
        self.assertTrue(act.present(ctx))
        rec = act.to_off(ctx, False)
        self.assertIn(["dnf", "-y", "remove", "ollama"], runner.calls)
        self.assertEqual(rec["reinstall"], "dnf install ollama")
        with self.assertRaises(ActionError):
            act.undo(ctx, rec)

    def test_offline_refuses(self):
        runner = FakeRunner({("rpm",): Result(0, "ollama\n")})
        with self.assertRaises(ActionError):
            PackageRemove("ollama").to_off(self.root.context(runner), False)

    def test_flatpak(self):
        runner = FakeRunner({("flatpak", "list"): Result(0, "com.jeffser.Alpaca\norg.x.Y\n"),
                             ("flatpak", "uninstall"): Result(0)})
        ctx = self.live(self.root.context(runner))
        act = FlatpakRemove("com.jeffser.Alpaca")
        self.assertTrue(act.present(ctx))
        self.assertFalse(FlatpakRemove("other.App").present(ctx))
        act.to_off(ctx, False)
        self.assertIn(["flatpak", "uninstall", "--system", "-y", "com.jeffser.Alpaca"], runner.calls)


if __name__ == "__main__":
    unittest.main()
