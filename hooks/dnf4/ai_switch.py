"""dnf 4 plugin (RHEL 9/10, Fedora before 41): re-apply ai-switch choices
after every transaction. dnf 5 uses hooks/dnf5/ai-switch.actions instead.

The helper never fails and does nothing unless ai-switch changed something,
so this cannot break a transaction; any error here is swallowed on purpose.
"""

import subprocess

import dnf

HELPER = "/usr/libexec/ai-switch-hook"


class AiSwitch(dnf.Plugin):
    name = "ai-switch"

    def transaction(self):
        # Skip image builds (--installroot): the host's choices do not apply.
        if self.base.conf.installroot not in ("/", ""):
            return
        try:
            subprocess.call([HELPER], stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass
