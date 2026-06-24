"""
Schemas Pydantic v2 para modelos de usuario.

Responsabilidad:
  Definir los esquemas de validación para operaciones CRUD con usuarios,
incluyendo email, password hashing y control de campos expuestos en respuestas API.

Cómo funciona:
  1. UserCreate valida datos entrantes para creación (email pattern, password length)
  2. userInDB incluye campo hashed sin exponerlo públicamente
  3. UserResponse excluye el campo 'hashed' usando model_validate_for_output=True
"""

import re
from typing import ClassVar, List as TypeList, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from uuid import UUID

class PasswordValidator:
    """Validador de contraseña según requisitos del proyecto."""
    
    # Patrón: mínimo 8 chars, al menos una mayúscula, minúsicula, dígito y caracter especial
    PATTERN = re.compile(r'^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[@$!%*?&#])[A-Za-z\d@$!%*?&#]{8,}$')
    
    @classmethod
    def validate(cls, value: str) -> bool:
        """
        Valida si la contraseña cumple los requisitos.
        
        Args:
            value: Contraseña a validar (sin verificar el valor real)
        
        Returns:
            True si es válida, False de lo contrario
        """
        return bool(cls.PATTERN.match(value))
    
    @classmethod
    def error_message(cls) -> str:
        """
        Devuelve mensaje de error para validación de contraseña.
        
        Returns:
            String con requisitos de la contraseña
        """
        return (
            'La contraseña debe tener mínimo 8 caracteres, '
            'una mayúscula, una minúscula, un dígito y un carácter especial.'
        )

# ── Schema para crear usuarios (input validation) ───────────────────────────────-
class UserCreate(BaseModel):
    """
    Esquema para creación de usuario.
    
    Atributos:
        email: Correo electrónico válido del usuario
        password: Contraseña segura con requisitos específicos
        name: Nombre completo opcional del usuario
    """
    model_config = ConfigDict(str_strip_whitespace=True)
    
    email: EmailStr = Field(..., description="Correo electrónico del usuario")
    password: str = Field(
        ..., min_length=8,
        pattern=r'^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[@$!%*?&#])[A-Za-z0-9@$!%*?&]{8,}$',
        description="Contraseña segura con requisitos"
    )
    name: Optional[str] = Field(None, min_length=1, max_length=256)

    @field_validator('password')
    @classmethod
    def validate_password(cls, value: str) -> str:
        """
        Valida que la contraseña cumpla con todos los requisitos.
        
        Args:
            value: Contraseña proporcionada por el usuario
        
        Returns:
            La misma contraseña si es válida
        
        Raises:
            ValueError: Si no cumple con los criterios de seguridad
        """
        if not PasswordValidator.validate(value):
            raise ValueError(PasswordValidator.error_message())
        return value

# ── Schema interno para base de datos (incluye hashed) ───────────────────────────
class UserDB(BaseModel):
    """
    Esquema interno con campo 'hashed' excluido en respuestas públicas.
    
    Atributos:
        email: Correo electrónico del usuario
        password_hashed: Hash de la contraseña para verificación
        name: Nombre completo opcional del usuario
        id: Identificador único UUID (opcional)
        created_at: Timestamp de creación (opcional)
        updated_at: Último timestamp de actualización (opcional)
    """
    model_config = ConfigDict(
        validate_assignment=True,
        extra='ignore'
    )
    
    email: EmailStr
    password_hashed: str  # Solo interno, no exponer en respuesta pública
    name: Optional[str] = None
    id: Optional[UUID] = None
    created_at: Optional[str] = Field(default_factory=lambda: "1970-01-01T00:00:00Z")
    updated_at: Optional[str] = Field(default_factory=lambda: "1970-01-01T00:00:00Z")

# ── Schema para respuesta pública (sin password_hashed) ───────────────────────────
class UserResponse(BaseModel):
    """
    Esquema de respuesta del usuario sin datos sensibles.
    
    Atributos:
        email: Correo electrónico público
        name: Nombre completo opcional
        id: Identificador único UUID
        created_at: Timestamp de creación
        updated_at: Último timestamp de actualización
    """
    model_config = ConfigDict(
        from_attributes=True,
        validate_assignment=False,
        extra='allow'
    )
    
    email: EmailStr
    name: Optional[str] = None
    id: UUID
    created_at: str
    updated_at: str
