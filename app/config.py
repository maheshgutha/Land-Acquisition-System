"""Runtime configuration. Everything can be overridden with environment variables."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

DB_URL = os.getenv("LANDSCAN_DB", f"sqlite:///{BASE_DIR / 'landscan.db'}")
SECRET_KEY = os.getenv("LANDSCAN_SECRET", "dev-only-change-me")
TOKEN_HOURS = int(os.getenv("LANDSCAN_TOKEN_HOURS", "8"))
STORAGE_DIR = Path(os.getenv("LANDSCAN_STORAGE", str(BASE_DIR / "storage")))
MODEL_DIR = Path(os.getenv("LANDSCAN_MODELS", str(BASE_DIR / "models")))
MAX_UPLOAD_MB = int(os.getenv("LANDSCAN_MAX_UPLOAD_MB", "10"))
# Land-acquisition documents are scans, photos, or office files - not executables or archives.
ALLOWED_UPLOAD_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".doc", ".docx", ".xls", ".xlsx", ".txt"}

# Seed synthetic demo data on first start when the database is empty.
AUTOSEED = os.getenv("LANDSCAN_AUTOSEED", "1") == "1"
# Expose the demo user list on the login screen. Turn off outside demos.
DEMO_MODE = os.getenv("LANDSCAN_DEMO", "1") == "1"

STORAGE_DIR.mkdir(parents=True, exist_ok=True)
MODEL_DIR.mkdir(parents=True, exist_ok=True)
