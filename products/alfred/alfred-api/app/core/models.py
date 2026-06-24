"""
Modelos de la base de datos usando SQLModel.

Este módulo contiene todas las clases que heredan de SQLModel y representan
tablas en PostgreSQL. Cada modelo se mapea a una tabla automáticamente por SQLAlchemy.
"""
from datetime import datetime
from uuid import UUID, uuid4

from sqlmodel import Column, ForeignKey, String, UniqueConstraint, func
from typing import Optional as TypingOptional
from app.core.database import SQLModel


class User(SQLModel, table=True):
    """Modelo de Usuario para la base de datos.

    Tabla 'usuarios' con campos:
        - id: Identificador único (UUID automático)
        - email: Correo electrónico del usuario (único en la DB)
        - nombre: Nombre completo del usuario
        - creado_en: Timestamp cuando se creó el registro
    """
    __tablename__ = 'usuarios'
    __table_args__: dict[str, ...] = (
        UniqueConstraint('email', name='uq_usuarios_email'),
    )

    id: Optional[UUID] = Column(
        String(36),
        primary_key=True,
        default_factory=lambda: str(uuid4()),
    )
    email: str = Column(
        String(255),
        nullable=False,
        index=True,
    )
    nombre: Optional[str] = Column(
        String(100),
        default=None,
    )
    creado_en: datetime = Column(
        type_=datetime,
        nullable=False,
        server_default=func.now(),
    )