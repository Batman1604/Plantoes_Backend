from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.core.config import settings
from app.core.database import Base, engine
from app.routes.plantao_routes import router

Base.metadata.create_all(bind=engine)


def ensure_foreign_keys() -> None:
    if engine.dialect.name != "postgresql":
        return

    statement = text(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'fk_locais_usuario_id'
            ) THEN
                ALTER TABLE locais
                ADD CONSTRAINT fk_locais_usuario_id
                FOREIGN KEY (usuario_id)
                REFERENCES usuarios(id)
                ON DELETE CASCADE;
            END IF;

            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'fk_plantoes_local_id'
            ) THEN
                ALTER TABLE plantoes
                ADD CONSTRAINT fk_plantoes_local_id
                FOREIGN KEY (local_id)
                REFERENCES locais(id)
                ON DELETE CASCADE;
            END IF;
        END $$;
        """
    )

    with engine.begin() as connection:
        connection.execute(statement)


ensure_foreign_keys()


app = FastAPI(title=settings.app_name)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)


@app.get("/health", tags=["health"])
def health_check():
    return {"status": "ok"}
