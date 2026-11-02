import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout

from ai_switch import cli, engine
from ai_switch.actions.packages import PackageRemove
from ai_switch.actions.systemd import SystemdMask
from ai_switch.context import FakeRunner, Result
from ai_switch.journal import Journal, JournalError
from ai_switch.model import Module, State
from tests.unit.helpers import LiveContextMixin, RootTestCase


class UnitModule(Module):
    def __init__(self, id, units, purge=(), **kw):
        super().__init__(id, "T " + id, "d", **kw)
        self.units = units
        self.purge = purge

    def actions(self, ctx):
        return [SystemdMask(u) for u in self.units]

    def purge_actions(self, ctx):
        return [PackageRemove(p) for p in self.purge]


class EngineCase(RootTestCase):
    def setUp(self):
        super().setUp()
        self.root.unit("a.service")
        self.root.unit("b.service")
        self.ctx = self.root.context()
        self.mod = UnitModule("m", ["a.service", "b.service"])
        self.journal = Journal(self.ctx)

    def disable(self, mods=None, **kw):
        plan = engine.plan_change(self.ctx, mods or [self.mod], engine.OP_OFF, **kw)
        return engine.apply_plan(self.ctx, self.journal, plan, lock=kw.get("lock", False))


class DisableRevert(EngineCase):
    def test_disable_then_revert_leaves_tree_identical(self):
        before = self.root.snapshot()
        results = self.disable()
        self.assertTrue(all(r.ok for r in results))
        self.assertEqual(self.mod.status(self.ctx).state, State.OFF)
        self.assertEqual(self.journal.desired()["m"], {"state": "off", "lock": False})
        results = engine.revert(self.ctx, self.journal, [self.mod], [])
        self.assertTrue(all(r.ok for r in results))
        self.assertEqual(self.root.snapshot(ignore_journal=False), before)
        self.assertFalse(self.root.path("/var/lib/ai-switch").exists())

    def test_toggle_many_times_still_reverts_to_original(self):
        before = self.root.snapshot()
        for _ in range(3):
            self.disable()
            plan = engine.plan_change(self.ctx, [self.mod], engine.OP_ON)
            engine.apply_plan(self.ctx, self.journal, plan)
        self.assertEqual(self.mod.status(self.ctx).state, State.ON)
        self.assertEqual(self.journal.desired()["m"]["state"], "on")
        engine.revert(self.ctx, self.journal, [self.mod], [])
        self.assertEqual(self.root.snapshot(ignore_journal=False), before)

    def test_premasked_unit_stays_masked_after_revert(self):
        import os
        self.root.mkdir("/etc/systemd/system")
        os.symlink("/dev/null", str(self.root.path("/etc/systemd/system/a.service")))
        before = self.root.snapshot()
        self.disable()
        engine.revert(self.ctx, self.journal, [self.mod], [])
        self.assertEqual(self.root.snapshot(ignore_journal=False), before)

    def test_journal_survives_reload(self):
        self.disable()
        again = Journal(self.ctx)
        self.assertEqual(len(again.entries("m")), 2)
        results = engine.revert(self.ctx, again, [self.mod], ["m"])
        self.assertTrue(all(r.ok for r in results))

    def test_partial_failure_reports_and_does_not_set_desired(self):
        self.root.write("/etc/systemd/system/b.service", "[Service]\n")  # admin override
        results = self.disable()
        self.assertEqual([r.ok for r in results], [True, False])
        self.assertNotIn("m", self.journal.desired())
        # The one that worked is journaled and can be reverted.
        engine.revert(self.ctx, self.journal, [self.mod], ["m"])
        self.assertFalse(self.ctx.unit_masked("a.service"))

    def test_revert_unknown_action_keeps_entry(self):
        self.disable()
        empty = UnitModule("m", [])
        results = engine.revert(self.ctx, self.journal, [empty], ["m"])
        self.assertTrue(all(not r.ok for r in results))
        self.assertEqual(len(self.journal.entries("m")), 2)

    def test_revert_when_nothing_journaled(self):
        results = engine.revert(self.ctx, self.journal, [self.mod], ["m"])
        self.assertEqual(results[0].message, "nothing to revert")


class Plans(EngineCase):
    def test_not_present_module_is_a_note(self):
        plan = engine.plan_change(self.ctx, [UnitModule("x", ["zzz.service"])], engine.OP_OFF)
        self.assertEqual(plan.items, [])
        self.assertIn("not present", plan.notes[0])

    def test_report_only_never_plans_changes(self):
        mod = UnitModule("r", ["a.service"], report_only=True)
        plan = engine.plan_change(self.ctx, [mod], engine.OP_OFF)
        self.assertEqual(plan.items, [])
        self.assertIn("report-only", plan.notes[0])

    def test_purge_adds_irreversible_items_only_when_asked(self):
        ctx = self.root.context(FakeRunner({("rpm",): Result(0, "ollama\n")}))
        mod = UnitModule("llm", ["a.service"], purge=["ollama"])
        plain = engine.plan_change(ctx, [mod], engine.OP_OFF)
        self.assertFalse(plain.irreversible)
        purged = engine.plan_change(ctx, [mod], engine.OP_OFF, purge=True)
        self.assertTrue(purged.irreversible)

    def test_report_only_with_purge_plans_removals(self):
        ctx = self.root.context(FakeRunner({("rpm",): Result(0, "ollama\n")}))
        mod = UnitModule("apps", [], purge=["ollama"], report_only=True)
        plan = engine.plan_change(ctx, [mod], engine.OP_OFF, purge=True)
        self.assertEqual([i.action.key for i in plan.items], ["pkg:ollama"])


class PurgeJournal(RootTestCase, LiveContextMixin):
    def test_irreversible_entry_is_reported_and_dropped_on_revert(self):
        runner = FakeRunner({("rpm",): Result(0, "ollama\n"), ("dnf",): Result(0)})
        ctx = self.live(self.root.context(runner))
        mod = UnitModule("llm", [], purge=["ollama"])
        journal = Journal(ctx)
        plan = engine.plan_change(ctx, [mod], engine.OP_OFF, purge=True)
        results = engine.apply_plan(ctx, journal, plan)
        self.assertTrue(results[0].ok)
        out = engine.revert(ctx, journal, [mod], ["llm"])
        self.assertIn("dnf install ollama", out[0].message)
        self.assertEqual(journal.entries(), [])


class Reconcile(EngineCase):
    def test_reapplies_after_update_replaced_state(self):
        self.disable()
        # A package update removes our mask symlinks.
        for u in ("a.service", "b.service"):
            self.root.path("/etc/systemd/system/" + u).unlink()
        self.assertEqual(self.mod.status(self.ctx).state, State.ON)
        results = engine.reconcile(self.ctx, self.journal, [self.mod])
        self.assertTrue(results and all(r.ok for r in results))
        self.assertEqual(self.mod.status(self.ctx).state, State.OFF)

    def test_does_nothing_when_still_off_or_user_wants_on(self):
        self.disable()
        self.assertEqual(engine.reconcile(self.ctx, self.journal, [self.mod]), [])
        plan = engine.plan_change(self.ctx, [self.mod], engine.OP_ON)
        engine.apply_plan(self.ctx, self.journal, plan)
        self.assertEqual(engine.reconcile(self.ctx, self.journal, [self.mod]), [])
        self.assertEqual(self.mod.status(self.ctx).state, State.ON)


class JournalFile(RootTestCase):
    def test_corrupt_journal_is_refused(self):
        self.root.write("/var/lib/ai-switch/state.json", "{nope")
        with self.assertRaises(JournalError):
            Journal(self.root.context())

    def test_wrong_version_is_refused(self):
        self.root.write("/var/lib/ai-switch/state.json", '{"version": 99}')
        with self.assertRaises(JournalError):
            Journal(self.root.context())

    def test_first_before_wins(self):
        j = Journal(self.root.context())
        self.assertTrue(j.record("m", "k", "x", {"v": 1}))
        self.assertFalse(j.record("m", "k", "x", {"v": 2}))
        self.assertEqual(Journal(self.root.context()).find("m", "k")["before"], {"v": 1})

    def test_locked_context_manager(self):
        j = Journal(self.root.context())
        with j.locked():
            j.record("m", "k", "x", {})
        self.assertTrue(self.root.path("/var/lib/ai-switch/state.json").exists())


class CliChange(RootTestCase):
    def setUp(self):
        super().setUp()
        self.root.unit("a.service")
        self.mods = [UnitModule("m", ["a.service"]),
                     UnitModule("gone", ["zzz.service"]),
                     UnitModule("exp", [], experimental=True)]

    def run_cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(argv, ctx=self.root.context(), modules=self.mods)
        return code, out.getvalue(), err.getvalue()

    def test_dry_run_changes_nothing(self):
        before = self.root.snapshot(ignore_journal=False)
        code, out, _ = self.run_cli(["disable", "m", "--dry-run"])
        self.assertEqual(code, 0)
        self.assertIn("mask a.service", out)
        self.assertIn("Dry run", out)
        self.assertEqual(self.root.snapshot(ignore_journal=False), before)

    def test_requires_yes_when_not_a_tty(self):
        code, _, err = self.run_cli(["disable", "m"])
        self.assertEqual(code, 2)
        self.assertIn("--yes", err)
        self.assertFalse(self.root.path("/etc/systemd/system/a.service").is_symlink())

    def test_disable_status_revert_via_cli(self):
        before = self.root.snapshot(ignore_journal=False)
        self.assertEqual(self.run_cli(["disable", "m", "--yes"])[0], 0)
        _, out, _ = self.run_cli(["--json", "status", "m"])
        self.assertEqual(json.loads(out)["modules"][0]["state"], "off")
        self.assertEqual(self.run_cli(["revert", "--yes"])[0], 0)
        self.assertEqual(self.root.snapshot(ignore_journal=False), before)

    def test_disable_all_skips_absent(self):
        code, out, _ = self.run_cli(["disable", "--all", "--yes"])
        self.assertEqual(code, 0)
        self.assertIn("gone: not present", out)
        self.assertTrue(self.root.path("/etc/systemd/system/a.service").is_symlink())

    def test_usage_errors(self):
        self.assertEqual(self.run_cli(["disable"])[0], 2)
        self.assertEqual(self.run_cli(["disable", "nope", "--yes"])[0], 2)
        self.assertEqual(self.run_cli(["disable", "m", "--all"])[0], 2)
        self.assertEqual(self.run_cli(["frobnicate"])[0], 2)

    def test_failure_exit_code(self):
        self.root.write("/etc/systemd/system/a.service", "[Service]\n")
        code, out, _ = self.run_cli(["disable", "m", "--yes"])
        self.assertEqual(code, 1)
        self.assertIn("FAIL", out)

    def test_json_output_of_plan(self):
        code, out, _ = self.run_cli(["--json", "disable", "m", "--dry-run"])
        data = json.loads(out)
        self.assertTrue(data["dry_run"])
        self.assertEqual(data["plan"][0]["action"], "systemd:a.service")

    def test_revert_with_nothing_journaled(self):
        code, out, _ = self.run_cli(["revert", "--yes"])
        self.assertEqual(code, 0)
        self.assertIn("Nothing to revert", out)

    def test_needs_root_on_live_system(self):
        ctx = LiveContextMixin.live(self.root.context())
        import os
        real = os.geteuid
        os.geteuid = lambda: 1000
        try:
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = cli.main(["disable", "m", "--yes"], ctx=ctx, modules=self.mods)
        finally:
            os.geteuid = real
        self.assertEqual(code, 3)
        self.assertFalse(self.root.path("/etc/systemd/system/a.service").is_symlink())

    def test_experimental_note(self):
        class Exp(UnitModule):
            pass
        self.mods = [Exp("e", ["a.service"], experimental=True)]
        _, out, _ = self.run_cli(["disable", "e", "--dry-run"])
        self.assertIn("experimental", out)


if __name__ == "__main__":
    unittest.main()
