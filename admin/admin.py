"""
Admin Panel for MealBot
FastAPI + Jinja2 + Tailwind CSS
"""
import os
import sys
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Request, Form, Depends, Query, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from contextlib import asynccontextmanager

# Добавляем путь к основному проекту
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import init_db
from auth import (
    authenticate_admin, create_session_token, get_current_admin,
    SESSION_COOKIE_NAME, MAX_AGE
)
from database_admin import AdminDatabase


# Lifespan для инициализации
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events"""
    # Startup
    await init_db()
    print("✅ Admin panel database initialized")
    yield
    # Shutdown
    print("👋 Admin panel shutting down")


app = FastAPI(
    title="MealBot Admin Panel",
    description="Admin panel for managing MealBot users and recipes",
    version="1.0.0",
    lifespan=lifespan
)

# Static files и templates
app.mount("/admin/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# ==================== AUTH ROUTES ====================

@app.get("/admin/login", response_class=HTMLResponse)
async def login_page(request: Request, error: Optional[str] = None):
    """Страница входа"""
    return templates.TemplateResponse("login.html", {
        "request": request,
        "error": error
    })


@app.post("/admin/login")
async def login_post(
    request: Request,
    username: str = Form(...),
    password: str = Form(...)
):
    """Обработка входа"""
    if authenticate_admin(username, password):
        response = RedirectResponse(url="/admin", status_code=302)
        token = create_session_token(username)
        response.set_cookie(
            SESSION_COOKIE_NAME,
            token,
            max_age=MAX_AGE,
            httponly=True,
            secure=False,  # True в production с HTTPS
            samesite="lax"
        )
        return response
    else:
        return RedirectResponse(
            url="/admin/login?error=invalid_credentials",
            status_code=302
        )


@app.get("/admin/logout")
async def logout():
    """Выход"""
    response = RedirectResponse(url="/admin/login", status_code=302)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response


# ==================== DASHBOARD ====================

@app.get("/admin", response_class=HTMLResponse)
async def dashboard(request: Request, admin: str = Depends(get_current_admin)):
    """Главная страница админки"""
    # Собираем статистику
    user_stats = await AdminDatabase.get_users_stats()
    recipe_stats = await AdminDatabase.get_recipes_stats()
    engagement = await AdminDatabase.get_engagement_stats()
    retention = await AdminDatabase.get_retention_stats()

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "admin": admin,
        "user_stats": user_stats,
        "recipe_stats": recipe_stats,
        "engagement": engagement,
        "retention": retention,
        "now": datetime.utcnow()
    })


# ==================== USERS ====================

@app.get("/admin/users", response_class=HTMLResponse)
async def users_list(
    request: Request,
    page: int = Query(1, ge=1),
    search: Optional[str] = Query(None),
    subscription: Optional[str] = Query(None),
    admin: str = Depends(get_current_admin)
):
    """Список пользователей"""
    per_page = 50
    skip = (page - 1) * per_page

    users = await AdminDatabase.get_users_list(
        skip=skip,
        limit=per_page,
        search=search,
        subscription_type=subscription
    )

    # Для пагинации нужно общее количество (упрощённо)
    stats = await AdminDatabase.get_users_stats()
    total_pages = (stats["total"] + per_page - 1) // per_page

    return templates.TemplateResponse("users.html", {
        "request": request,
        "admin": admin,
        "users": users,
        "page": page,
        "total_pages": total_pages,
        "search": search,
        "subscription_filter": subscription,
        "stats": stats
    })


@app.get("/admin/users/{user_id}", response_class=HTMLResponse)
async def user_detail(
    request: Request,
    user_id: int,
    admin: str = Depends(get_current_admin)
):
    """Детальная страница пользователя"""
    user = await AdminDatabase.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return templates.TemplateResponse("user_detail.html", {
        "request": request,
        "admin": admin,
        "user": user
    })


@app.post("/admin/users/{user_id}/update-subscription")
async def update_subscription(
    request: Request,
    user_id: int,
    subscription_type: str = Form(...),
    days: int = Form(...),
    admin: str = Depends(get_current_admin)
):
    """Обновление подписки пользователя"""
    success = await AdminDatabase.update_user_subscription(
        user_id, subscription_type, days
    )

    if success:
        return RedirectResponse(
            url=f"/admin/users/{user_id}?success=subscription_updated",
            status_code=302
        )
    else:
        raise HTTPException(status_code=400, detail="Failed to update subscription")


@app.post("/admin/users/{user_id}/delete")
async def delete_user(
    request: Request,
    user_id: int,
    admin: str = Depends(get_current_admin)
):
    """Удаление пользователя"""
    success = await AdminDatabase.delete_user(user_id)
    if success:
        return RedirectResponse(url="/admin/users?success=user_deleted", status_code=302)
    else:
        raise HTTPException(status_code=400, detail="Failed to delete user")


# ==================== RECIPES ====================

@app.get("/admin/recipes", response_class=HTMLResponse)
async def recipes_list(
    request: Request,
    page: int = Query(1, ge=1),
    day: Optional[int] = Query(None),
    meal_type: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    admin: str = Depends(get_current_admin)
):
    """Список рецептов"""
    per_page = 50
    skip = (page - 1) * per_page

    recipes = await AdminDatabase.get_recipes_list(
        skip=skip,
        limit=per_page,
        day_number=day,
        meal_type=meal_type,
        search=search
    )

    stats = await AdminDatabase.get_recipes_stats()

    return templates.TemplateResponse("recipes.html", {
        "request": request,
        "admin": admin,
        "recipes": recipes,
        "page": page,
        "day_filter": day,
        "meal_type_filter": meal_type,
        "search": search,
        "stats": stats,
        "meal_types": ["breakfast", "lunch", "snack", "dinner"]
    })


@app.get("/admin/recipes/{recipe_id}", response_class=HTMLResponse)
async def recipe_detail(
    request: Request,
    recipe_id: int,
    admin: str = Depends(get_current_admin)
):
    """Детальная страница рецепта"""
    recipe = await AdminDatabase.get_recipe_by_id(recipe_id)
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")

    return templates.TemplateResponse("recipe_detail.html", {
        "request": request,
        "admin": admin,
        "recipe": recipe,
        "meal_types": ["breakfast", "lunch", "snack", "dinner"]
    })


@app.get("/admin/recipes/new", response_class=HTMLResponse)
async def recipe_new(
    request: Request,
    admin: str = Depends(get_current_admin)
):
    """Форма создания нового рецепта"""
    return templates.TemplateResponse("recipe_edit.html", {
        "request": request,
        "admin": admin,
        "recipe": None,
        "meal_types": ["breakfast", "lunch", "snack", "dinner"]
    })


@app.post("/admin/recipes/save")
async def recipe_save(
    request: Request,
    id: Optional[int] = Form(None),
    day_number: int = Form(...),
    meal_type: str = Form(...),
    title: str = Form(...),
    shopping: str = Form(...),
    portion: str = Form(...),
    recipe_text: str = Form(..., alias="recipe"),
    calories_text: str = Form(...),
    calories_value: int = Form(0),
    proteins: Optional[float] = Form(None),
    fats: Optional[float] = Form(None),
    carbs: Optional[float] = Form(None),
    cooking_time: Optional[int] = Form(None),
    is_premium: bool = Form(False),
    admin: str = Depends(get_current_admin)
):
    """Сохранение рецепта"""
    recipe_data = {
        "id": id,
        "day_number": day_number,
        "meal_type": meal_type,
        "title": title,
        "shopping": shopping,
        "portion": portion,
        "recipe": recipe_text,
        "calories_text": calories_text,
        "calories_value": calories_value,
        "proteins": proteins,
        "fats": fats,
        "carbs": carbs,
        "cooking_time": cooking_time,
        "is_premium": is_premium,
        "tags": []  # Можно добавить позже
    }

    recipe_id = await AdminDatabase.create_or_update_recipe(recipe_data)

    return RedirectResponse(
        url=f"/admin/recipes/{recipe_id}?success=recipe_saved",
        status_code=302
    )


@app.post("/admin/recipes/{recipe_id}/delete")
async def delete_recipe(
    request: Request,
    recipe_id: int,
    admin: str = Depends(get_current_admin)
):
    """Удаление рецепта"""
    success = await AdminDatabase.delete_recipe(recipe_id)
    if success:
        return RedirectResponse(url="/admin/recipes?success=recipe_deleted", status_code=302)
    else:
        raise HTTPException(status_code=400, detail="Failed to delete recipe")


# ==================== API ENDPOINTS (для AJAX) ====================

@app.get("/admin/api/stats")
async def api_stats(admin: str = Depends(get_current_admin)):
    """API: Получение статистики"""
    return {
        "users": await AdminDatabase.get_users_stats(),
        "recipes": await AdminDatabase.get_recipes_stats(),
        "engagement": await AdminDatabase.get_engagement_stats()
    }


@app.get("/admin/api/users/search")
async def api_users_search(
    q: str = Query(..., min_length=2),
    admin: str = Depends(get_current_admin)
):
    """API: Поиск пользователей"""
    users = await AdminDatabase.get_users_list(search=q, limit=10)
    return {"users": users}


# ==================== RUN ====================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
