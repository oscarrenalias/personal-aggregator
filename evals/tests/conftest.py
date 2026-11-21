"""pytest configuration for evals/tests.

Adds the evals/ directory to sys.path so that clusterer_merge and shared
packages are importable without installing them.
"""

import os
import sys
from pathlib import Path

# aggregator_common.db builds Settings() at import time; a DATABASE_URL must be
# present even when no real DB connection is made (evals tests are DB-free).
os.environ.setdefault("DATABASE_URL", "postgresql://placeholder@localhost/aggregator")

# evals/ directory (parent of this tests/ directory)
_EVALS_DIR = Path(__file__).parent.parent
if str(_EVALS_DIR) not in sys.path:
    sys.path.insert(0, str(_EVALS_DIR))
