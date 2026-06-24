"""
Servicios de autenticación para usuarios.

Responsabilidad:
  - Hash y verificación de contraseñas usando bcrypt
  - Gestión de sesión asíncrona con SQLAlchemy/SQLModel
  - Validaciones antes de crear usuarios (email único, formato válido)
  - Operaciones CRUD básicas para usuarios autentificados
"""
import re
from typing import Optional

import bcrypt
from sqlalchemy import select
from sqlmodel import SQLModel, Session
from structlog.stdlib import get_logger

from app.core.config import settings
from app.models.user import User

logger = get_logger()
BCRYPT_ROUNDS = 12
EMAIL_REGEX = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')

async def hash_password(password: str) -> str:
    """
    Hash un password usando bcrypt con rounds configurados.

    Args:
        password: Contraseña en texto plano que será hasheada

    Returns:
        String del hash Bcrypt listo para almacenar

    Raises:
        ValueError: Si el password es demasiado corto (mínimo 8 caracteres)
    """
    if len(password) < settings.min_password_length:
        logger.warning(
            "password_too_short",
            length=len(password),
            min_required=settings.min_password_length
        )
        raise ValueError(f"Password debe tener al menos {settings.min_password_length} caracteres")

    salt = bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    hashed = bcrypt.hashpw(password.encode(), salt).decode()
    logger.info("password_hashed", rounds=BCRYPT_ROUNDS, length=len(hashed))
    return hashed

async def verify_password(plain_text: str, password_hash: str) -> bool:
    """
    Verifica si una contraseña coincide con su hash almacenado.

    Args:
        plain_text: Contraseña en texto plano proporcionada por el usuario
        password_hash: Hash Bcrypt almacenado en la base de datos

    Returns:
        True si coinciden, False en caso contrario
    """
    try:
        is_match = bcrypt.checkpw(plain_text.encode(), password_hash.encode())
        logger.info("password_verified", match=is_match)
        return bool(is_match)
    except Exception as e:
        logger.error("password_verify_error", error=str(e))
        raise

async def create_user(session: Session, email: str, username: str, password: str) -> User | None:
    """
Crea un nuevo usuario tras validaciones necesarias.

    Args:
        session: Sesión asíncrona de SQLModel para operaciones DB
        email: Email único del usuario (se valida formato y unicidad)
        username: Nombre de usuario único en el sistema
        password: Contraseña que será hasheada antes de guardar

    Returns:
        Objeto User creado si todo es válido, None si fallan validaciones

    Raises:
        ValueError: Si email ya existe o formato inválido
        RuntimeError: Error inesperado durante creación (no capturado)
    """
    if not EMAIL_REGEX.match(email):
        logger.warning("invalid_email_format", email=email[:20] + "...")
        raise ValueError(f"Formato de correo electrónico inválido")

    existing = session.exec(select(User).where(User.email == email)).first()
    if existing:
        logger.warning("email_already_exists", email=email)
        return None

    hashed_pwd = await hash_password(password)

    user = User(
        username=username,
        email=email,
        password=hashed_pwd,
        is_active=True,
        role="user" if "admin" not in settings.admin_emails else "admin"
    )

    session.add(user)
    await session.commit()
    logger.info("user_created", username=username, email=user.email[:10] + "...")
    return user