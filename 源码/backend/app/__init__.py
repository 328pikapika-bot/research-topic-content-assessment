"""Application package bootstrap.

Load the project .env before submodules freeze module-level configuration.
This keeps direct script/test imports consistent with the FastAPI startup path.
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv


_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
if _ENV_PATH.exists():
    load_dotenv(_ENV_PATH)
