#!/usr/bin/env python3
"""End-to-end check inside a distro container (run by run-matrix.sh).

Builds synthetic AI "installs" (unit files, a fake Firefox with a schema, a
fake Chrome), then drives the real CLI as a subprocess: status, disable,
reconcile, enable, revert. The real package manager of the distro answers the
package queries. Revert must leave the watched trees byte-identical.

Usage: matrix.py EXPECTED_PKG_MANAGER
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile

SRC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WATCH = ["/etc", "/opt", "/usr/lib/systemd", "/usr/lib/firefox", "/usr/lib64/firefox", "/var/lib/ai-switch"]
failures = []


def check(name, ok, detail=""):
    print("%s  %s%s" % ("PASS" if ok else "FAIL", name, ("  -> " + str(detail)) if detail and not ok else ""))
    if not ok:
        failures.append(name)


def cli(*args, expect=0):
    env = dict(os.environ, PYTHONPATH=SRC, PYTHONDONTWRITEBYTECODE="1")
    p = subprocess.run([sys.executable, "-m", "ai_switch"] + list(args), env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    if expect is not None and p.returncode != expect:
        print("  cli %s exited %d (wanted %d)\n  stdout: %s\n  stderr: %s" % (
            " ".join(args), p.returncode, expect, p.stdout[-600:], p.stderr[-600:]))
    return p


def status():
    p = cli("--json", "status")
    return json.loads(p.stdout)


def tree_hash():
    """Map of path -> digest for the watched trees (journal excluded)."""
    out = {}
    for top in WATCH:
        for dirpath, dirnames, filenames in os.walk(top):
            if dirpath.startswith("/var/lib/ai-switch"):
                continue
            out[dirpath] = "dir"
            for f in filenames:
                p = os.path.join(dirpath, f)
                if os.path.islink(p):
                    out[p] = "link:" + os.readlink(p)
                else:
                    try:
                        with open(p, "rb") as fh:
                            out[p] = hashlib.sha256(fh.read()).hexdigest()
                    except OSError:
                        out[p] = "unreadable"
            for d in dirnames:
                p = os.path.join(dirpath, d)
                if os.path.islink(p):
                    out[p] = "link:" + os.readlink(p)
    return out


def same_tree(before):
    now = tree_hash()
    if now == before:
        return True
    for k in sorted(set(before) | set(now)):
        if before.get(k) != now.get(k):
            print("  tree differs: %s  before=%s now=%s" % (k, before.get(k), now.get(k)))
    return False


def write(path, text=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


SCHEMA = {"properties": {"AIControls": {"properties": {
    f: {"properties": {"Value": {"oneOf": [{"const": "available"}, {"const": "blocked"}]},
                       "Locked": {"type": "boolean"}}}
    for f in ("Default", "Translations", "PDFAltText", "SmartTabGroups",
              "LinkPreviewKeyPoints", "SidebarChatbot", "SmartWindow", "SpeechRecognition")}}}}


def main():
    want_pm = sys.argv[1]
    osr = dict(l.rstrip().split("=", 1) for l in open("/etc/os-release") if "=" in l)
    distro = osr.get("ID", "").strip('"')
    ids = {distro} | set(osr.get("ID_LIKE", "").strip('"').split())
    print("== %s %s (python %s) pm=%s" % (distro, osr.get("VERSION_ID", ""), sys.version.split()[0], want_pm))

    rpm_family = want_pm in ("dnf", "zypper")
    fx_dir = "/usr/lib64/firefox" if rpm_family else "/usr/lib/firefox"
    debish = bool(ids & {"debian", "ubuntu"})
    fx_policy = "/etc/firefox/policies/policies.json" if debish else fx_dir + "/distribution/policies.json"

    # --- fixtures -----------------------------------------------------------
    write("/usr/lib/systemd/system/clad.service", "[Unit]\nDescription=fixture\n")
    write("/usr/lib/systemd/system/ollama.service", "[Unit]\nDescription=fixture\n")
    write(fx_dir + "/firefox", "")
    os.makedirs(fx_dir + "/browser", exist_ok=True)
    with zipfile.ZipFile(fx_dir + "/browser/omni.ja", "w") as z:
        z.writestr("modules/policies/policies-schema.json", json.dumps(SCHEMA))
    write("/opt/google/chrome/chrome", "")
    write("/etc/opt/chrome/policies/managed/admin.json", '{"HomepageLocation": "https://example.org"}\n')
    if debish:
        write("/etc/firefox/policies/policies.json", '{"policies": {"DisableAppUpdate": true}}\n')

    # --- real package manager ------------------------------------------------
    sys.path.insert(0, SRC)
    from ai_switch.context import Context
    ctx = Context("/")
    check("distro maps to expected package manager", ctx.pkg_manager == want_pm, ctx.pkg_manager)
    pkgs = ctx.installed_packages()
    check("real package query returns packages", len(pkgs) > 5, len(pkgs))
    check("real package query sees bash or coreutils", bool(pkgs & {"bash", "coreutils"}), sorted(pkgs)[:5])
    for probe in ("systemd", "util-linux"):
        if probe in pkgs:
            files = ctx.package_files(probe)
            check("package_files(%s) lists files" % probe, len(files) > 10, len(files))
            units = ctx.units_of_packages([probe])
            check("units_of_packages(%s) finds units" % probe, bool(units) or probe == "util-linux", units[:3])
            break

    # --- status before -------------------------------------------------------
    initial = tree_hash()
    st = {m["id"]: m for m in status()["modules"]}
    check("firefox detected and on", st.get("firefox", {}).get("state") == "on", st.get("firefox"))
    check("chromium-family detected and on", st.get("chromium-family", {}).get("state") == "on", st.get("chromium-family"))
    check("local-llm-services detected and on", st.get("local-llm-services", {}).get("state") == "on", st.get("local-llm-services"))
    if ids & {"rhel", "fedora", "centos"}:
        check("rhel-lightspeed applies and on", st.get("rhel-lightspeed", {}).get("state") == "on", st.get("rhel-lightspeed"))
    else:
        check("rhel-lightspeed hidden on this distro", "rhel-lightspeed" not in st)
    check("suse-ai only on SUSE", ("suse-ai" in st) == bool(ids & {"suse", "opensuse", "sles"}))
    check("deepin only on deepin", ("deepin-uos-ai" in st) == ("deepin" in ids))
    check("status wrote nothing", same_tree(initial))

    p = cli("disable", "--all", "--dry-run")
    check("dry run exits 0 and changes nothing", p.returncode == 0 and same_tree(initial))
    p = cli("disable", "firefox", expect=None)
    check("refuses to change without --yes (no tty)", p.returncode == 2 and same_tree(initial), p.returncode)

    # --- disable all ---------------------------------------------------------
    p = cli("disable", "--all", "--lock", "--yes")
    check("disable --all --lock succeeds", p.returncode == 0, p.returncode)
    st = {m["id"]: m for m in status()["modules"]}
    check("firefox off (locked)", st["firefox"]["state"] == "off (locked)", st["firefox"])
    check("chromium-family off (locked)", st["chromium-family"]["state"] == "off (locked)", st["chromium-family"])
    check("local-llm-services off", st["local-llm-services"]["state"] == "off", st["local-llm-services"])
    with open(fx_policy) as f:
        data = json.load(f)
    check("firefox AIControls written to the right file for this distro",
          data["policies"]["AIControls"]["Default"] == {"Value": "blocked", "Locked": True}, fx_policy)
    if debish:
        check("existing Firefox admin policy preserved", data["policies"].get("DisableAppUpdate") is True)
    with open("/etc/opt/chrome/policies/managed/ai-switch.json") as f:
        chrome = json.load(f)
    check("chrome policy has documented disabling values",
          chrome["GeminiSettings"] == 1 and chrome["HelpMeWriteSettings"] == 2, chrome)
    check("admin chrome policy untouched",
          os.path.exists("/etc/opt/chrome/policies/managed/admin.json"))
    check("ollama masked via symlink to /dev/null",
          os.path.islink("/etc/systemd/system/ollama.service")
          and os.readlink("/etc/systemd/system/ollama.service") == "/dev/null")
    if shutil.which("systemctl"):
        r = subprocess.run(["systemctl", "--root=/", "is-enabled", "ollama.service"],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        check("real systemctl agrees the unit is masked", r.stdout.strip() == "masked", (r.stdout, r.stderr))
    else:
        print("SKIP  real systemctl check (no systemctl in image)")

    # --- reconcile after a simulated package update ---------------------------
    os.remove("/etc/systemd/system/ollama.service")
    check("status notices the undone mask",
          {m["id"]: m for m in status()["modules"]}["local-llm-services"]["state"] == "on")
    p = cli("reconcile")
    check("reconcile restores it", p.returncode == 0 and os.path.islink("/etc/systemd/system/ollama.service"), p.stdout)
    p = cli("reconcile")
    check("second reconcile is a no-op", "Nothing to reconcile" in p.stdout, p.stdout)

    # --- enable then disable again ---------------------------------------------
    p = cli("enable", "--all", "--yes")
    check("enable --all succeeds", p.returncode == 0, p.stdout)
    st = {m["id"]: m for m in status()["modules"]}
    check("everything on after enable",
          all(st[i]["state"] == "on" for i in ("firefox", "chromium-family", "local-llm-services")),
          {i: st[i]["state"] for i in ("firefox", "chromium-family", "local-llm-services")})
    check("reconcile leaves 'on' choices alone", "Nothing to reconcile" in cli("reconcile").stdout)
    check("disable again works", cli("disable", "--all", "--yes").returncode == 0)

    # --- revert ----------------------------------------------------------------
    p = cli("revert", "--yes")
    check("revert succeeds", p.returncode == 0, p.stdout)
    check("tree is byte-identical to before ai-switch", same_tree(initial))
    check("journal removed", not os.path.exists("/var/lib/ai-switch"))
    check("second revert has nothing to do", "Nothing to revert" in cli("revert", "--yes").stdout)

    print("\n%d failure(s)" % len(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
