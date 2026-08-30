"""Root conftest.py — fix tmm_engine namespace-package shadowing.

When pytest runs from code/, Python's namespace-package resolution picks up
code/tmm_engine/ (which only holds test fixtures: materials/ + rii_cache.db)
as a namespace package and ignores the veritmm editable install.  Inserting
the veritmm source root at the front of sys.path before any imports happen
ensures `import tmm_engine` always resolves to the real package.
"""
from __future__ import annotations

import sys
from pathlib import Path

_VERITMM_ROOT = Path(__file__).resolve().parents[1] / "veritmm"
if _VERITMM_ROOT.exists() and str(_VERITMM_ROOT) not in sys.path:
    sys.path.insert(0, str(_VERITMM_ROOT))
