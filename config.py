import os
from dotenv import load_dotenv

load_dotenv()

# Токены и настройки
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID", "123456789"))

# Планы подписок
SUBSCRIPTION_PLANS = {
    "free": {
        "name": "Free",
        "price": 0,
        "days": 7,
        "features": ["basic_recipes", "limited_ai", "ads"]
    },
    "basic": {
        "name": "Basic",
        "price": 299,
        "days": 30,
        "features": ["all_recipes", "meal_plans", "shopping_lists", "unlimited_ai", "no_ads"]
    },
    "pro": {
        "name": "Pro",
        "price": 599,
        "days": 30,
        "features": ["all_recipes", "personal_plans", "smart_substitutes", "priority_support", "pdf_export", "family_mode"]
    }
}

# Лимиты
FREE_AI_QUESTIONS_PER_DAY = 5
FREE_DAYS_VISIBLE = 7