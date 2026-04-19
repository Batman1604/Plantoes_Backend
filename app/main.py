import logging
import threading
import time

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import settings
from app import models  # noqa: F401
from app.core.database import Base, SessionLocal, engine
from app.routes.plantao_routes import router
from app.services.notificacao_service import NotificacaoService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

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

            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'fk_plantoes_usuario_id'
            ) THEN
                ALTER TABLE plantoes
                ADD CONSTRAINT fk_plantoes_usuario_id
                FOREIGN KEY (usuario_id)
                REFERENCES usuarios(id)
                ON DELETE CASCADE;
            END IF;
        END $$;
        """
    )

    with engine.begin() as connection:
        connection.execute(statement)


def ensure_notificado_column() -> None:
    if engine.dialect.name == "sqlite":
        with engine.begin() as connection:
            columns = connection.execute(text("PRAGMA table_info(plantoes)")).all()
            if not any(column[1] == "notificado" for column in columns):
                connection.execute(text("ALTER TABLE plantoes ADD COLUMN notificado BOOLEAN NOT NULL DEFAULT 0"))
        return

    if engine.dialect.name == "postgresql":
        with engine.begin() as connection:
            exists = connection.execute(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'plantoes' AND column_name = 'notificado'"
                )
            ).scalar() is not None
            if not exists:
                connection.execute(text("ALTER TABLE plantoes ADD COLUMN notificado BOOLEAN NOT NULL DEFAULT FALSE"))
        return


ensure_foreign_keys()
ensure_notificado_column()


def ensure_novas_colunas() -> None:
    if engine.dialect.name == "sqlite":
        with engine.begin() as connection:
            plantoes_cols = connection.execute(text("PRAGMA table_info(plantoes)")).all()
            if not any(column[1] == "executado" for column in plantoes_cols):
                connection.execute(text("ALTER TABLE plantoes ADD COLUMN executado BOOLEAN NOT NULL DEFAULT 0"))
            locais_cols = connection.execute(text("PRAGMA table_info(locais)")).all()
            if not any(column[1] == "executado" for column in locais_cols):
                connection.execute(text("ALTER TABLE locais ADD COLUMN executado BOOLEAN NOT NULL DEFAULT 0"))

            plantoes_cols = connection.execute(text("PRAGMA table_info(plantoes)")).all()
            if not any(column[1] == "pagamento_plantao" for column in plantoes_cols):
                connection.execute(text("ALTER TABLE plantoes ADD COLUMN pagamento_plantao DATE NULL"))
        return

    if engine.dialect.name == "postgresql":
        with engine.begin() as connection:
            executado_exists = connection.execute(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'plantoes' AND column_name = 'executado'"
                )
            ).scalar() is not None
            if not executado_exists:
                connection.execute(text("ALTER TABLE plantoes ADD COLUMN executado BOOLEAN NOT NULL DEFAULT FALSE"))
            executado_exists = connection.execute(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'locais' AND column_name = 'executado'"
                )
            ).scalar() is not None
            if not executado_exists:
                connection.execute(text("ALTER TABLE locais ADD COLUMN executado BOOLEAN NOT NULL DEFAULT FALSE"))

            pagamento_exists = connection.execute(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'plantoes' AND column_name = 'pagamento_plantao'"
                )
            ).scalar() is not None
            if not pagamento_exists:
                connection.execute(text("ALTER TABLE plantoes ADD COLUMN pagamento_plantao DATE NULL"))
        return



ensure_novas_colunas()

app = FastAPI(title=settings.app_name)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    logger.warning("HTTP exception: %s", exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "message": str(exc.detail), "data": None},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    logger.warning("Validação falhou: %s", exc.errors())
    return JSONResponse(
        status_code=422,
        content={"success": False, "message": "Dados de entrada inválidos.", "data": exc.errors()},
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Erro interno não tratado")
    return JSONResponse(
        status_code=500,
        content={"success": False, "message": "Erro interno do servidor.", "data": None},
    )


def notificacao_worker() -> None:
    while True:
        try:
            with SessionLocal() as db:
                NotificacaoService.processar_notificacoes(db)
        except Exception:
            logger.exception("Erro ao executar worker de notificações")
        time.sleep(60)


@app.on_event("startup")
def iniciar_worker_notificacoes() -> None:
    thread = threading.Thread(target=notificacao_worker, daemon=True, name="notificacao-worker")
    thread.start()


@app.get("/health", tags=["health"])
def health_check():
    return {"status": "ok"}
