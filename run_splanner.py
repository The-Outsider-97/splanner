"""``python applications/splanner/run_splanner.py`` (or double-click) starts SPlanner; the same as ``python -m applications.splanner``."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))          # the SLAI root, so ``applications`` and ``src`` import

from applications.splanner.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
