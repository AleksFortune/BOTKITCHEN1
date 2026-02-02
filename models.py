from sqlalchemy import Column, Integer, String, Text, Float, Boolean, DateTime, ForeignKey, JSON, BigInteger
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base

class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True)
    telegram_id = Column(BigInteger, unique=True, index=True)
    username = Column(String(100))
    first_name = Column(String(100))
    last_name = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)
    last_active = Column(DateTime, default=datetime.utcnow)
    
    # Подписка
    subscription_type = Column(String(20), default="free")  # free, basic, pro
    subscription_expires = Column(DateTime)
    trial_used = Column(Boolean, default=False)
    
    # Лимиты (для free)
    ai_questions_today = Column(Integer, default=0)
    ai_questions_reset = Column(DateTime, default=datetime.utcnow)
    
    # Профиль
    goal = Column(String(50))  # mass, loss, maintain
    daily_calories = Column(Integer, default=2500)
    family_size = Column(Integer, default=2)
    
    # Связи
    favorites = relationship("Favorite", back_populates="user")
    meal_plans = relationship("MealPlan", back_populates="user")
    history = relationship("CookingHistory", back_populates="user")

class Recipe(Base):
    __tablename__ = "recipes"
    
    id = Column(Integer, primary_key=True)
    
    # Твой формат полностью сохранён!
    day_number = Column(Integer, index=True)  # 1-30 (или больше)
    meal_type = Column(String(20))  # breakfast, lunch, snack, dinner
    
    # Полные тексты как в твоём файле
    title = Column(Text)  # "🌅 ЗАВТРАК ДЕНЬ 1: Овсянка с бананом..."
    shopping = Column(Text)  # "📦 НА ЗАКУПКУ (на 2 человека):..."
    portion = Column(Text)  # "🍽 НА ПОРЦИЮ (1 человек):..."
    recipe = Column(Text)  # "📝 ПРИГОТОВЛЕНИЕ:..."
    calories_text = Column(Text)  # "🔥 КАЛОРИЙНОСТЬ: 550 ккал..."
    
    # Дополнительно для умного поиска
    calories_value = Column(Integer)  # 550 (число для фильтров)
    proteins = Column(Float)
    fats = Column(Float)
    carbs = Column(Float)
    cooking_time = Column(Integer)  # минуты
    is_premium = Column(Boolean, default=False)
    tags = Column(JSON, default=list)  # ["быстро", "куриное", "завтрак"]
    
    # Связи
    favorites = relationship("Favorite", back_populates="recipe")
    history = relationship("CookingHistory", back_populates="recipe")

class Favorite(Base):
    __tablename__ = "favorites"
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    recipe_id = Column(Integer, ForeignKey("recipes.id"))
    added_at = Column(DateTime, default=datetime.utcnow)
    
    user = relationship("User", back_populates="favorites")
    recipe = relationship("Recipe", back_populates="favorites")

class MealPlan(Base):
    __tablename__ = "meal_plans"
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    date = Column(DateTime)
    day_number = Column(Integer)  # 1-30
    
    # Что запланировано
    breakfast_id = Column(Integer, ForeignKey("recipes.id"))
    lunch_id = Column(Integer, ForeignKey("recipes.id"))
    snack_id = Column(Integer, ForeignKey("recipes.id"))
    dinner_id = Column(Integer, ForeignKey("recipes.id"))
    
    created_at = Column(DateTime, default=datetime.utcnow)
    
    user = relationship("User", back_populates="meal_plans")

class CookingHistory(Base):
    __tablename__ = "cooking_history"
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    recipe_id = Column(Integer, ForeignKey("recipes.id"))
    cooked_at = Column(DateTime, default=datetime.utcnow)
    rating = Column(Integer)  # 1-5
    photo_url = Column(String(500))
    notes = Column(Text)
    
    user = relationship("User", back_populates="history")
    recipe = relationship("Recipe", back_populates="history")