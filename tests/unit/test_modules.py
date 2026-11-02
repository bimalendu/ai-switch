import io
import json
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout

from ai_switch import cli, engine, registry
from ai_switch.context import FakeRunner, Result
from ai_switch.journal import Journal
from ai_switch.model import ActionError, State
from ai_switch.modules.chromium import BROWSERS, ChromiumFamily
from ai_switch.modules.firefox import Firefox, validate
from ai_switch.modules.known_apps import KnownAiApps, parse_list
from ai_switch.modules.services import (LOCAL_LLM, RHEL_LIGHTSPEED, DEEPIN_AI,
                                        UbuntuInference)
from tests.unit.helpers import FakeRoot, LiveContextMixin, RootTestCase

SCHEMA = {"properties": {"AIControls": {"properties": {
    f: {"properties": {"Value": {"oneOf": [{"const": "available"}, {"const": "blocked"}]},
                       "Locked": {"type": "boolean"}}}
    for f in ("Default", "Translations", "PDFAltText", "SmartTabGroups", "LinkPreviewKeyPoints",
              "SidebarChatbot", "SmartWindow", "SpeechRecognition")}}}}


def make_omni(root, install_dir, schema=SCHEMA):
    p = root.path(install_dir + "/browser/omni.ja")
    p.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(str(p), "w") as z:
        z.writestr("modules/policies/policies-schema.json", json.dumps(schema))
    return p


class FirefoxTests(RootTestCase):
    def test_fedora_layout_roundtrip(self):
        make_omni(self.root, "/usr/lib64/firefox")
        self.root.write("/usr/lib64/firefox/firefox", "")
        ctx, mod = self.root.context(), Firefox()
        self.assertEqual(mod.status(ctx).state, State.ON)
        before = self.root.snapshot(ignore_journal=False)
        journal = Journal(ctx)
        plan = engine.plan_change(ctx, [mod], engine.OP_OFF)
        self.assertEqual(plan.items[0].action.path, "/usr/lib64/firefox/distribution/policies.json")
        self.assertTrue(all(r.ok for r in engine.apply_plan(ctx, journal, plan)))
        self.assertEqual(mod.status(ctx).state, State.OFF)
        written = json.loads(self.root.path("/usr/lib64/firefox/distribution/policies.json").read_text())
        self.assertEqual(written, {"policies": {"AIControls": {"Default": {"Value": "blocked", "Locked": False}}}})
        engine.revert(ctx, journal, [mod], [])
        self.assertEqual(self.root.snapshot(ignore_journal=False), before)

    def test_lock(self):
        make_omni(self.root, "/usr/lib64/firefox")
        ctx, mod = self.root.context(), Firefox()
        plan = engine.plan_change(ctx, [mod], engine.OP_OFF, lock=True)
        engine.apply_plan(ctx, Journal(ctx), plan, lock=True)
        self.assertEqual(mod.status(ctx).state, State.OFF_LOCKED)

    def test_old_firefox_without_aicontrols_is_refused(self):
        make_omni(self.root, "/usr/lib64/firefox", {"properties": {"Homepage": {}}})
        ctx, mod = self.root.context(), Firefox()
        plan = engine.plan_change(ctx, [mod], engine.OP_OFF)
        results = engine.apply_plan(ctx, Journal(ctx), plan)
        self.assertFalse(results[0].ok)
        self.assertIn("AIControls", results[0].message)
        self.assertFalse(self.root.path("/usr/lib64/firefox/distribution").exists())

    def test_validate_flags_unknown_feature_and_value(self):
        make_omni(self.root, "/usr/lib64/firefox")
        bad = {"policies": {"AIControls": {"Nope": {"Value": "blocked"},
                                           "Default": {"Value": "maybe", "Extra": 1}}}}
        problems = validate(self.root.context(), "/usr/lib64/firefox", bad)
        self.assertEqual(len(problems), 3)

    def test_validate_skips_when_schema_unreadable(self):
        self.assertEqual(validate(self.root.context(), "/usr/lib64/firefox", {}), [])

    def test_all_known_features_validate(self):
        make_omni(self.root, "/usr/lib64/firefox")
        payload = {"policies": {"AIControls": {
            f: {"Value": "blocked", "Locked": False} for f in SCHEMA["properties"]["AIControls"]["properties"]}}}
        self.assertEqual(validate(self.root.context(), "/usr/lib64/firefox", payload), [])

    def test_debian_uses_etc_policies(self):
        root = FakeRoot("debian")
        self.addCleanup(root.cleanup)
        root.write("/usr/lib/firefox-esr/firefox-esr", "")
        root.write("/usr/lib/firefox/firefox", "")
        paths = sorted(a.path for a in Firefox().actions(root.context()) if hasattr(a, "path"))
        self.assertEqual(paths, ["/etc/firefox-esr/policies/policies.json",
                                 "/etc/firefox/policies/policies.json"])

    def test_snap_build_is_experimental_and_unvalidated(self):
        root = FakeRoot("ubuntu")
        self.addCleanup(root.cleanup)
        root.write("/var/lib/snapd/snaps/firefox_1234.snap", "x")
        ctx, mod = root.context(), Firefox()
        plan = engine.plan_change(ctx, [mod], engine.OP_OFF)
        self.assertEqual(len(plan.items), 1)
        self.assertTrue(plan.items[0].action.experimental)
        self.assertTrue(all(r.ok for r in engine.apply_plan(ctx, Journal(ctx), plan)))

    def test_flatpak_is_reported_unknown_and_never_written(self):
        self.root.write("/var/lib/flatpak/app/org.mozilla.firefox/x", "")
        ctx, mod = self.root.context(), Firefox()
        self.assertEqual(mod.status(ctx).state, State.UNKNOWN)
        plan = engine.plan_change(ctx, [mod], engine.OP_OFF)
        self.assertEqual(plan.items, [])
        self.assertIn("Flatpak", plan.notes[0])

    def test_not_present(self):
        self.assertEqual(Firefox().status(self.root.context()).state, State.NOT_PRESENT)

    def test_existing_enterprise_policy_preserved(self):
        make_omni(self.root, "/usr/lib64/firefox")
        orig = '{"policies": {"DisableAppUpdate": true}}\n'
        self.root.write("/usr/lib64/firefox/distribution/policies.json", orig)
        ctx, mod = self.root.context(), Firefox()
        j = Journal(ctx)
        engine.apply_plan(ctx, j, engine.plan_change(ctx, [mod], engine.OP_OFF))
        data = json.loads(self.root.path("/usr/lib64/firefox/distribution/policies.json").read_text())
        self.assertTrue(data["policies"]["DisableAppUpdate"])
        engine.revert(ctx, j, [mod], [])
        self.assertEqual(self.root.path("/usr/lib64/firefox/distribution/policies.json").read_text(), orig)


class ChromiumTests(RootTestCase):
    def test_only_installed_browsers(self):
        self.root.write("/opt/google/chrome/chrome", "")
        self.root.write("/opt/brave.com/brave/brave", "")
        ctx, mod = self.root.context(), ChromiumFamily()
        plan = engine.plan_change(ctx, [mod], engine.OP_OFF)
        self.assertEqual(sorted(i.action.path for i in plan.items), [
            "/etc/brave/policies/managed/ai-switch.json",
            "/etc/opt/chrome/policies/managed/ai-switch.json"])
        self.assertEqual([i.action.experimental for i in plan.items if "brave" in i.action.path], [True])

    def test_roundtrip_and_status(self):
        self.root.write("/usr/lib64/chromium-browser/chromium-browser", "")
        ctx, mod = self.root.context(), ChromiumFamily()
        before = self.root.snapshot(ignore_journal=False)
        j = Journal(ctx)
        engine.apply_plan(ctx, j, engine.plan_change(ctx, [mod], engine.OP_OFF))
        self.assertEqual(mod.status(ctx).state, State.OFF_LOCKED)
        data = json.loads(self.root.path("/etc/chromium/policies/managed/ai-switch.json").read_text())
        self.assertEqual(data["GeminiSettings"], 1)
        self.assertEqual(data["HelpMeWriteSettings"], 2)
        self.assertNotIn("GenAiDefaultSettings", data)
        engine.revert(ctx, j, [mod], [])
        self.assertEqual(self.root.snapshot(ignore_journal=False), before)

    def test_policy_values_use_documented_types(self):
        for b in BROWSERS:
            for k, v in b.policies.items():
                self.assertIn(type(v), (int, bool), k)


class ServiceModuleTests(RootTestCase):
    def test_rhel_lightspeed_candidate_units(self):
        self.root.unit("clad.service")
        ctx = self.root.context()
        self.assertEqual(RHEL_LIGHTSPEED.status(ctx).state, State.ON)
        plan = engine.plan_change(ctx, [RHEL_LIGHTSPEED], engine.OP_OFF)
        self.assertEqual([i.action.key for i in plan.items], ["systemd:clad.service"])

    def test_applies_by_distro(self):
        for distro, expect in (("fedora", True), ("rhel", True), ("rocky", True),
                               ("ubuntu", False), ("debian", False)):
            r = FakeRoot(distro)
            self.addCleanup(r.cleanup)
            self.assertEqual(RHEL_LIGHTSPEED.applies(r.context()), expect, distro)
        for distro, expect in (("sles", True), ("opensuse", True), ("fedora", False)):
            r = FakeRoot(distro)
            self.addCleanup(r.cleanup)
            from ai_switch.modules.services import SUSE_AI
            self.assertEqual(SUSE_AI.applies(r.context()), expect, distro)

    def test_units_discovered_from_package_database(self):
        root = FakeRoot("deepin")
        self.addCleanup(root.cleanup)
        root.unit("uos-ai-agent.service", "/lib/systemd/system")
        runner = FakeRunner({
            ("dpkg-query", "--admindir", str(root.path("/var/lib/dpkg")), "-W"):
                Result(0, "uos-ai install ok installed\nbash install ok installed\n"),
            ("dpkg-query", "--admindir", str(root.path("/var/lib/dpkg")), "-L"):
                Result(0, "/.\n/usr/bin/uos-ai\n/lib/systemd/system/uos-ai-agent.service\n"
                          "/lib/systemd/system/getty@.service\n"),
        })
        ctx = root.context(runner)
        plan = engine.plan_change(ctx, [DEEPIN_AI], engine.OP_OFF)
        self.assertEqual([i.action.key for i in plan.items], ["systemd:uos-ai-agent.service"])
        self.assertTrue(plan.items[0].action.experimental)

    def test_local_llm_masks_only_existing_units(self):
        self.root.unit("ollama.service", "/etc/systemd/system")
        # An admin-written unit lives where the mask would go: must refuse, not clobber.
        ctx = self.root.context()
        results = engine.apply_plan(ctx, Journal(ctx), engine.plan_change(ctx, [LOCAL_LLM], engine.OP_OFF))
        self.assertEqual([r.ok for r in results], [False])
        self.assertIn("refusing to overwrite", results[0].message)
        self.assertEqual(self.root.path("/etc/systemd/system/ollama.service").read_text(),
                         "[Unit]\nDescription=test\n")

    def test_local_llm_vendor_unit_masked_and_reverted(self):
        self.root.unit("ollama.service")
        ctx = self.root.context()
        before = self.root.snapshot(ignore_journal=False)
        j = Journal(ctx)
        engine.apply_plan(ctx, j, engine.plan_change(ctx, [LOCAL_LLM], engine.OP_OFF))
        self.assertEqual(LOCAL_LLM.status(ctx).state, State.OFF)
        engine.revert(ctx, j, [LOCAL_LLM], [])
        self.assertEqual(self.root.snapshot(ignore_journal=False), before)


class UbuntuInferenceTests(RootTestCase, LiveContextMixin):
    distro = "ubuntu"

    def test_disable_installed_snaps_only(self):
        listing = "Name Version Rev Tracking Publisher Notes\ngemma3 1 5 latest canonical -\nhello 1 1 latest x -\n"
        runner = FakeRunner({("snap", "list"): Result(0, listing), ("snap", "disable"): Result(0)})
        ctx = self.live(self.root.context(runner))
        mod = UbuntuInference()
        self.assertEqual(mod.status(ctx).state, State.ON)
        plan = engine.plan_change(ctx, [mod], engine.OP_OFF)
        self.assertEqual([i.action.key for i in plan.items], ["snap:gemma3"])
        engine.apply_plan(ctx, Journal(ctx), plan)
        self.assertIn(["snap", "disable", "gemma3"], runner.calls)

    def test_hidden_on_non_ubuntu_without_snapd(self):
        r = FakeRoot("fedora")
        self.addCleanup(r.cleanup)
        self.assertFalse(UbuntuInference().applies(r.context()))


class KnownAppsTests(RootTestCase, LiveContextMixin):
    def test_parse_list(self):
        got = parse_list("any: a  # c\nrpm: b*\nflatpak: x.Y\n# no\njunk\nwat: z\n")
        self.assertEqual(got, [("any", "a"), ("rpm", "b*"), ("flatpak", "x.Y")])

    def test_packaged_list_loads(self):
        kinds = {k for k, _ in KnownAiApps().entries}
        self.assertEqual(kinds, {"any", "rpm", "flatpak"})

    def test_rpm_entries_only_on_rpm_distros(self):
        pkgs = "ollama\ngoose\n"
        fed = self.root.context(FakeRunner({("rpm",): Result(0, pkgs)}))
        self.assertEqual(KnownAiApps().status(fed).detail, "installed: goose, ollama")
        deb = FakeRoot("debian")
        self.addCleanup(deb.cleanup)
        dctx = deb.context(FakeRunner({("dpkg-query",): Result(0, "ollama install ok installed\n"
                                                                    "goose install ok installed\n")}))
        # Debian's "goose" is a database migration tool: must not be flagged.
        self.assertEqual(KnownAiApps().status(dctx).detail, "installed: ollama")

    def test_report_only_and_purge(self):
        runner = FakeRunner({("rpm",): Result(0, "ollama\n"), ("dnf",): Result(0),
                             ("flatpak", "list"): Result(0, "com.jeffser.Alpaca\n"),
                             ("flatpak", "uninstall"): Result(0)})
        ctx = self.live(self.root.context(runner))
        mod = KnownAiApps()
        self.assertEqual(mod.status(ctx).state, State.ON)
        self.assertEqual(engine.plan_change(ctx, [mod], engine.OP_OFF).items, [])
        plan = engine.plan_change(ctx, [mod], engine.OP_OFF, purge=True)
        self.assertEqual(sorted(i.action.key for i in plan.items),
                         ["flatpak:com.jeffser.Alpaca", "pkg:ollama"])
        self.assertTrue(plan.irreversible)


class WholeRegistry(unittest.TestCase):
    def test_every_distro_status_and_dry_run_never_crash_and_never_write(self):
        from tests.unit.helpers import OS_RELEASES
        for distro in OS_RELEASES:
            root = FakeRoot(distro)
            self.addCleanup(root.cleanup)
            before = root.snapshot(ignore_journal=False)
            for argv in (["status"], ["--json", "status"], ["disable", "--all", "--dry-run"],
                         ["disable", "--all", "--purge", "--dry-run"]):
                out, err = io.StringIO(), io.StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    code = cli.main(argv, ctx=root.context())
                self.assertEqual(code, 0, (distro, argv, err.getvalue()))
            self.assertEqual(root.snapshot(ignore_journal=False), before, distro)

    def test_module_ids_unique_and_documented(self):
        mods = registry.all_modules()
        ids = [m.id for m in mods]
        self.assertEqual(len(ids), len(set(ids)))
        with open("docs/catalog.md", encoding="utf-8") as f:
            catalog = f.read()
        for i in ids:
            self.assertIn(i, catalog, "module %s missing from docs/catalog.md" % i)

    def test_full_disable_all_and_revert_is_byte_identical(self):
        root = FakeRoot("rhel")
        self.addCleanup(root.cleanup)
        make_omni(root, "/usr/lib64/firefox")
        root.write("/usr/lib64/firefox/firefox", "")
        root.write("/opt/google/chrome/chrome", "")
        root.write("/etc/opt/chrome/policies/managed/other.json", '{"X": 1}\n')
        root.unit("clad.service")
        root.unit("clad.socket")
        root.unit("ollama.service")
        before = root.snapshot(ignore_journal=False)
        ctx = root.context()
        self.assertEqual(cli.main(["disable", "--all", "--lock", "--yes"], ctx=root.context()), 0)
        states = {m.id: m.status(ctx).state for m in registry.applicable(ctx)}
        self.assertEqual(states["firefox"], State.OFF_LOCKED)
        self.assertEqual(states["chromium-family"], State.OFF_LOCKED)
        self.assertEqual(states["rhel-lightspeed"], State.OFF)
        self.assertEqual(states["local-llm-services"], State.OFF)
        self.assertEqual(cli.main(["revert", "--yes"], ctx=root.context()), 0)
        self.assertEqual(root.snapshot(ignore_journal=False), before)


if __name__ == "__main__":
    unittest.main()
