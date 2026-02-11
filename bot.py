import logging
import re
import os
import asyncio
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, PreCheckoutQuery
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    MessageHandler, filters, ContextTypes, ConversationHandler
)
from sqlalchemy import select, func, update

from config import TELEGRAM_TOKEN, ADMIN_ID, SUBSCRIPTION_PLANS, FREE_AI_QUESTIONS_PER_DAY, FREE_DAYS_VISIBLE
from database import init_db, async_session
from models import User, Recipe, Favorite, MealPlan, CookingHistory
from data_loader import load_recipes, YOUR_MEALS_DATA

# Логирование
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Состояния для ConversationHandler
AI_CHAT = 1

# ═══════════════════════════════════════════════════════════════
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ═══════════════════════════════════════════════════════════════

async def get_or_create_user(telegram_id: int, username: str, first_name: str) -> User:
    """Получить или создать пользователя"""
    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == telegram_id)
        )
        user = result.scalar_one_or_none()

        if not user:
            user = User(
                telegram_id=telegram_id,
                username=username,
                first_name=first_name,
                subscription_expires=datetime.utcnow() + timedelta(days=3)
            )
            session.add(user)
            await session.commit()
            logger.info(f"Новый пользователь: {first_name} ({telegram_id})")

        user.last_active = datetime.utcnow()
        await session.commit()

        return user

def check_subscription(user: User) -> dict:
    """Проверить статус подписки"""
    now = datetime.utcnow()

    if user.subscription_expires and user.subscription_expires > now:
        return {
            "active": True,
            "type": user.subscription_type,
            "expires": user.subscription_expires,
            "days_left": (user.subscription_expires - now).days
        }
    else:
        return {
            "active": False,
            "type": "expired",
            "days_left": 0
        }

def can_view_day(user: User, day: int) -> bool:
    """Может ли пользователь видеть этот день"""
    sub = check_subscription(user)

    if sub["active"]:
        return True

    return day <= FREE_DAYS_VISIBLE

def can_use_ai(user: User) -> bool:
    """Может ли пользователь использовать AI сейчас"""
    sub = check_subscription(user)

    if sub["active"] and sub["type"] in ["basic", "pro"]:
        return True

    now = datetime.utcnow()

    if user.ai_questions_reset.date() != now.date():
        user.ai_questions_today = 0
        user.ai_questions_reset = now

    return user.ai_questions_today < FREE_AI_QUESTIONS_PER_DAY

# ═══════════════════════════════════════════════════════════════
# ГЛАВНОЕ МЕНЮ
# ═══════════════════════════════════════════════════════════════

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, edit=False):
    """Показать главное меню"""
    user = update.effective_user

    db_user = await get_or_create_user(user.id, user.username, user.first_name)
    sub = check_subscription(db_user)

    text = f"""👋 Привет, {user.first_name}!

🍽 Это MaybeCook — твой персональный план питания на 30 дней!

✅ Что внутри:
• Полные рецепты с граммовкой
• Списки закупок
• Всё для аэрогриля
• AI-помощник

🎁 У тебя {sub['days_left']} дней пробного доступа!
"""

    keyboard = [
        [InlineKeyboardButton("📅 План на день", callback_data='menu_day'),
         InlineKeyboardButton("🔥 Аэрогриль", callback_data='aeroguide')],
        [InlineKeyboardButton("🛒 Закупки", callback_data='shopping'),
         InlineKeyboardButton("⭐ Избранное", callback_data='favorites')],
        [InlineKeyboardButton("🤖 AI Помощник", callback_data='ask_ai'),
         InlineKeyboardButton("💎 Подписка", callback_data='subscription')],
        [InlineKeyboardButton("❓ Помощь", callback_data='help')]
    ]

    if edit and update.callback_query:
        await update.callback_query.edit_message_text(
            text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        if update.message:
            await update.message.reply_text(
                text,
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        elif update.callback_query:
            await update.callback_query.message.reply_text(
                text,
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Старт бота"""
    await show_main_menu(update, context, edit=False)

# ═══════════════════════════════════════════════════════════════
# МЕНЮ ДНЕЙ
# ═══════════════════════════════════════════════════════════════

async def show_days_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать меню выбора дня"""
    query = update.callback_query
    await query.answer()

    user = await get_or_create_user(
        update.effective_user.id,
        update.effective_user.username,
        update.effective_user.first_name
    )

    keyboard = []

    for week in range(5):
        row = []
        for day_offset in range(7):
            day_num = week * 7 + day_offset + 1
            if day_num <= 30:
                if can_view_day(user, day_num):
                    row.append(InlineKeyboardButton(
                        str(day_num), 
                        callback_data=f'day_{day_num}'
                    ))
                else:
                    row.append(InlineKeyboardButton(
                        "🔒", 
                        callback_data=f'locked_{day_num}'
                    ))
        if row:
            keyboard.append(row)

    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data='back_main')])

    sub = check_subscription(user)
    if not sub["active"]:
        text = f"📅 Выбери день (1-{FREE_DAYS_VISIBLE} бесплатно):\n\n🔒 Дни {FREE_DAYS_VISIBLE+1}-30 доступны по подписке!"
    else:
        text = "📅 Выбери день (1-30):"

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ═══════════════════════════════════════════════════════════════
# КОНКРЕТНЫЙ ДЕНЬ
# ═══════════════════════════════════════════════════════════════

async def show_day(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать конкретный день"""
    query = update.callback_query
    await query.answer()

    day = int(query.data.split('_')[1])
    context.user_data['current_day'] = day

    keyboard = [
        [InlineKeyboardButton("🌅 Завтрак", callback_data=f'meal_{day}_breakfast'),
         InlineKeyboardButton("🍽 Обед", callback_data=f'meal_{day}_lunch')],
        [InlineKeyboardButton("☕ Полдник", callback_data=f'meal_{day}_snack'),
         InlineKeyboardButton("🌙 Ужин", callback_data=f'meal_{day}_dinner')],
        [InlineKeyboardButton("🛒 Закупки на день", callback_data=f'shopday_{day}')],
        [InlineKeyboardButton("📊 Итого день", callback_data=f'total_{day}')],
        [InlineKeyboardButton("🔙 К дням", callback_data='menu_day')]
    ]

    await query.edit_message_text(
        f"📅 *ДЕНЬ {day}*\n\nВыбери приём пищи:",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ═══════════════════════════════════════════════════════════════
# ПОКАЗ БЛЮДА
# ═══════════════════════════════════════════════════════════════

async def show_meal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать рецепт"""
    query = update.callback_query
    await query.answer()

    parts = query.data.split('_')
    day = int(parts[1])
    meal_type = parts[2]

    async with async_session() as session:
        result = await session.execute(
            select(Recipe).where(
                Recipe.day_number == day,
                Recipe.meal_type == meal_type
            )
        )
        recipe = result.scalar_one_or_none()

        if not recipe:
            await query.edit_message_text("❌ Рецепт не найден")
            return

    context.user_data['current_recipe'] = recipe.title

    text = f"{recipe.title}\n\n"
    text += f"{recipe.shopping}\n\n"
    text += f"{recipe.portion}\n\n"
    text += f"{recipe.recipe}\n\n"
    text += f"{recipe.calories_text}"

    keyboard = [
        [InlineKeyboardButton("⭐ В избранное", callback_data=f'fav_{day}_{meal_type}')],
        [InlineKeyboardButton("✅ Я приготовил!", callback_data=f'cooked_{day}_{meal_type}')],
        [InlineKeyboardButton("🤖 Вопрос про блюдо", callback_data='ask_ai_recipe')],
        [InlineKeyboardButton("🔙 Назад", callback_data=f'day_{day}')]
    ]

    if len(text) > 4000:
        await query.edit_message_text(
            text[:4000] + "...",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await query.edit_message_text(
            text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

# ═══════════════════════════════════════════════════════════════
# ИЗБРАННОЕ
# ═══════════════════════════════════════════════════════════════

async def add_to_favorites(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Добавить в избранное"""
    query = update.callback_query
    await query.answer()

    user = await get_or_create_user(
        update.effective_user.id,
        update.effective_user.username,
        update.effective_user.first_name
    )

    parts = query.data.split('_')
    day = int(parts[1])
    meal_type = parts[2]

    async with async_session() as session:
        result = await session.execute(
            select(Recipe).where(
                Recipe.day_number == day,
                Recipe.meal_type == meal_type
            )
        )
        recipe = result.scalar_one_or_none()

        if not recipe:
            await query.answer("❌ Ошибка!")
            return

        result = await session.execute(
            select(Favorite).where(
                Favorite.user_id == user.id,
                Favorite.recipe_id == recipe.id
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            await query.answer("⭐ Уже в избранном!")
        else:
            fav = Favorite(user_id=user.id, recipe_id=recipe.id)
            session.add(fav)
            await session.commit()
            await query.answer("⭐ Добавлено в избранное!")

async def show_favorites(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать избранное"""
    query = update.callback_query
    await query.answer()

    user = await get_or_create_user(
        update.effective_user.id,
        update.effective_user.username,
        update.effective_user.first_name
    )

    async with async_session() as session:
        result = await session.execute(
            select(Favorite, Recipe).join(Recipe).where(Favorite.user_id == user.id)
        )
        favorites = result.all()

        if not favorites:
            text = "⭐ *Избранное пусто*\n\nДобавляй блюда через кнопку '⭐ В избранное'"
        else:
            text = "⭐ *ТВОЁ ИЗБРАННОЕ:*\n\n"
            for fav, recipe in favorites:
                text += f"• День {recipe.day_number} — {recipe.title.split(':')[0]}\n"

        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data='back_main')]]

        await query.edit_message_text(
            text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

# ═══════════════════════════════════════════════════════════════
# AI ПОМОЩНИК
# ═══════════════════════════════════════════════════════════════

AI_KNOWLEDGE = {
    "замена": """🔄 *ЗАМЕНА ПРОДУКТОВ*

*Мясо:*
• Курица ↔️ Индейка (1:1)
• Свинина ↔️ Говядина (+10 мин)
• Фарш — любой вид

*Крупы:*
• Рис ↔️ Булгур ↔️ Кус-кус
• Гречка ↔️ Киноа
• Макароны — любые

*Молочка:*
• Сметана ↔️ Йогурт греческий
• Молоко ↔️ Кефир/Ряженка
• Творог — любой % жирности

*Овощи:*
• Любые сезонные замены""",

    "время": """⏱ *ВРЕМЯ ПРИГОТОВЛЕНИЯ*

*Если нет аэрогриля:*
• Духовка: +20°C, время ×1.5
• Сковорода: средний огонь, с крышкой
• Мультиварка: режим "Выпечка"

*Проверка готовности:*
• Курица: 74°C внутри
• Свинина: 71°C
• Дать отдохнуть 5 минут""",

    "хранение": """❄️ *ХРАНЕНИЕ*

• Готовое мясо: 3 дня в холодильнике
• Супы: 2 дня
• Каши: 2 дня
• Заморозка: до 3 месяцев

💡 *Совет:* Готовь на 2 дня — экономь время!""",

    "бжу": """📊 *БЖУ НА ДЕНЬ (2500 ккал)*

• Белки: 150г (25%)
• Жиры: 85г (30%)
• Углеводы: 280г (45%)

*Увеличить белок:*
• Протеин (+30г)
• Орехи (+10г)
• Творог (+15г)"""
}

def get_ai_answer(question: str, recipe_context: str = "") -> str:
    """Локальный AI без API"""
    q = question.lower()

    if any(w in q for w in ['замен', 'вместо', 'нет', 'другой']):
        return AI_KNOWLEDGE["замена"]
    elif any(w in q for w in ['время', 'сколько', 'готовить', 'духовк']):
        return AI_KNOWLEDGE["время"]
    elif any(w in q for w in ['хран', 'холодильник', 'замороз']):
        return AI_KNOWLEDGE["хранение"]
    elif any(w in q for w in ['бжу', 'белок', 'калор', 'питани']):
        return AI_KNOWLEDGE["бжу"]
    else:
        if recipe_context:
            return f"""💡 *Совет по блюду:* {recipe_context}

• Можно приготовить заранее на 2 дня
• Хранить в закрытом контейнере
• Разогревать в аэрогриле 5 минут при 160°C

❓ Уточни вопрос:
• "Замена продуктов"
• "Время приготовления"
• "Хранение"
• "БЖУ/калории"""
        else:
            return """🤖 *Я готов помочь!*

Напиши вопрос про:
• Замену продуктов
• Время приготовления
• Хранение блюд
• БЖУ и калории

Или опиши свою ситуацию!"""

async def start_ai_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начать диалог с AI"""
    query = update.callback_query
    await query.answer()

    user = await get_or_create_user(
        update.effective_user.id,
        update.effective_user.username,
        update.effective_user.first_name
    )

    if not can_use_ai(user):
        await query.edit_message_text(
            "❌ *Лимит вопросов исчерпан*\n\n"
            "Free: 5 вопросов/день\n"
            "💎 Оформи подписку для безлимита!",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("💎 Оформить подписку", callback_data='subscription')
            ]])
        )
        return

    context.user_data['awaiting_ai'] = True
    recipe = context.user_data.get('current_recipe', '')

    header = f"про: {recipe}" if recipe else "общий вопрос"

    await query.edit_message_text(
        f"🤖 *Задай вопрос ({header})*\n\n"
        "Примеры:\n"
        "• Чем заменить курицу?\n"
        "• Сколько готовить в духовке?\n"
        "• Как хранить готовое?\n\n"
        "Напиши сообщением:",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Отмена", callback_data='back_main')
        ]])
    )

async def handle_ai_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка вопроса к AI"""
    if not context.user_data.get('awaiting_ai'):
        return

    user = await get_or_create_user(
        update.effective_user.id,
        update.effective_user.username,
        update.effective_user.first_name
    )

    question = update.message.text
    recipe = context.user_data.get('current_recipe', '')

    if user.subscription_type == 'free':
        user.ai_questions_today += 1
        async with async_session() as session:
            await session.merge(user)
            await session.commit()

    answer = get_ai_answer(question, recipe)

    if user.subscription_type == 'free':
        remaining = FREE_AI_QUESTIONS_PER_DAY - user.ai_questions_today
        answer += f"\n\n📊 Осталось вопросов сегодня: {remaining}"

    await update.message.reply_text(
        f"🤖 *Ответ:*\n\n{answer}",
        parse_mode='Markdown'
    )

    context.user_data['awaiting_ai'] = False

    keyboard = [[InlineKeyboardButton("📋 Меню", callback_data='back_main')]]
    await update.message.reply_text(
        "Ещё вопрос? Нажми 🤖 AI Помощник в меню!",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ═══════════════════════════════════════════════════════════════
# АЭРОГРИЛЬ (НОВОЕ)
# ═══════════════════════════════════════════════════════════════

async def show_aeroguide(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать справочник аэрогриля"""
    query = update.callback_query
    await query.answer()

    text = """🔥 *СПРАВОЧНИК АЭРОГРИЛЯ*

*🍗 КУРИЦА:*
• Бёдра — 190°C, 35-40 мин (кожей вверх!)
• Филе — 180°C, 25-30 мин (в фольге для сочности)
• Крылышки — 200°C, 30-35 мин (перевернуть на 15 мин)
• Голени — 190°C, 40 мин (перевернуть на 25 мин)

*🥩 СВИНИНА:*
• Стейки — 190°C, 25-30 мин (отдых 5 мин!)
• Рёбрышки — 180°C, 45-50 мин (фольга 30 мин)
• Котлеты — 190°C, 20-25 мин
• Тушение — 180°C, 50-60 мин

*🥩 ГОВЯДИНА:*
• Стейк medium — 160°C, 15-20 мин
• Ростбиф — 150°C, 40-50 мин

*🐟 РЫБА:*
• Филе белой рыбы — 160°C, 12-15 мин
• Лосось — 150°C, 10-12 мин
• Креветки — 180°C, 5-7 мин

*🥔 ГАРНИРЫ:*
• Картофель по-деревенски — 200°C, 25-30 мин
• Овощи гриль — 180°C, 20-25 мин
• Брокколи — 180°C, 8-10 мин
• Перец — 180°C, 10-12 мин
• Кабачки — 170°C, 12-15 мин

*💡 ЗОЛОТЫЕ СОВЕТЫ:*
• Разогревай аэрогриль 5 минут перед готовкой
• Не складывай продукты внахлёст — готовь одним слоем
• Переворачивай на половине времени
• Давай мясу отдохнуть 5 минут после готовки
• Маринуй минимум 20 минут для вкуса
• Смазывай решётку маслом
• Добавляй специи за 10 минут до готовности

*❌ ЧТО НЕЛЬЗЯ:*
• Слишком высокая температура — снаружи горит, внутри сыро
• Много еды сразу — готовится неравномерно
• Не прогрел аэрогриль — добавь 3-5 минут к времени"""

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Назад в меню", callback_data='back_main')]
    ])

    await query.edit_message_text(text, reply_markup=keyboard, parse_mode='Markdown')

# ═══════════════════════════════════════════════════════════════
# ПОМОЩЬ (НОВОЕ)
# ═══════════════════════════════════════════════════════════════

async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать помощь"""
    query = update.callback_query
    await query.answer()

    text = """👋 *ПРИВЕТ! Я ПОМОГУ РАЗОБРАТЬСЯ*

*🚀 КАК НАЧАТЬ ГОТОВИТЬ?*
Всё просто:
1️⃣ Жми *"📅 План на день"*
2️⃣ Выбирай день (1-30) — начни с 1-го!
3️⃣ Выбирай приём пищи: завтрак, обед, ужин
4️⃣ Следуй рецепту — там всё расписано пошагово

*💡 ЛАЙФХАКИ:*

*Как не потерять классный рецепт?*
Нажми ⭐ внизу рецепта — он сохранится в "Избранное". Больше не ищешь по 5 минут!

*AI — твой друг!*
Задавай вопросы: "Чем заменить курицу?" или "Сколько калорий в порции?"
Free: 5 вопросов/день | Basic: 15 | Pro: безлимит + генерация рецептов!

*Что делать, если нет продукта?*
Спроси AI — он подскажет замену. Или загляни в "Аэрогриль" — там таблица замен!

*🎁 БЕСПЛАТНО vs ПЛАТНО:*

*Бесплатно:*
• Дни 1-7 программы
• 5 AI-вопросов в день
• Без сохранения в избранное

*Basic (299₽):*
• Все 30 дней питания
• 15 AI-вопросов/день
• Расчёт калорий под тебя
• Закупки на день

*Pro (599₽):*
• Всё + безлимитный AI
• Готовь для всей семьи (5 чел)
• PDF-списки закупок
• Личный диетолог-куратор
• Рецепты раньше всех!

*❓ ЧАСТЫЕ ВОПРОСЫ:*

*Q: Можно ли заморозить блюдо?*
A: Конечно! Укажу в рецепте, что замораживается. Обычно до 3 месяцев.

*Q: Не ем мясо, есть ли альтернативы?*
A: В Pro-версии AI подберёт вегетарианские замены. Или пиши в поддержку!

*Q: Как рассчитать порции на 3 человека?*
A: В Pro есть "Семейный режим" — автоматически умножает ингредиенты!

*🆘 НЕ РАБОТАЕТ / ЕСТЬ ВОПРОС?*
Пиши сюда: @your_support_username
Отвечаем быстро, помогаем всем! 💪"""

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔥 Справочник аэрогриля", callback_data='aeroguide')],
        [InlineKeyboardButton("💎 Оформить подписку", callback_data='subscription')],
        [InlineKeyboardButton("🔙 Назад в меню", callback_data='back_main')]
    ])

    await query.edit_message_text(text, reply_markup=keyboard, parse_mode='Markdown')

# ═══════════════════════════════════════════════════════════════
# ЗАКУПКИ НА ДЕНЬ (НОВОЕ)
# ═══════════════════════════════════════════════════════════════

async def show_shopday(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать закупки на день"""
    query = update.callback_query
    await query.answer()

    day = int(query.data.split('_')[1])

    # Проверяем подписку
    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == update.effective_user.id)
        )
        user = result.scalar_one_or_none()

        if day > 7 and (not user or user.subscription_type == 'free'):
            if not user or not (user.subscription_expires and user.subscription_expires > datetime.utcnow()):
                await query.edit_message_text(
                    "🔒 Доступно по подписке!\n\nОформите Basic или Pro для доступа к дням 8-30.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("💎 Подписка", callback_data='subscription')],
                        [InlineKeyboardButton("🔙 Назад", callback_data='menu_day')]
                    ])
                )
                return

    # Получаем рецепты дня
    async with async_session() as session:
        result = await session.execute(
            select(Recipe).where(Recipe.day_number == day)
        )
        recipes = result.scalars().all()

    if not recipes:
        await query.edit_message_text(
            "Рецепты не найдены.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Назад", callback_data=f'day_{day}')
            ]])
        )
        return

    # Собираем продукты
    all_products = []
    for recipe in recipes:
        if recipe.shopping:
            products = [p.strip() for p in recipe.shopping.split('•') if p.strip()]
            all_products.extend(products)

    if not all_products:
        await query.edit_message_text(
            "Список закупок пуст.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Назад", callback_data=f'day_{day}')
            ]])
        )
        return

    # Формируем текст
    text = f"🛒 *ЗАКУПКИ НА ДЕНЬ {day}*\n\n"
    for i, product in enumerate(all_products, 1):
        text += f"{i}. {product}\n"

    text += f"\n_Всего позиций: {len(all_products)}_"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 К дню", callback_data=f'day_{day}')],
        [InlineKeyboardButton("🏠 В главное меню", callback_data='back_main')]
    ])

    await query.edit_message_text(text, reply_markup=keyboard, parse_mode='Markdown')

# ═══════════════════════════════════════════════════════════════
# ИТОГО ДЕНЬ (НОВОЕ)
# ═══════════════════════════════════════════════════════════════

async def show_total_day(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать итоги дня (калории, БЖУ)"""
    query = update.callback_query
    await query.answer()

    day = int(query.data.split('_')[1])

    # Проверяем подписку
    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == update.effective_user.id)
        )
        user = result.scalar_one_or_none()

        if day > 7 and (not user or user.subscription_type == 'free'):
            if not user or not (user.subscription_expires and user.subscription_expires > datetime.utcnow()):
                await query.edit_message_text(
                    "🔒 Доступно по подписке!",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("💎 Подписка", callback_data='subscription')],
                        [InlineKeyboardButton("🔙 Назад", callback_data='menu_day')]
                    ])
                )
                return

    # Получаем рецепты дня
    async with async_session() as session:
        result = await session.execute(
            select(Recipe).where(Recipe.day_number == day)
        )
        recipes = result.scalars().all()

    # Суммируем
    total_calories = 0
    total_proteins = 0
    total_fats = 0
    total_carbs = 0

    meal_stats = []
    for recipe in recipes:
        cal = recipe.calories_value or 0
        total_calories += cal

        if recipe.proteins:
            total_proteins += recipe.proteins
        if recipe.fats:
            total_fats += recipe.fats
        if recipe.carbs:
            total_carbs += recipe.carbs

        emoji = {'breakfast': '🌅', 'lunch': '🍽', 'snack': '☕', 'dinner': '🌙'}.get(recipe.meal_type, '🍽')
        meal_stats.append(f"{emoji} {cal} ккал")

    text = f"📊 *ИТОГО ДЕНЬ {day}*\n\n"
    text += "*По приёмам пищи:*\n"
    for stat in meal_stats:
        text += f"  {stat}\n"

    text += f"\n*🔥 Всего за день:*\n"
    text += f"  Калории: {total_calories} ккал\n"

    if total_proteins > 0:
        text += f"  Белки: {total_proteins:.1f}г\n"
        text += f"  Жиры: {total_fats:.1f}г\n"
        text += f"  Углеводы: {total_carbs:.1f}г\n"

    # Сравнение с нормой
    if user and user.daily_calories:
        diff = user.daily_calories - total_calories
        if abs(diff) < 100:
            text += f"\n✅ *Идеально!* Соответствует твоей норме ({user.daily_calories} ккал)"
        elif diff > 0:
            text += f"\n⚡ *Ниже нормы* на {diff} ккал\nМожно добавить перекус!"
        else:
            text += f"\n⚠️ *Выше нормы* на {abs(diff)} ккал\nУчти при планировании!"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 К дню", callback_data=f'day_{day}')],
        [InlineKeyboardButton("🏠 В главное меню", callback_data='back_main')]
    ])

    await query.edit_message_text(text, reply_markup=keyboard, parse_mode='Markdown')

# ═══════════════════════════════════════════════════════════════
# ПОДПИСКА (ОБНОВЛЕННОЕ)
# ═══════════════════════════════════════════════════════════════

async def show_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать планы подписок"""
    query = update.callback_query
    await query.answer()

    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == update.effective_user.id)
        )
        user = result.scalar_one_or_none()
        current_sub = user.subscription_type if user else "free"

    text = f"""💎 *ПОДПИСКА MAYBECOOK*

*Твой статус:* {current_sub.upper()}

━━━━━━━━━━━━━━━━━━━━━

*🆓 FREE — 0₽*
Попробуй бесплатно:
• Дни 1-7 программы
• 5 AI-вопросов/день
• Просмотр без сохранения

❌ Нет дней 8-30
❌ Нет избранного
❌ Нет персонализации

━━━━━━━━━━━━━━━━━━━━━

*💎 BASIC — 299₽/мес*
Всё для комфортного питания:

🔥 Полный доступ ко всем 30 дням
🔥 15 AI-вопросов каждый день
🔥 Персональный расчёт калорий и БЖУ
🔥 Списки закупок на 1 день
🔥 До 20 блюд в избранном

✨ Экономия времени — не думай, что готовить!

━━━━━━━━━━━━━━━━━━━━━

*👑 PRO — 599₽/мес*
Максимум результата для семьи:

Всё из Basic, плюс:

👑 Безлимитный AI + генерация рецептов
👑 Семейный режим (до 5 профилей)
👑 Списки закупок на неделю + PDF экспорт
👑 Личный диетолог-куратор
👑 Ранний доступ к новым рецептам
👑 Челленджи и призы

✨ Экономия 10+ часов в неделю!

━━━━━━━━━━━━━━━━━━━━━

_Оплата временно недоступна.
По вопросам подписки пишите: @your_support_username_"""

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Назад в меню", callback_data='back_main')]
    ])

    await query.edit_message_text(text, reply_markup=keyboard, parse_mode='Markdown')

# ═══════════════════════════════════════════════════════════════
# ОБРАБОТЧИК КНОПОК
# ═══════════════════════════════════════════════════════════════

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Главный обработчик всех кнопок"""
    query = update.callback_query
    data = query.data

    # Главное меню
    if data == 'back_main':
        await show_main_menu(update, context, edit=True)
        return

    # Меню дней
    if data == 'menu_day':
        await show_days_menu(update, context)
        return

    if data.startswith('day_'):
        await show_day(update, context)
        return

    if data.startswith('locked_'):
        await query.answer("🔒 Доступно по подписке!", show_alert=True)
        return

    # Блюда
    if data.startswith('meal_'):
        await show_meal(update, context)
        return

    # Избранное
    if data.startswith('fav_'):
        await add_to_favorites(update, context)
        return

    if data == 'favorites':
        await show_favorites(update, context)
        return

    # AI
    if data in ['ask_ai', 'ask_ai_recipe']:
        await start_ai_chat(update, context)
        return

    # Аэрогриль (новое)
    if data == 'aeroguide':
        await show_aeroguide(update, context)
        return

    # Помощь (новое)
    if data == 'help':
        await show_help(update, context)
        return

    # Закупки на день (новое)
    if data.startswith('shopday_'):
        await show_shopday(update, context)
        return

    # Итого день (новое)
    if data.startswith('total_'):
        await show_total_day(update, context)
        return

    # Подписка (обновленное)
    if data == 'subscription':
        await show_subscription(update, context)
        return

    # Заглушки только для покупки
    if data in ['shopping', 'buy_basic', 'buy_pro']:
        await query.answer()
        await query.edit_message_text(
            "🚧 *В разработке*\n\nЭта функция скоро будет доступна!",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data='back_main')]])
        )
        return

# ═══════════════════════════════════════════════════════════════
# ЗАПУСК
# ═══════════════════════════════════════════════════════════════

async def init_app():
    """Инициализация базы данных"""
    await init_db()
    try:
        await load_recipes()
    except Exception as e:
        logger.warning(f"Не удалось загрузить рецепты: {e}")

def main():
    global application

    application = Application.builder().token(TELEGRAM_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_ai_message))

    PORT = int(os.environ.get('PORT', '10000'))
    RENDER_EXTERNAL_HOSTNAME = os.environ.get('RENDER_EXTERNAL_HOSTNAME')
    WEBHOOK_URL = os.environ.get('WEBHOOK_URL', '').rstrip('/')

    if RENDER_EXTERNAL_HOSTNAME and WEBHOOK_URL:
        logger.info("🚀 Запуск в режиме WEBHOOK")

        async def init_and_start():
            await init_app()
            await application.initialize()
            await application.start()
            await application.bot.set_webhook(
                url=f"{WEBHOOK_URL}/webhook",
                allowed_updates=Update.ALL_TYPES
            )
            logger.info(f"✅ Webhook установлен: {WEBHOOK_URL}/webhook")

        from aiohttp import web

        async def webhook_handler(request):
            data = await request.json()
            update = Update.de_json(data, application.bot)
            await application.process_update(update)
            return web.Response(text="OK")

        async def health_handler(request):
            return web.Response(text="MaybeCook Bot is running!")

        app = web.Application()
        app.router.add_get('/', health_handler)
        app.router.add_post('/webhook', webhook_handler)

        async def run():
            await init_and_start()
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, '0.0.0.0', PORT)
            await site.start()
            logger.info(f"✅ Сервер запущен на порту {PORT}")
            while True:
                await asyncio.sleep(3600)

        asyncio.run(run())

    else:
        logger.info("🔄 Запуск в режиме POLLING (локально)")

        async def run_polling():
            await init_app()
            await application.initialize()
            await application.start()
            await application.updater.start_polling(drop_pending_updates=True)
            await asyncio.Event().wait()

        asyncio.run(run_polling())

if __name__ == '__main__':
    main()
