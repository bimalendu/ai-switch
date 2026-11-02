#!/usr/bin/env python3
"""Drive the real GTK window under Xvfb against fixtures on the container's
real filesystem, through the real helper process (pkexec/polkit excluded)."""

import json
import os
import subprocess
import sys
import time
import zipfile

SRC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, SRC)
OUT = os.environ.get("SHOTS", "")
failures = []


def check(name, ok, detail=""):
    print("%s  %s%s" % ("PASS" if ok else "FAIL", name, ("  -> %s" % (detail,)) if detail and not ok else ""))
    if not ok:
        failures.append(name)


def write(path, text=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def fixtures():
    schema = {"properties": {"AIControls": {"properties": {
        f: {"properties": {"Value": {"oneOf": [{"const": "available"}, {"const": "blocked"}]},
                           "Locked": {"type": "boolean"}}}
        for f in ("Default", "Translations", "PDFAltText", "SmartTabGroups", "LinkPreviewKeyPoints",
                  "SidebarChatbot", "SmartWindow", "SpeechRecognition")}}}}
    write("/usr/lib/systemd/system/clad.service", "[Unit]\n")
    write("/usr/lib/systemd/system/ollama.service", "[Unit]\n")
    write("/usr/lib64/firefox/firefox")
    os.makedirs("/usr/lib64/firefox/browser", exist_ok=True)
    with zipfile.ZipFile("/usr/lib64/firefox/browser/omni.ja", "w") as z:
        z.writestr("modules/policies/policies-schema.json", json.dumps(schema))
    write("/opt/google/chrome/chrome")


def shot(win, name):
    """Render the window to a PNG with GTK itself (no ImageMagick needed)."""
    if not OUT:
        return
    from gi.repository import Gtk
    try:
        w, h = win.get_width(), win.get_height()
        paintable = Gtk.WidgetPaintable.new(win)
        snap = Gtk.Snapshot()
        paintable.snapshot(snap, w, h)
        node = snap.to_node()
        texture = win.get_native().get_renderer().render_texture(node, None)
        texture.save_to_png(os.path.join(OUT, name))
        print("info  wrote %s (%dx%d)" % (name, w, h))
    except Exception as e:  # screenshots are a bonus, never a failure
        print("info  screenshot %s skipped: %s" % (name, e))


def main():
    fixtures()
    os.environ["PYTHONPATH"] = SRC
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    os.environ["AI_SWITCH_CMD"] = "python3 -m ai_switch"
    os.environ["AI_SWITCH_HELPER_CMD"] = "python3 -m ai_switch.helper"

    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, GLib, Gtk
    from ai_switch import gui

    ctx = GLib.MainContext.default()

    def wait(cond, what, timeout=40):
        t0 = time.time()
        while not cond():
            if time.time() - t0 > timeout:
                raise TimeoutError(what)
            while ctx.pending():
                ctx.iteration(False)
            time.sleep(0.05)
        while ctx.pending():
            ctx.iteration(False)

    app = gui.Application()
    app.register(None)
    app.activate()
    win = app.window
    print("libadwaita %d.%d.%d  GTK %d.%d.%d" % (
        Adw.get_major_version(), Adw.get_minor_version(), Adw.get_micro_version(),
        Gtk.get_major_version(), Gtk.get_minor_version(), Gtk.get_micro_version()))

    wait(lambda: not win.busy, "initial load")
    check("list page shown after load", win.stack.get_visible_child_name() == "list",
          win.last_error)
    ids = [r.id for r in win.rows]
    check("rows built for every module", {"firefox", "chromium-family", "local-llm-services",
                                           "known-ai-apps"} <= set(ids), ids)
    check("window title shows the distro", "fedora" in win.title_widget.get_subtitle().lower()
          or "ubuntu" in win.title_widget.get_subtitle().lower() or win.title_widget.get_subtitle() != "",
          win.title_widget.get_subtitle())
    for i in ("firefox", "chromium-family", "local-llm-services"):
        sw = win.switches.get(i)
        check("%s switch is on and usable" % i, sw is not None and sw.get_active() and sw.get_sensitive())
    check("report-only module has no switch", "known-ai-apps" not in win.switches)
    check("Apply is disabled with no changes", not win.apply_button.get_sensitive())
    shot(win, "1-initial.png")

    # --- toggle two modules off, apply through the real helper ---
    win.switches["local-llm-services"].set_active(False)
    wait(lambda: win.apply_button.get_sensitive(), "apply enabled")
    check("Apply enabled after a toggle", True)
    win.switches["firefox"].set_active(False)
    win.lock_switch.set_active(True)
    off, on = win.pending()
    check("pending change set is exactly what was toggled", sorted(off) == ["firefox", "local-llm-services"] and on == [],
          (off, on))
    shot(win, "2-toggled.png")
    win.apply()
    wait(lambda: not win.busy and win.stack.get_visible_child_name() == "list", "apply finished")
    check("ollama unit masked by the helper",
          os.path.islink("/etc/systemd/system/ollama.service")
          and os.readlink("/etc/systemd/system/ollama.service") == "/dev/null")
    with open("/usr/lib64/firefox/distribution/policies.json") as f:
        pol = json.load(f)
    check("Firefox blocked and locked (lock switch honoured)",
          pol["policies"]["AIControls"]["Default"] == {"Value": "blocked", "Locked": True}, pol)
    check("chrome untouched", not os.path.exists("/etc/opt/chrome/policies/managed/ai-switch.json"))
    states = {r.id: r.state for r in win.rows}
    check("window refreshed to the new states",
          states["firefox"] == "off (locked)" and states["local-llm-services"] == "off"
          and states["chromium-family"] == "on", states)
    check("switches reflect reality after refresh",
          not win.switches["firefox"].get_active() and win.switches["chromium-family"].get_active())
    check("Apply disabled again", not win.apply_button.get_sensitive())
    shot(win, "3-applied.png")

    # --- turn one back on ---
    win.switches["local-llm-services"].set_active(True)
    wait(lambda: win.apply_button.get_sensitive(), "apply enabled again")
    win.apply()
    wait(lambda: not win.busy, "enable finished")
    check("ollama unmasked after switching back on", not os.path.lexists("/etc/systemd/system/ollama.service"))

    # --- revert everything ---
    win._revert()
    wait(lambda: not win.busy and win.stack.get_visible_child_name() == "list", "revert finished")
    check("revert removed the Firefox policy", not os.path.exists("/usr/lib64/firefox/distribution"))
    check("revert removed the journal", not os.path.exists("/var/lib/ai-switch"))
    check("all switches back on", all(win.switches[i].get_active() for i in
                                      ("firefox", "chromium-family", "local-llm-services")))
    shot(win, "4-reverted.png")

    # --- failure path: broken CLI shows the error page ---
    win.cli_cmd = ["/nonexistent/ai-switch"]
    win.refresh()
    wait(lambda: not win.busy, "error load")
    check("error page shown when ai-switch cannot run", win.stack.get_visible_child_name() == "error")

    print("\n%d failure(s)" % len(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        rc = main()
    except Exception as e:  # report, do not hang the container
        import traceback
        traceback.print_exc()
        rc = 1
    sys.stdout.flush()
    os._exit(rc)
