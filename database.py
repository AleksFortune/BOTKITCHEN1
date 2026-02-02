from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base, sessionmaker
import os

# Получаем URL из переменных окружения
DATABASE_URL = os.getenv("DATABASE_URL")

# Преобразуем для asyncpg
if DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://")
else:
    # Fallback на SQLite (не должно случиться на Render)
    DATABASE_URL = "sqlite+aiosqlite:///mealbot.db"

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()

async def init_db():
    """Создание всех таблиц"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

async def get_session():
    """Получение сессии для работы с БД"""
    async with async_session() as session:
        yield session
