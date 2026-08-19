# Vetta - proprietary software. Copyright (c) 2026 Anoop Shekhar.
# Public to read, not to use. Copying, modification, deployment or commercial
# use requires written permission: thisisanoopshekhar89@gmail.com
"""Build Vetta.exe — a single-file Windows executable.

    python app/build_exe.py

The result lands in dist/Vetta.exe and needs no Python on the target machine.
Templates are bundled with --add-data; PyMuPDF ships compiled binaries that
PyInstaller finds on its own, so no --collect-all is required for it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "app", "app.py")
TEMPLATES = os.path.join(ROOT, "app", "templates")
SEP = ";" if os.name == "nt" else ":"


def main() -> int:
    if not os.path.exists(APP):
        print("cannot find %s" % APP, file=sys.stderr)
        return 2

    for d in ("build", "dist"):
        p = os.path.join(ROOT, d)
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onefile",
        "--name", "Vetta",
        "--add-data", "%s%s%s" % (TEMPLATES, SEP, "templates"),
        # Flask/Jinja discover these late, so name them explicitly.
        "--hidden-import", "jinja2",
        "--hidden-import", "vetta",
        "--hidden-import", "vetta.cli",
        "--hidden-import", "vetta.dashboard",
        "--hidden-import", "vetta.pipeline",
        "--hidden-import", "vetta.quality",
        "--hidden-import", "vetta.store",
        "--hidden-import", "vetta.pdfreport",
        "--hidden-import", "reportlab.pdfbase._fontdata",
        "--paths", ROOT,
        # reportlab is only used by the sample generator, not the app.
        # Excluded deliberately: none of these are used by the app, but they get
        # pulled in transitively and cost hundreds of MB in a onefile build.
        "--exclude-module", "tkinter",
        "--exclude-module", "pytest",
        # PIL is NOT excluded: reportlab imports it, and the app builds PDF reports.
        "--exclude-module", "numpy",
        "--exclude-module", "pandas",
        "--exclude-module", "scipy",
        "--exclude-module", "matplotlib",
        "--exclude-module", "IPython",
        "--exclude-module", "notebook",
        "--exclude-module", "sqlalchemy",
        "--exclude-module", "playwright",
        "--exclude-module", "fontTools",
        "--exclude-module", "win32com",
        "--exclude-module", "pythoncom",
        "--exclude-module", "setuptools",
        "--strip",
        APP,
    ]
    print("building…\n  %s\n" % " ".join(cmd[2:]))
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0:
        return r.returncode

    exe = os.path.join(ROOT, "dist", "Vetta.exe")
    if os.path.exists(exe):
        print("\nbuilt: %s  (%.1f MB)" % (exe, os.path.getsize(exe) / 1048576))
        print("Double-click it: a browser opens on http://127.0.0.1:5099/")
    else:
        print("\nbuild finished but %s is missing" % exe, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
