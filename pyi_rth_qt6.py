"""Make the PyQt6 wheel's nested Qt DLL directory visible to Windows."""
from __future__ import annotations

import os
import sys


if sys.platform.startswith("win"):
    _qt_bin = os.path.join(sys._MEIPASS, "PyQt6", "Qt6", "bin")
    if os.path.isdir(_qt_bin):
        os.environ["PATH"] = _qt_bin + os.pathsep + os.environ.get("PATH", "")
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(_qt_bin)
