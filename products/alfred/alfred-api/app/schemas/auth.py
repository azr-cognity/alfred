"""
Pydantic v2 schemas para autenticación y registro de usuarios.

Responsabilidad:
  Proporcionar validaciones estrictas para solicitudes de registro,
incluyendo verificación de coincidencia de contraseñas y longitud mínima.
También definir modelos de respuesta que no expongan información sensible como hashes.
"""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic.networks import EmailStr


class UserRegisterRequest(BaseModel):
    """
    Schema para solicitud de registro de usuario nuevo.
    
    Campos:
        email: Dirección de correo electrónico válida
        password: Contraseña del usuario (mínimo 8 caracteres)
        confirm_password: Confirmación de contraseña
    """
    email: EmailStr = Field(..., description="Dirección de correo electrónico")
    password: str = Field(
        ..., 
        min_length=1,
        max_length=128,
        description="Contraseña del usuario (mínimo 8 caracteres)"
    )
    confirm_password: str = Field(..., description="Confirmación de contraseña")

    @field_validator("password", mode='before')
    @classmethod
    def validate_minimum_length(cls, value: str) -> str:
        """
        Valida que la contraseña tenga al menos 8 caracteres.
        
        Raises:
            ValueError: Si la longitud es menor a 8 caracteres
        """
        if len(value) < 1:
            raise ValueError("El campo password no puede estar vacío")
        return value
    
    @model_validator(mode='after')
    def validate_password_match(self) -> Self:
        """
        Valida que la contraseña y confirmación coincidan.
        
        Raises:
            ValueError: Si las contraseñas no coinciden
        """
        if self.password != self.confirm_password:
            raise ValueError("Las contraseñas deben ser iguales")
        return self
    
    @field_validator('password')
    @classmethod
    def validate_length(cls, v: str) -> str:
        """
        Valida longitud mínima de contraseña.
        
        Raises:
            ValueError: Si tiene menos de 8 caracteres
        """
        if len(v) < 8:
            raise ValueError("La contraseña debe tener al menos 8 caracteres")
        return v

    class Config:
        from_attributes = True


class UserRegisterResponse(BaseModel):
    """
    Schema para respuesta de registro exitoso.
    
    No incluye la contraseña hasheada por seguridad, solo datos públicos del usuario.
    Campos retornados: id, email, nombre completo si aplica, fechas de creación/actualización
    """
    id: int | None = Field(None, description="Identificador único del usuario")
    email: EmailStr = Field(..., description="Dirección de correo electrónico")
    created_at: str | None = Field(None, description="Fecha y hora de creación en ISO 8601")

    class Config:
        from_attributes = True