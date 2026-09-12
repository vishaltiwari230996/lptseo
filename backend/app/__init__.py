"""SEO agent backend package — registers the SEO agent on sys.path.

Extracted from the AgentOS backend. The original registers five department
agents here; this copy carries only the one.
"""

from __future__ import annotations

import sys
from pathlib import Path

# The agent lives under "agents/<Agent Name>/<package>". The folder name
# contains spaces, so we put the agent root on sys.path and import the
# underscore-named package inside it.
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_AGENT_ROOTS = [
    _BACKEND_ROOT / "agents" / "SEO GEO agent",
]
for _root in _AGENT_ROOTS:
    if _root.is_dir() and str(_root) not in sys.path:
        sys.path.insert(0, str(_root))
