Name:           ai-switch
Version:        0.1.0
Release:        1%{?dist}
Summary:        Explicitly turn system-level AI features on or off, and put things back
License:        GPL-3.0-or-later
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch

BuildRequires:  make
BuildRequires:  python3-devel
BuildRequires:  systemd-rpm-macros
Requires:       python3 >= 3.9
%if 0%{?fedora}
# dnf 5 runs the reconcile hook through its actions plugin.
Requires:       libdnf5-plugin-actions
%endif

%description
ai-switch finds the AI features built into a Linux system (browser AI policies,
vendor assistants, local LLM services) and lets you turn each one off or on with
one explicit command. Every change is journaled with the state before it, so
"ai-switch revert" puts the system back exactly as it was. Nothing changes
without a command; packages are removed only with --purge.

Package-manager hooks re-apply your choices after updates. They only act when
ai-switch has changed something on the machine.

%package gui
Summary:        Window for ai-switch, with a polkit-guarded helper
Requires:       %{name} = %{version}-%{release}
Requires:       polkit
%if 0%{?suse_version}
Requires:       python3-gobject
Requires:       typelib-1_0-Gtk-4_0
Requires:       typelib-1_0-Adw-1
%else
Requires:       python3-gobject
Requires:       gtk4
Requires:       libadwaita
%endif

%description gui
A GTK 4 / libadwaita window with one switch per AI feature. It runs without
privileges and changes things through pkexec and /usr/libexec/ai-switch-helper,
which accepts module ids only (no --purge, --root or --all).

%prep
%autosetup

%install
%if 0%{?fedora}
%global aisw_hooks dnf5
%endif
%if 0%{?rhel}
%global aisw_hooks dnf4
%endif
%if 0%{?suse_version}
%global aisw_hooks zypper
%endif
make install-core install-hooks install-gui DESTDIR=%{buildroot} \
    PYTHON=%{__python3} PYLIB=%{python3_sitelib} \
    DNF4_PLUGDIR=%{python3_sitelib}/dnf-plugins HOOKS="%{aisw_hooks}"

%check
%{__python3} -m unittest discover -s tests/unit -t .

%preun
if [ $1 -eq 0 ] && [ -s /var/lib/ai-switch/state.json ]; then
    echo "ai-switch: removing the package keeps the changes it made (masked units, policy files)." >&2
    echo "ai-switch: to undo them, reinstall it and run 'ai-switch revert'." >&2
fi
%systemd_preun ai-switch-reconcile.timer ai-switch-reconcile.service

%postun
%systemd_postun ai-switch-reconcile.timer ai-switch-reconcile.service

%files
%license LICENSE
%doc README.md docs/
%{_bindir}/ai-switch
%{python3_sitelib}/ai_switch/
%{_libexecdir}/ai-switch-hook
%{_unitdir}/ai-switch-reconcile.service
%{_unitdir}/ai-switch-reconcile.timer
%if 0%{?fedora}
%config(noreplace) %{_sysconfdir}/dnf/libdnf5-plugins/actions.d/ai-switch.actions
%endif
%if 0%{?rhel}
%{python3_sitelib}/dnf-plugins/ai_switch.py
%{python3_sitelib}/dnf-plugins/__pycache__/ai_switch.*
%config(noreplace) %{_sysconfdir}/dnf/plugins/ai-switch.conf
%endif
%if 0%{?suse_version}
%dir %{_prefix}/lib/zypp/plugins
%dir %{_prefix}/lib/zypp/plugins/commit
%{_prefix}/lib/zypp/plugins/commit/ai-switch
%endif

%files gui
%{_bindir}/ai-switch-gui
%{_libexecdir}/ai-switch-helper
%{_datadir}/polkit-1/actions/org.aiswitch.apply.policy
%{_datadir}/applications/org.aiswitch.AiSwitch.desktop

%changelog
* Mon Nov 02 2026 ai-switch packagers <ai-switch@localhost> - 0.1.0-1
- Initial local package
