"""
Database queries for admin panel
"""
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
from sqlalchemy import select, func, desc, asc, update, delete, and_
from sqlalchemy.orm import selectinload

# Импортируем из основного проекта
import sys
sys.path.append('..')
from database import async_session
from models import User, Recipe, Favorite, CookingHistory, MealPlan


class AdminDatabase:
    """Класс для админских запросов к БД"""

    # ==================== USERS ====================

    @staticmethod
    async def get_users_stats() -> Dict[str, Any]:
        """Статистика по пользователям"""
        async with async_session() as session:
            # Всего пользователей
            total = await session.scalar(select(func.count(User.id)))

            # Новые за 24 часа
            last_24h = await session.scalar(
                select(func.count(User.id))
                .where(User.created_at >= datetime.utcnow() - timedelta(hours=24))
            )

            # Новые за 7 дней
            last_7d = await session.scalar(
                select(func.count(User.id))
                .where(User.created_at >= datetime.utcnow() - timedelta(days=7))
            )

            # Активные сегодня
            active_today = await session.scalar(
                select(func.count(User.id))
                .where(User.last_active >= datetime.utcnow() - timedelta(hours=24))
            )

            # По типам подписки
            sub_stats = {}
            for sub_type in ["free", "basic", "pro"]:
                count = await session.scalar(
                    select(func.count(User.id))
                    .where(User.subscription_type == sub_type)
                )
                sub_stats[sub_type] = count

            # С подпиской (активной)
            now = datetime.utcnow()
            with_active_sub = await session.scalar(
                select(func.count(User.id))
                .where(and_(
                    User.subscription_expires.isnot(None),
                    User.subscription_expires > now
                ))
            )

            return {
                "total": total,
                "last_24h": last_24h,
                "last_7d": last_7d,
                "active_today": active_today,
                "subscription_types": sub_stats,
                "with_active_subscription": with_active_sub,
                "conversion_rate": round(with_active_sub / total * 100, 2) if total > 0 else 0
            }

    @staticmethod
    async def get_users_list(
        skip: int = 0,
        limit: int = 50,
        search: Optional[str] = None,
        subscription_type: Optional[str] = None,
        sort_by: str = "created_at",
        sort_order: str = "desc"
    ) -> List[Dict[str, Any]]:
        """Список пользователей с фильтрацией"""
        async with async_session() as session:
            query = select(User)

            # Фильтр по поиску
            if search:
                query = query.where(
                    or_(
                        User.username.ilike(f"%{search}%"),
                        User.first_name.ilike(f"%{search}%"),
                        User.telegram_id.cast(String).ilike(f"%{search}%")
                    )
                )

            # Фильтр по подписке
            if subscription_type:
                query = query.where(User.subscription_type == subscription_type)

            # Сортировка
            sort_column = getattr(User, sort_by, User.created_at)
            if sort_order == "desc":
                query = query.order_by(desc(sort_column))
            else:
                query = query.order_by(asc(sort_column))

            query = query.offset(skip).limit(limit)

            result = await session.execute(query)
            users = result.scalars().all()

            return [AdminDatabase._user_to_dict(u) for u in users]

    @staticmethod
    def _user_to_dict(user: User) -> Dict[str, Any]:
        """Конвертация User в dict"""
        now = datetime.utcnow()
        sub_active = user.subscription_expires and user.subscription_expires > now

        return {
            "id": user.id,
            "telegram_id": user.telegram_id,
            "username": user.username,
            "first_name": user.first_name,
            "created_at": user.created_at,
            "last_active": user.last_active,
            "subscription_type": user.subscription_type,
            "subscription_expires": user.subscription_expires,
            "subscription_active": sub_active,
            "days_left": (user.subscription_expires - now).days if sub_active else 0,
            "ai_questions_today": user.ai_questions_today,
            "daily_calories": user.daily_calories,
            "family_size": user.family_size
        }

    @staticmethod
    async def get_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
        """Получить пользователя по ID"""
        async with async_session() as session:
            result = await session.execute(
                select(User).where(User.id == user_id)
            )
            user = result.scalar_one_or_none()
            return AdminDatabase._user_to_dict(user) if user else None

    @staticmethod
    async def update_user_subscription(
        user_id: int,
        subscription_type: str,
        days: int
    ) -> bool:
        """Обновить подписку пользователя"""
        async with async_session() as session:
            expires = datetime.utcnow() + timedelta(days=days)

            await session.execute(
                update(User)
                .where(User.id == user_id)
                .values(
                    subscription_type=subscription_type,
                    subscription_expires=expires
                )
            )
            await session.commit()
            return True

    @staticmethod
    async def delete_user(user_id: int) -> bool:
        """Удалить пользователя"""
        async with async_session() as session:
            await session.execute(
                delete(User).where(User.id == user_id)
            )
            await session.commit()
            return True

    # ==================== RECIPES ====================

    @staticmethod
    async def get_recipes_stats() -> Dict[str, Any]:
        """Статистика по рецептам"""
        async with async_session() as session:
            total = await session.scalar(select(func.count(Recipe.id)))

            # По типам приёма пищи
            meal_stats = {}
            for meal in ["breakfast", "lunch", "snack", "dinner"]:
                count = await session.scalar(
                    select(func.count(Recipe.id))
                    .where(Recipe.meal_type == meal)
                )
                meal_stats[meal] = count

            # Премиум vs бесплатные
            premium_count = await session.scalar(
                select(func.count(Recipe.id)).where(Recipe.is_premium == True)
            )

            return {
                "total": total,
                "by_meal_type": meal_stats,
                "premium": premium_count,
                "free": total - premium_count
            }

    @staticmethod
    async def get_recipes_list(
        skip: int = 0,
        limit: int = 50,
        day_number: Optional[int] = None,
        meal_type: Optional[str] = None,
        is_premium: Optional[bool] = None,
        search: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Список рецептов с фильтрацией"""
        async with async_session() as session:
            query = select(Recipe)

            if day_number:
                query = query.where(Recipe.day_number == day_number)
            if meal_type:
                query = query.where(Recipe.meal_type == meal_type)
            if is_premium is not None:
                query = query.where(Recipe.is_premium == is_premium)
            if search:
                query = query.where(Recipe.title.ilike(f"%{search}%"))

            query = query.order_by(Recipe.day_number, Recipe.meal_type)
            query = query.offset(skip).limit(limit)

            result = await session.execute(query)
            recipes = result.scalars().all()

            return [AdminDatabase._recipe_to_dict(r) for r in recipes]

    @staticmethod
    def _recipe_to_dict(recipe: Recipe) -> Dict[str, Any]:
        """Конвертация Recipe в dict"""
        return {
            "id": recipe.id,
            "day_number": recipe.day_number,
            "meal_type": recipe.meal_type,
            "title": recipe.title,
            "calories_value": recipe.calories_value,
            "calories_text": recipe.calories_text,
            "is_premium": recipe.is_premium,
            "tags": recipe.tags or [],
            "cooking_time": recipe.cooking_time,
            "shopping_preview": recipe.shopping[:100] + "..." if recipe.shopping else ""
        }

    @staticmethod
    async def get_recipe_by_id(recipe_id: int) -> Optional[Dict[str, Any]]:
        """Получить рецепт по ID с полными данными"""
        async with async_session() as session:
            result = await session.execute(
                select(Recipe).where(Recipe.id == recipe_id)
            )
            recipe = result.scalar_one_or_none()
            if not recipe:
                return None

            return {
                "id": recipe.id,
                "day_number": recipe.day_number,
                "meal_type": recipe.meal_type,
                "title": recipe.title,
                "shopping": recipe.shopping,
                "portion": recipe.portion,
                "recipe": recipe.recipe,
                "calories_text": recipe.calories_text,
                "calories_value": recipe.calories_value,
                "proteins": recipe.proteins,
                "fats": recipe.fats,
                "carbs": recipe.carbs,
                "cooking_time": recipe.cooking_time,
                "is_premium": recipe.is_premium,
                "tags": recipe.tags or []
            }

    @staticmethod
    async def create_or_update_recipe(recipe_data: Dict[str, Any]) -> int:
        """Создать или обновить рецепт"""
        async with async_session() as session:
            recipe_id = recipe_data.get("id")

            if recipe_id:
                # Update
                await session.execute(
                    update(Recipe)
                    .where(Recipe.id == recipe_id)
                    .values(**recipe_data)
                )
                await session.commit()
                return recipe_id
            else:
                # Create
                recipe = Recipe(**recipe_data)
                session.add(recipe)
                await session.commit()
                return recipe.id

    @staticmethod
    async def delete_recipe(recipe_id: int) -> bool:
        """Удалить рецепт"""
        async with async_session() as session:
            await session.execute(
                delete(Recipe).where(Recipe.id == recipe_id)
            )
            await session.commit()
            return True

    # ==================== ANALYTICS ====================

    @staticmethod
    async def get_engagement_stats() -> Dict[str, Any]:
        """Статистика вовлечённости"""
        async with async_session() as session:
            # Всего избранного
            favorites_count = await session.scalar(select(func.count(Favorite.id)))

            # Всего приготовлено (история)
            cooked_count = await session.scalar(select(func.count(CookingHistory.id)))

            # Средний рейтинг
            avg_rating = await session.scalar(
                select(func.avg(CookingHistory.rating))
                .where(CookingHistory.rating.isnot(None))
            )

            # Топ популярных рецептов (по избранному)
            top_favorites = await session.execute(
                select(Recipe.title, func.count(Favorite.id).label("count"))
                .join(Favorite, Recipe.id == Favorite.recipe_id)
                .group_by(Recipe.id)
                .order_by(desc("count"))
                .limit(10)
            )

            return {
                "total_favorites": favorites_count,
                "total_cooked": cooked_count,
                "average_rating": round(avg_rating, 2) if avg_rating else 0,
                "top_favorites": [(title, count) for title, count in top_favorites.all()]
            }

    @staticmethod
    async def get_retention_stats() -> Dict[str, Any]:
        """Статистика удержания (cohort analysis)"""
        async with async_session() as session:
            # Пользователи по дням регистрации (последние 14 дней)
            cohorts = []
            for i in range(14):
                date = datetime.utcnow().date() - timedelta(days=i)
                next_date = date + timedelta(days=1)

                # Зарегистрировались в этот день
                registered = await session.scalar(
                    select(func.count(User.id))
                    .where(
                        User.created_at >= datetime.combine(date, datetime.min.time()),
                        User.created_at < datetime.combine(next_date, datetime.min.time())
                    )
                )

                # Из них активны сегодня
                active = await session.scalar(
                    select(func.count(User.id))
                    .where(
                        User.created_at >= datetime.combine(date, datetime.min.time()),
                        User.created_at < datetime.combine(next_date, datetime.min.time()),
                        User.last_active >= datetime.utcnow() - timedelta(hours=24)
                    )
                )

                retention = round(active / registered * 100, 1) if registered > 0 else 0

                cohorts.append({
                    "date": date.strftime("%Y-%m-%d"),
                    "registered": registered,
                    "active_today": active,
                    "retention": retention
                })

            return {"cohorts": cohorts}


# Добавляем импорт для поиска
from sqlalchemy import or_
from sqlalchemy import String
