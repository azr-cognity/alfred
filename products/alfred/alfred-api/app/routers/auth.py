"""
Router de autenticación para el endpoint POST /api/auth/register.

Responsabilidad:
- Registrar nuevos usuarios con validaciones Pydantic
- Verificar que email no esté duplicado en la base de datos
- Hashear password antes de guardar
- Retornar 201 si se crea usuario exitosamente, o 409 para emails duplicados
"""
import uuid
from typing import Union

import structlog
from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.database import get_db_session
from app.models.user import UserCreateModel
from app.schemas.auth import RegisterUserResponseSchema, UserRegisterRequestSchema
from app.utils.password import hash_password

logger = structlog.get_logger()
router = APIRouter(prefix="/api/auth", tags=["auth"])


def _generate_user_id() -> str:
    """
    Genera un identificador UUID v4 único para el usuario.
    
    Returns:
        String con formato de UUID version 4 (36 caracteres).
    """
    return str(uuid.uuid4())


@router.post(
    "/register",
    response_model=Union[RegisterUserResponseSchema],
    status_code=status.HTTP_201_CREATED,
)
async def register_user(
    payload: UserRegisterRequestSchema, db_session: AsyncSession = Depends(get_db_session)  # type: ignore[misc]
) -> Union[dict[str, str | uuid.UUID]]:
    """
    Registra un nuevo usuario en el sistema.

    Args:
        payload: Objeto validado con UserRegisterRequestSchema que contiene email y password
                  ambos campos son requeridos por Pydantic v2 con validaciones específicas
        db_session: Sesión de base de datos async inyectada mediante Depends de FastAPI

    Returns:
        Dict validated by RegisterUserResponseSchema on success (status 201)

    Raises:
        HTTPException: With status_code=409 Conflict if email ya está registrado en la DB
                      Con status_code=500 si falla el hash de password o insertar usuario.
    """
    logger.info("Processando registro nuevo user", email=payload.email)

    # Buscar si existe email duplicado antes de crear cualquier objeto
    check_query = select(UserCreateModel).where(UserCreateModel.email == payload.email.lower())
    existing_user_result = await db_session.execute(check_query)
    existing_user: UserCreateModel | None = existing_user_result.scalar_one_or_none()

    if existing_user is not None:
        logger.warning("Intento registro usuario duplicado", email=payload.email)
        raise Exception(f"El correo electrónico {payload.email} ya está registrado.")

    # Hashear password antes de guardar en DB (no almacenar passwords plaintext)
    hashed_password: str = hash_password(payload.password)
    logger.info("Password user hasheado exitosamente", email=payload.email, iterations=10)

    try:
        # Crear y persistir objeto User model en la base de datos async
        new_user = UserCreateModel(
            id=_generate_user_id(),
            name=f"{payload.first_name} {payload.last_name}",
            email=payload.email.lower().strip(),  # Normalizar email (case insensitive)
            password=hashed_password,
        )

        db_session.add(new_user)  # type: ignore[attr-defined]
    except Exception as insert_error:
        logger.error("Error inserting user to database", error=str(insert_error))
        raise ValueError(f"Failed create usuario en base de datos") from insert_error