import logging
import re
import os
import asyncio  # ← ДОБАВИЛИ ЭТО
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    MessageHandler, filters, ContextTypes, ConversationHandler
)
from sqlalchemy import select

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

🍽 Это твой персональный план питания на 30 дней!

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
# ПОДПИСКА
# ═══════════════════════════════════════════════════════════════

async def show_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать планы подписок"""
    query = update.callback_query
    await query.answer()
    
    user = await get_or_create_user(
        update.effective_user.id,
        update.effective_user.username,
        update.effective_user.first_name
    )
    
    sub = check_subscription(user)
    
    text = f"""💎 *ПОДПИСКИ*

Твой статус: {"✅ Активна" if sub['active'] else "❌ Неактивна"}
Тип: {sub['type'].upper()}
Осталось дней: {sub['days_left']}

*Доступно:*

📱 *Free* (0₽)
• {FREE_DAYS_VISIBLE} дней меню
• {FREE_AI_QUESTIONS_PER_DAY} вопросов AI/день
• Реклама

💎 *Basic* (299₽/мес)
• Все 30 дней меню
• Безлимит AI
• Списки закупок
• Без рекламы

👑 *Pro* (599₽/мес)
• Всё из Basic
• Персональные планы
• Приоритетная поддержка
• PDF-экспорт
"""
    
    keyboard = [
        [InlineKeyboardButton("💎 Basic - 299₽", callback_data='buy_basic'),
         InlineKeyboardButton("👑 Pro - 599₽", callback_data='buy_pro')],
        [InlineKeyboardButton("🔙 Назад", callback_data='back_main')]
    ]
    
    await query.edit_message_text(
        text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

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
    
    # Подписка
    if data == 'subscription':
        await show_subscription(update, context)
        return
    
    # Заглушки
    if data in ['aeroguide', 'shopping', 'help', 'shopday_', 'total_', 'buy_basic', 'buy_pro']:
        await query.answer()
        await query.edit_message_text(
            "🚧 *В разработке*\n\nЭта функция скоро будет доступна!",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Назад", callback_data='back_main')
            ]])
        )
        return

# ═══════════════════════════════════════════════════════════════
# ЗАПУСК С WEBHOOK (для Render)
# ═══════════════════════════════════════════════════════════════

async def init_app():
    """Инициализация базы данных"""
    await init_db()
    try:
        await load_recipes()
    except Exception as e:
        logger.warning(f"Не удалось загрузить рецепты: {e}")

def main():
    # Создаем приложение бота
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, 
        handle_ai_message
    ))
    
    # Получаем переменные от Render
    PORT = int(os.environ.get('PORT', '10000'))
    RENDER_EXTERNAL_HOSTNAME = os.environ.get('RENDER_EXTERNAL_HOSTNAME')
    
    if RENDER_EXTERNAL_HOSTNAME:
        # POLLING + фейковый сервер для Render
        from aiohttp import web
        
        async def fake_server():
            app = web.Application()
            app.router.add_get('/', lambda r: web.Response(text="Bot is running!"))
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, '0.0.0.0', PORT)
            await site.start()
            logger.info(f"✅ Keep-alive server on port {PORT}")
            while True:
                await asyncio.sleep(3600)
        
        async def run_bot():
            # Инициализация базы данных
            await init_app()
            # Запускаем фейковый сервер как задачу
            asyncio.create_task(fake_server())
            # Запускаем бота
            logger.info("🔄 Запуск Polling для Render")
            await application.initialize()
            await application.start()
            await application.updater.start_polling()
            # Держим процесс живым
            while True:
                await asyncio.sleep(3600)
        
        # Запускаем всё в одном event loop
        asyncio.run(run_bot())
    else:
        # Локально — просто polling
        asyncio.run(init_app())
        logger.info("🔄 Запуск Polling (локально)")
        application.run_polling()

if __name__ == '__main__':
    main()

