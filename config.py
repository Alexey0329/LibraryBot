"""Configuration for Flibusta Telegram Bot."""
import os
from dotenv import load_dotenv

load_dotenv()

# Telegram Bot Token
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Flibusta settings
FLIBUSTA_BASE_URL = "http://flibusta.is"

# Pagination settings
ITEMS_PER_PAGE = 5  # Number of items to show per page in bot

# Request settings
REQUEST_TIMEOUT = 90  # seconds

# User Agent for HTTP requests
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
