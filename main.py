"""Entry point for Vercel: it looks for a FastAPI instance named `app` in main.py.

The application itself lives in src/skoolathome/api/.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from skoolathome.api.app import app  # noqa: E402

__all__ = ["app"]
