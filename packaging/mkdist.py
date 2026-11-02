#!/usr/bin/env python3
"""Write dist/ai-switch-VERSION.tar.gz from the working tree (tracked and new, not ignored files).

``git archive`` would leave out uncommitted changes, which is the wrong thing while a
package is being tried. Reproducible: sorted names, fixed mtime/owner.
"""
import os
import re
import subprocess
import sys
import tarfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    with open(os.path.join(ROOT, "ai_switch", "__init__.py")) as f:
        version = re.search(r'__version__ = "(.+?)"', f.read()).group(1)
    names = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=ROOT,
                           stdout=subprocess.PIPE, check=True).stdout.decode().split("\0")
    names = sorted(n for n in names if n and os.path.isfile(os.path.join(ROOT, n)))
    os.makedirs(os.path.join(ROOT, "dist"), exist_ok=True)
    out = os.path.join(ROOT, "dist", "ai-switch-%s.tar.gz" % version)
    prefix = "ai-switch-%s/" % version
    with tarfile.open(out, "w:gz") as tar:
        for n in names:
            info = tar.gettarinfo(os.path.join(ROOT, n), arcname=prefix + n)
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            info.mtime = 0
            with open(os.path.join(ROOT, n), "rb") as f:
                tar.addfile(info, f)
    print(os.path.relpath(out, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
