# ---------------------------------------------------------------------------
# File:        startup/init_db.py
# Version:     0.1
# Date:        2026-07-11
# Author:      Scott Douglass
# Description: One-time script that applies schema.sql to create/upgrade
#              survey.db.
# ---------------------------------------------------------------------------
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import init_db

if __name__ == "__main__":
    init_db()
    print("Initialised survey.db")
