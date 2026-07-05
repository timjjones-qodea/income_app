from __future__ import annotations

import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("RIE_DATA_DIR", BASE_DIR / "data"))
UPLOAD_DIR = Path(os.getenv("RIE_UPLOAD_DIR", DATA_DIR / "uploads"))
DATABASE_URL = os.getenv("RIE_DATABASE_URL", f"sqlite:///{DATA_DIR / 'retirement_income.db'}")


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

