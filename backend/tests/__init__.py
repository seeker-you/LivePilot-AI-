"""Unit tests for the LivePilot backend.

Point every test run at a throwaway SQLite DB before any ``app`` import so
tests never touch the demo database.
"""

import os
import tempfile
from pathlib import Path

_TEST_DB = Path(tempfile.gettempdir()) / "livepilot_unittest.db"
_TEST_DB.unlink(missing_ok=True)
os.environ["DATABASE_PATH"] = str(_TEST_DB)
