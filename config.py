# bot settings
import json
from os import getenv
from os.path import join, dirname
from types import SimpleNamespace

BOT_TOKEN = getenv("BOT_TOKEN", "")
CURRENCY = getenv("CURRENCY", "")
PROVIDER_TOKEN = getenv("PROVIDER_TOKEN", "")
MANAGERS_CHAT = int(getenv("MANAGERS_CHAT", ""))
ADMIN_IDS = list(map(int, getenv("ADMIN_IDS", "").split(",")))
REFUND_PERCENTS = float(getenv("REFUND_PERCENTS", 1))

# database settings
DATABASE = getenv("MYSQL_DATABASE", "")
DB_HOST = getenv("MYSQL_HOST", "")
DB_PORT = int(getenv("MYSQL_PORT", "3306"))
DB_USER = getenv("MYSQL_USER", "")
DB_PASSWORD = getenv("MYSQL_PASSWORD", "")

# files settings
UPLOAD_BASE = 'images'
STATE_FILE_NAME = "states.json"

# admin panel settings
admin_user = "booking_admin"
admin_secret_key = 'm6awtIc05xC0HPx7OjGkyY4RHDQJIddbfUN'
admin_password = '19kt2lAoFpBtXu3q4fSxyQwbYaI3wLvZlEF'
lock_by_ip, allow_ip_list = False, []

# ui settings
RECORD_PER_PAGE, RECORDS_ROWS = int(getenv("RECORD_PER_PAGE", "6")), int(getenv("RECORDS_ROWS", "2"))
LANGUAGE = getenv("LANGUAGE", "ru")

with open(join(dirname(__file__), "language.json"), "r", encoding="utf-8") as f:
    languages = SimpleNamespace(**{k: SimpleNamespace(**v) for k, v in json.load(f).items()})
assert hasattr(languages, LANGUAGE), f"Language {LANGUAGE} not allowed!"
texts = getattr(languages, LANGUAGE)
