"""
Аутентификация для админ-панели
Simple session-based auth with admin credentials from env
"""
import os
from datetime import datetime, timedelta
from typing import Optional
from fastapi import Request, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from passlib.context import CryptContext
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

# Настройки безопасности
SECRET_KEY = os.getenv("ADMIN_SECRET_KEY", "your-super-secret-key-change-in-production")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH", "")  # bcrypt hash
SESSION_COOKIE_NAME = "admin_session"
MAX_AGE = 3600 * 8  # 8 часов

# Если хеш не задан, используем plain text (только для разработки!)
ADMIN_PASSWORD_PLAIN = os.getenv("ADMIN_PASSWORD", "admin123")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
serializer = URLSafeTimedSerializer(SECRET_KEY)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Проверка пароля"""
    if hashed_password:
        return pwd_context.verify(plain_password, hashed_password)
    # Fallback для разработки
    return plain_password == ADMIN_PASSWORD_PLAIN


def create_session_token(username: str) -> str:
    """Создание токена сессии"""
    return serializer.dumps({"username": username, "timestamp": datetime.utcnow().isoformat()})


def verify_session_token(token: str) -> Optional[dict]:
    """Проверка токена сессии"""
    try:
        data = serializer.loads(token, max_age=MAX_AGE)
        return data
    except (BadSignature, SignatureExpired):
        return None


async def get_current_admin(request: Request) -> str:
    """Dependency для проверки авторизации"""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            headers={"Location": "/admin/login"}
        )

    data = verify_session_token(token)
    if not data or data.get("username") != ADMIN_USERNAME:
        raise HTTPException(
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            headers={"Location": "/admin/login"}
        )

    return ADMIN_USERNAME


def authenticate_admin(username: str, password: str) -> bool:
    """Проверка credentials"""
    if username != ADMIN_USERNAME:
        return False
    return verify_password(password, ADMIN_PASSWORD_HASH)
