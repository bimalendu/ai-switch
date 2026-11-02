"""GTK4 + libadwaita window for ai-switch (needs libadwaita 1.2 or newer).

All decisions live in gui_model.py and the CLI. This file only draws rows and
runs two commands without blocking the window:

* ``ai-switch --json status``                 (unprivileged, read-only)
* ``pkexec /usr/libexec/ai-switch-helper ...`` (root, behind a polkit prompt)

Environment, for development and tests only (the GUI itself is unprivileged):
AI_SWITCH_CMD replaces ``ai-switch``; AI_SWITCH_HELPER_CMD replaces the pkexec call.
"""

from __future__ import annotations

import sys
from typing import Dict, List, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from . import __version__, gui_model as gm  # noqa: E402
from .model import State  # noqa: E402

APP_ID = "org.aiswitch.AiSwitch"

BADGE_CSS = {State.ON: "warning", State.OFF: "success", State.OFF_LOCKED: "success",
             State.NOT_PRESENT: "dim-label", State.UNKNOWN: "dim-label"}


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, cli_cmd: List[str], helper_cmd: List[str]):
        super().__init__(application=app, title="AI Switch", default_width=620, default_height=700)
        self.cli_cmd, self.helper_cmd = cli_cmd, helper_cmd
        self.rows: List[gm.Row] = []
        self.touched: Dict[str, bool] = {}
        self.switches: Dict[str, Gtk.Switch] = {}
        self._group_rows: List[Gtk.Widget] = []
        self.busy = False
        self.last_error: Optional[str] = None

        # --- header ---
        self.title_widget = Adw.WindowTitle(title="AI Switch", subtitle="")
        header = Adw.HeaderBar(title_widget=self.title_widget)
        self.apply_button = Gtk.Button(label="Apply", sensitive=False)
        self.apply_button.add_css_class("suggested-action")
        self.apply_button.connect("clicked", lambda *_: self.apply())
        header.pack_start(self.apply_button)
        menu = Gio.Menu()
        menu.append("Revert all changes…", "win.revert")
        menu.append("About", "win.about")
        self.menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu)
        header.pack_end(self.menu_button)
        self.refresh_button = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Refresh")
        self.refresh_button.connect("clicked", lambda *_: self.refresh())
        header.pack_end(self.refresh_button)
        for name, cb in (("revert", self._confirm_revert), ("about", self._about)):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", cb)
            self.add_action(act)

        # --- pages: loading / list / error ---
        self.stack = Gtk.Stack(vexpand=True)
        spinner = Gtk.Spinner(spinning=True, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                              width_request=32, height_request=32)
        self.stack.add_named(spinner, "loading")

        self.page = Adw.PreferencesPage()
        self.group = Adw.PreferencesGroup(
            title="AI features",
            description="Switch off to turn a feature off for everyone on this computer. "
                        "Nothing changes until you press Apply.")
        self.page.add(self.group)
        options = Adw.PreferencesGroup(title="Options")
        lock_row = Adw.ActionRow(
            title="Lock where possible",
            subtitle="Stop people re-enabling a feature in the app's own settings (Firefox).")
        self.lock_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        lock_row.add_suffix(self.lock_switch)
        lock_row.set_activatable_widget(self.lock_switch)
        options.add(lock_row)
        self.page.add(options)
        self.stack.add_named(self.page, "list")

        self.error_page = Adw.StatusPage(icon_name="dialog-error-symbolic", title="Could not read the state")
        retry = Gtk.Button(label="Try again", halign=Gtk.Align.CENTER)
        retry.add_css_class("pill")
        retry.connect("clicked", lambda *_: self.refresh())
        self.error_page.set_child(retry)
        self.stack.add_named(self.error_page, "error")

        self.toasts = Adw.ToastOverlay(child=self.stack)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(header)
        box.append(self.toasts)
        self.set_content(box)
        self.refresh()

    # --- running commands without blocking the UI ---------------------------------

    def _run(self, argv: List[str], done) -> None:
        try:
            proc = Gio.Subprocess.new(
                argv, Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE)
        except GLib.Error as e:
            done(127, "", str(e))
            return

        def finished(p, res):
            try:
                _, out, err = p.communicate_utf8_finish(res)
            except GLib.Error as e:
                done(127, "", str(e))
                return
            done(p.get_exit_status(), out or "", err or "")

        proc.communicate_utf8_async(None, None, finished)

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.refresh_button.set_sensitive(not busy)
        self.menu_button.set_sensitive(not busy)
        self.page.set_sensitive(not busy)
        self._update_apply()

    # --- status ---------------------------------------------------------------------

    def refresh(self) -> None:
        self._set_busy(True)
        self.stack.set_visible_child_name("loading")
        self._run(self.cli_cmd + ["--json", "status"], self._status_done)

    def _status_done(self, code: int, out: str, err: str) -> None:
        try:
            if code != 0:
                raise ValueError(err.strip() or "ai-switch exited with status %d" % code)
            distro, rows = gm.parse_status(out)
        except (ValueError, KeyError) as e:
            self.last_error = str(e)
            self.error_page.set_description(self.last_error)
            self.stack.set_visible_child_name("error")
            self._set_busy(False)
            return
        self.last_error = None
        self.rows, self.touched = rows, {}
        self.title_widget.set_subtitle(distro)
        self._rebuild()
        self.stack.set_visible_child_name("list")
        self._set_busy(False)

    def _rebuild(self) -> None:
        for w in self._group_rows:
            self.group.remove(w)
        self._group_rows, self.switches = [], {}
        for r in self.rows:
            row = Adw.ActionRow(title=GLib.markup_escape_text(r.title),
                                subtitle=GLib.markup_escape_text(r.description),
                                tooltip_text=r.tooltip or None)
            row.set_name("row-" + r.id)
            badge = Gtk.Label(label=r.badge, valign=Gtk.Align.CENTER)
            badge.add_css_class("caption")
            badge.add_css_class(BADGE_CSS.get(r.state, "dim-label"))
            row.add_suffix(badge)
            if r.experimental:
                exp = Gtk.Label(label="experimental", valign=Gtk.Align.CENTER)
                exp.add_css_class("caption")
                exp.add_css_class("dim-label")
                row.add_suffix(exp)
            if not r.report_only:
                sw = Gtk.Switch(valign=Gtk.Align.CENTER, active=r.is_on, sensitive=r.can_toggle)
                sw.connect("notify::active", self._toggled, r.id)
                row.add_suffix(sw)
                row.set_activatable_widget(sw)
                self.switches[r.id] = sw
            self.group.add(row)
            self._group_rows.append(row)
        self._update_apply()

    def _toggled(self, sw: Gtk.Switch, _pspec, module_id: str) -> None:
        self.touched[module_id] = sw.get_active()
        self._update_apply()

    def pending(self):
        return gm.pending_changes(self.rows, self.touched)

    def _update_apply(self) -> None:
        off, on = self.pending()
        self.apply_button.set_sensitive(bool(off or on) and not self.busy)

    # --- apply / revert ---------------------------------------------------------------

    def apply(self) -> None:
        off, on = self.pending()
        if not (off or on) or self.busy:
            return
        argv = gm.apply_argv(self.helper_cmd, off, on, self.lock_switch.get_active())
        self._set_busy(True)
        self._run(argv, self._change_done)

    def _confirm_revert(self, *_args) -> None:
        dlg = Adw.MessageDialog(
            transient_for=self, heading="Revert all changes?",
            body="Restores each AI feature to how it was before AI Switch changed anything. "
                 "Software that was removed is not reinstalled.")
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("revert", "Revert")
        dlg.set_response_appearance("revert", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_default_response("cancel")
        dlg.connect("response", lambda d, resp: self._revert() if resp == "revert" else None)
        dlg.present()

    def _revert(self) -> None:
        self._set_busy(True)
        self._run(gm.revert_argv(self.helper_cmd), self._change_done)

    def _change_done(self, code: int, out: str, err: str) -> None:
        outcome = gm.parse_outcome(code, out, err)
        self._set_busy(False)
        if outcome.ok or outcome.cancelled:
            self.toasts.add_toast(Adw.Toast(title=outcome.summary, timeout=4))
        else:
            dlg = Adw.MessageDialog(transient_for=self, heading=outcome.summary,
                                    body="\n".join(outcome.messages) or "No details were returned.")
            dlg.add_response("ok", "OK")
            dlg.present()
        self.refresh()

    def _about(self, *_args) -> None:
        about = Adw.AboutWindow(transient_for=self, application_name="AI Switch",
                                application_icon="preferences-system", version=__version__,
                                comments="Turn system-level AI features on or off, and put things back.")
        about.present()


class Application(Adw.Application):
    def __init__(self) -> None:
        flags = getattr(Gio.ApplicationFlags, "DEFAULT_FLAGS", Gio.ApplicationFlags.FLAGS_NONE)
        super().__init__(application_id=APP_ID, flags=flags)
        self.window: Optional[MainWindow] = None

    def do_activate(self) -> None:
        if self.window is None:
            self.window = MainWindow(
                self,
                gm.command_from_env("AI_SWITCH_CMD", gm.DEFAULT_CLI),
                gm.command_from_env("AI_SWITCH_HELPER_CMD", gm.DEFAULT_HELPER))
        self.window.present()


def main() -> int:
    return Application().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
