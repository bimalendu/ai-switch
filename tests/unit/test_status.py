import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout

from ai_switch import cli
from ai_switch.model import (Action, Module, Observation, State, OBS_OFF,
                             OBS_OFF_LOCKED, OBS_ON, OBS_UNKNOWN)
from tests.unit.helpers import RootTestCase


class StubAction(Action):
    def __init__(self, key, present=True, obs=OBS_ON):
        super().__init__(key, key)
        self._present, self._obs = present, obs

    def present(self, ctx):
        return self._present

    def read(self, ctx):
        return Observation(self._obs, self._obs)


class StubModule(Module):
    def __init__(self, id, acts, **kw):
        super().__init__(id, "Title " + id, "desc", **kw)
        self._acts = acts

    def actions(self, ctx):
        return self._acts


class Aggregation(RootTestCase):
    def state(self, *specs):
        acts = [StubAction("a%d" % i, p, o) for i, (p, o) in enumerate(specs)]
        return StubModule("m", acts).status(self.root.context()).state

    def test_not_present(self):
        self.assertEqual(self.state(), State.NOT_PRESENT)
        self.assertEqual(self.state((False, OBS_ON)), State.NOT_PRESENT)

    def test_any_on_wins(self):
        self.assertEqual(self.state((True, OBS_OFF), (True, OBS_ON)), State.ON)
        self.assertEqual(self.state((True, OBS_UNKNOWN), (True, OBS_ON)), State.ON)

    def test_unknown_beats_off(self):
        self.assertEqual(self.state((True, OBS_OFF), (True, OBS_UNKNOWN)), State.UNKNOWN)

    def test_all_locked(self):
        self.assertEqual(self.state((True, OBS_OFF_LOCKED), (True, OBS_OFF_LOCKED)),
                         State.OFF_LOCKED)

    def test_mixed_off_is_plain_off(self):
        self.assertEqual(self.state((True, OBS_OFF_LOCKED), (True, OBS_OFF)), State.OFF)


class StatusCommand(RootTestCase):
    def run_cli(self, argv, mods):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(argv, ctx=self.root.context(), modules=mods)
        return code, out.getvalue(), err.getvalue()

    def test_table(self):
        mods = [StubModule("one", [StubAction("a")]),
                StubModule("two", [], experimental=True)]
        code, out, _ = self.run_cli(["status"], mods)
        self.assertEqual(code, 0)
        self.assertIn("one", out)
        self.assertIn("not present", out)
        self.assertIn("[experimental]", out)

    def test_json(self):
        code, out, _ = self.run_cli(["--json", "status"], [StubModule("one", [StubAction("a")])])
        data = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(data["distro"], "fedora")
        self.assertEqual(data["modules"][0]["state"], "on")

    def test_unknown_id(self):
        code, _, err = self.run_cli(["status", "nope"], [StubModule("one", [])])
        self.assertEqual(code, 2)
        self.assertIn("unknown module: nope", err)

    def test_does_not_modify_root(self):
        before = self.root.snapshot(ignore_journal=False)
        self.run_cli(["status"], [StubModule("one", [StubAction("a")])])
        self.assertEqual(before, self.root.snapshot(ignore_journal=False))

    def test_inapplicable_module_hidden(self):
        class Never(StubModule):
            def applies(self, ctx):
                return False

        _, out, _ = self.run_cli(["status"], [Never("hidden", [])])
        self.assertNotIn("hidden", out)


if __name__ == "__main__":
    unittest.main()
