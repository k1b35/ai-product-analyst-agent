"""Синтетический мир «InvestSocial»: соцсеть для инвесторов с заранее известными инцидентами."""
from pathlib import Path
import os

ROOT = Path(__file__).resolve().parents[1]


def default_db_path() -> Path:
    return Path(os.getenv("PA_SQLITE_PATH") or ROOT / "data" / "world.db")
