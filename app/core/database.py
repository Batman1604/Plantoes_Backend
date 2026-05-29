from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import settings


# Base para todos os modelos SQLAlchemy da aplicação.
class Base(DeclarativeBase):
    pass


# Conexão com o banco de dados e fábrica de sessões.
engine = create_engine(settings.database_url, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db():
    """Dependência do FastAPI para obter uma sessão de banco de dados por requisição."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
