"""Selected OptoMind research utilities required by the TMM handoff package.

The full literature-review system is intentionally not bundled here.  The
included modules provide scoped knowledge-base lookup, Semantic Scholar text
retrieval, artifact writing, cost accounting, publication integrity checks,
and LaTeX/PDF rendering for the optical TMM runtime.
"""

# Optional public Review adapters may be installed alongside the minimal handoff.
# Keep local accepted utilities; do not vendor or copy the Review runtime.
from pkgutil import extend_path
__path__ = extend_path(__path__, __name__)
