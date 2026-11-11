from pathlib import Path
import os
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env")

DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = BASE_DIR / "storage" / "uploads"
DB_PATH = DATA_DIR / "knowledge_nexus.db"

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
OCR_LANGUAGES = os.getenv("OCR_LANGUAGES", "eng")
AUTH_COOKIE_SECURE = os.getenv("AUTH_COOKIE_SECURE", "false").lower() == "true"
