"""Drop-in Team B entry point for the Team A repository.

The project is intentionally runnable directly from a checkout (``python
main.py``), so the vendored ``src`` package is added to ``sys.path`` here
instead of requiring an editable install just to import the plugin.
"""

from pathlib import Path
import sys


_PROJECT_ROOT = Path(__file__).absolute().parent.parent
_SRC_ROOT = _PROJECT_ROOT / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from llm_cipher.security.team_a_adapter import (
    PLUGIN_API_VERSION,
    PLUGIN_NAME,
    evaluate_security,
)


evaluate = evaluate_security


__all__ = ["PLUGIN_API_VERSION", "PLUGIN_NAME", "evaluate_security", "evaluate"]
