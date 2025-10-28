# config.py
import os

OWNER_ID = int(os.getenv("OWNER_ID"))
STRIPE_KEY = os.getenv("STRIPE_KEY")
BOT_TOKEN = os.getenv("BOT_TOKEN")
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
