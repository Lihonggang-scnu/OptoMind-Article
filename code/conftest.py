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


# O-12: confirmation gates default to a 30s human wait; in the test suite
# the "human" is always silent, so clamp the wait to 1s globally (production
# behaviour is untouched -- this fixture only exists under pytest).
import pytest


@pytest.fixture(autouse=True)
def _clamp_confirmation_wait(monkeypatch):
    from optomind_optics.harness import confirmation_gate as _cg

    original_init = _cg.ConfirmationGate.__init__

    def clipped_init(self, run_dir, *, timeout_seconds=30, **kwargs):
        original_init(
            self,
            run_dir,
            timeout_seconds=min(int(timeout_seconds), 1),
            **kwargs,
        )

    monkeypatch.setattr(_cg.ConfirmationGate, "__init__", clipped_init)
    yield
