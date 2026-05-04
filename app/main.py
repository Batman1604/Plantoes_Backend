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
from app.routes.politicas_routes import router as politicas_router
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
            locais_cols = connection.execute(text("PRAGMA table_info(locais)")).all()
            if not any(column[1] == "cor" for column in locais_cols):
                connection.execute(text("ALTER TABLE locais ADD COLUMN cor VARCHAR(20) NULL"))

            plantoes_cols = connection.execute(text("PRAGMA table_info(plantoes)")).all()
            if not any(column[1] == "pagamento_plantao" for column in plantoes_cols):
                connection.execute(text("ALTER TABLE plantoes ADD COLUMN pagamento_plantao DATE NULL"))
            plantoes_cols = connection.execute(text("PRAGMA table_info(plantoes)")).all()
            if not any(column[1] == "pagamento_recebido" for column in plantoes_cols):
                connection.execute(
                    text("ALTER TABLE plantoes ADD COLUMN pagamento_recebido BOOLEAN NOT NULL DEFAULT 0")
                )
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

            cor_exists = connection.execute(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'locais' AND column_name = 'cor'"
                )
            ).scalar() is not None
            if not cor_exists:
                connection.execute(text("ALTER TABLE locais ADD COLUMN cor VARCHAR(20) NULL"))

            pagamento_exists = connection.execute(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'plantoes' AND column_name = 'pagamento_plantao'"
                )
            ).scalar() is not None
            if not pagamento_exists:
                connection.execute(text("ALTER TABLE plantoes ADD COLUMN pagamento_plantao DATE NULL"))

            pagamento_recebido_exists = connection.execute(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'plantoes' AND column_name = 'pagamento_recebido'"
                )
            ).scalar() is not None
            if not pagamento_recebido_exists:
                connection.execute(
                    text("ALTER TABLE plantoes ADD COLUMN pagamento_recebido BOOLEAN NOT NULL DEFAULT FALSE")
                )
        return



ensure_novas_colunas()


def ensure_plano_column() -> None:
    if engine.dialect.name == "sqlite":
        with engine.begin() as connection:
            usuarios_cols = connection.execute(text("PRAGMA table_info(usuarios)")).all()
            if not any(column[1] == "plano" for column in usuarios_cols):
                connection.execute(text("ALTER TABLE usuarios ADD COLUMN plano VARCHAR(20) NOT NULL DEFAULT 'free'"))
            connection.execute(
                text(
                    "UPDATE usuarios "
                    "SET plano = LOWER(TRIM(plano)) "
                    "WHERE plano IS NOT NULL"
                )
            )
            connection.execute(
                text(
                    "UPDATE usuarios "
                    "SET plano = 'free' "
                    "WHERE plano IS NULL OR plano = '' OR plano NOT IN ('free', 'premium')"
                )
            )
        return

    if engine.dialect.name == "postgresql":
        with engine.begin() as connection:
            plano_exists = connection.execute(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'usuarios' AND column_name = 'plano'"
                )
            ).scalar() is not None
            if not plano_exists:
                connection.execute(text("ALTER TABLE usuarios ADD COLUMN plano VARCHAR(20) NOT NULL DEFAULT 'free'"))
            connection.execute(
                text(
                    "UPDATE usuarios "
                    "SET plano = LOWER(TRIM(plano)) "
                    "WHERE plano IS NOT NULL"
                )
            )
            connection.execute(
                text(
                    "UPDATE usuarios "
                    "SET plano = 'free' "
                    "WHERE plano IS NULL OR plano = '' OR plano NOT IN ('free', 'premium')"
                )
            )

            constraint_exists = connection.execute(
                text(
                    "SELECT 1 FROM pg_constraint "
                    "WHERE conname = 'ck_usuarios_plano'"
                )
            ).scalar() is not None
            if not constraint_exists:
                connection.execute(
                    text(
                        "ALTER TABLE usuarios "
                        "ADD CONSTRAINT ck_usuarios_plano "
                        "CHECK (plano IN ('free', 'premium'))"
                    )
                )
        return


ensure_plano_column()


def ensure_usuario_datas_columns() -> None:
    if engine.dialect.name == "sqlite":
        with engine.begin() as connection:
            usuarios_cols = connection.execute(text("PRAGMA table_info(usuarios)")).all()
            if not any(column[1] == "criado_em" for column in usuarios_cols):
                connection.execute(text("ALTER TABLE usuarios ADD COLUMN criado_em DATETIME NULL"))
                connection.execute(text("UPDATE usuarios SET criado_em = CURRENT_TIMESTAMP WHERE criado_em IS NULL"))
            usuarios_cols = connection.execute(text("PRAGMA table_info(usuarios)")).all()
            if not any(column[1] == "pagamento_realizado" for column in usuarios_cols):
                connection.execute(text("ALTER TABLE usuarios ADD COLUMN pagamento_realizado DATE NULL"))
        return

    if engine.dialect.name == "postgresql":
        with engine.begin() as connection:
            criado_em_exists = connection.execute(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'usuarios' AND column_name = 'criado_em'"
                )
            ).scalar() is not None
            if not criado_em_exists:
                connection.execute(text("ALTER TABLE usuarios ADD COLUMN criado_em TIMESTAMPTZ"))
                connection.execute(text("UPDATE usuarios SET criado_em = NOW() WHERE criado_em IS NULL"))

            pagamento_realizado_exists = connection.execute(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'usuarios' AND column_name = 'pagamento_realizado'"
                )
            ).scalar() is not None
            if not pagamento_realizado_exists:
                connection.execute(text("ALTER TABLE usuarios ADD COLUMN pagamento_realizado DATE NULL"))
        return


ensure_usuario_datas_columns()


def ensure_sobreaviso_e_endereco_columns() -> None:
    if engine.dialect.name == "sqlite":
        with engine.begin() as connection:
            plantoes_cols = connection.execute(text("PRAGMA table_info(plantoes)")).all()
            if not any(column[1] == "sobreaviso" for column in plantoes_cols):
                connection.execute(text("ALTER TABLE plantoes ADD COLUMN sobreaviso BOOLEAN NOT NULL DEFAULT 0"))
            locais_cols = connection.execute(text("PRAGMA table_info(locais)")).all()
            if not any(column[1] == "endereco_local" for column in locais_cols):
                connection.execute(text("ALTER TABLE locais ADD COLUMN endereco_local VARCHAR(255) NULL"))
        return

    if engine.dialect.name == "postgresql":
        with engine.begin() as connection:
            sobreaviso_exists = connection.execute(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'plantoes' AND column_name = 'sobreaviso'"
                )
            ).scalar() is not None
            if not sobreaviso_exists:
                connection.execute(
                    text("ALTER TABLE plantoes ADD COLUMN sobreaviso BOOLEAN NOT NULL DEFAULT FALSE")
                )

            endereco_exists = connection.execute(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'locais' AND column_name = 'endereco_local'"
                )
            ).scalar() is not None
            if not endereco_exists:
                connection.execute(text("ALTER TABLE locais ADD COLUMN endereco_local VARCHAR(255) NULL"))
        return


ensure_sobreaviso_e_endereco_columns()

app = FastAPI(title=settings.app_name)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)
app.include_router(politicas_router)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    if exc.status_code == 401:
        logger.warning("Tentativa de acesso não autorizado")
        return JSONResponse(
            status_code=401,
            content={
                "success": False,
                "message": "Não autenticado",
                "code": "UNAUTHORIZED",
            },
        )

    if exc.status_code == 403:
        message = str(exc.detail)
        return JSONResponse(
            status_code=403,
            content={
                "success": False,
                "message": message,
            },
        )

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
