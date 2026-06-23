from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from app.core.config import settings

# Motor async — una sola instancia para toda la app
engine = create_async_engine(
    settings.database_url,
    echo=settings.alfred_env == "development",  # logs SQL en dev
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,   # verifica conexión antes de usarla
)

# Fábrica de sesiones
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def init_db() -> None:
    """Crea las tablas si no existen. Llamar al arrancar la app."""
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency de FastAPI. Uso:

        @router.get("/ejemplo")
        async def endpoint(session: AsyncSession = Depends(get_session)):
            ...
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
