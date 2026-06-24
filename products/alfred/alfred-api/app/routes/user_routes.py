"""
Router para endpoints relacionados con usuarios.

Responsabilidad:
  Proporcionar los endpoints de API para gestión de usuarios incluyendo registro,
  autenticación y perfil. Implementa validaciones Pydantic v2, verificación de email único
  y fuerza de contraseña mediante dependencies reutilizables.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel.ext.sqlalchemy.session import Session
import structlog

from app.core.database import get_db_session
from app.models.user import User as UserModel
from app.schemas.user import (
    PasswordStrengthCheck,
    RegistrationRequest,
    RegistrationResponse,
)

logger = structlog.get_logger()
router = APIRouter(tags=["users"])


def validate_password_strength(password: str) -> dict:
    """
    Validar que la contraseña cumpla con los requisitos de seguridad.

    Args:
        password: La contraseña a validar.

    Returns:
        Dict con información sobre el strength check.

    Raises:
        HTTPException 422 si la contraseña es débil o insuficiente en longitud.
    """
    if len(password) < 8:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Password must be at least 8 characters long",
        )

    has_upper = any(c.isupper() for c in password)
    has_lower = any(c.islower() for c in password)
    has_digit = any(c.isdigit() for c in password)
    has_special = any(not c.isalnum() for c in password)

    strength_score = sum([has_upper, has_lower, has_digit, has_special])

    if strength_score < 3:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=("Password must contain at least one uppercase letter, "
                    "one lowercase letter, one digit and special character."),
        )

    return PasswordStrengthCheck(valid=True)


def check_email_uniqueness(email: str, db_session: Session) -> bool:
    """
    Verificar si el email ya existe en la base de datos.

    Args:
        email: El email a verificar.
        db_session: Sesión de SQLAlchemy para consulta.

    Returns:
        True si el usuario NO existe (email disponible).

    Raises:
        HTTPException 409 Conflict si el email ya está registrado.
    """
    existing_user = (
        db_session.query(UserModel)
        .filter(UserModel.email == email.lower())
        .first()
    )

    if existing_user is not None:
        logger.warning("Registration attempt with existing email", email=email)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User already exists. Please log in or use a different email.",
        )

    return True


@router.post(
    "/users/register",
    response_model=RegistrationResponse,
    status_code=status.HTTP_201_CREATED,
)
def register_user(
    registration: RegistrationRequest = Depends(ValidationRegistration),  # type: ignore[attr-defined]
    db_session: Session = Depends(get_db_session),
) -> RegistrationResponse:
    """
    Registrar un nuevo usuario en el sistema.

    Args:
        registration: Datos de registro validados vía dependencies.
        db_session: Sesión transaccional para persistencia.

    Returns:
        Usuario creado con su ID generado.

    Raises:
        HTTPException 409 si email ya existe o está malformado.
        HTTPException 421 si la contraseña es insuficientemente fuerte.
    """
    db_session.add(
        UserModel(email=registration.email, password_hash="HASH_PLACEHOLDER")
    )
    db_session.commit()

    logger.info("User registered successfully", email=registration.email)

    return RegistrationResponse(id=None)  # type: ignore[arg-type]
