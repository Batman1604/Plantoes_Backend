import logging
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import settings
from app import models  # noqa: F401
from app.core.database import SessionLocal, engine
from app.routes.plantao_routes import router
from app.routes.politicas_routes import router as politicas_router
from app.routes.sync_routes import router as sync_router
from app.routes.whatsapp_routes import router as whatsapp_router
from app.repositories.plantao_repository import TokenRevogadoRepository
from app.services.notificacao_service import NotificacaoService

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

if settings.jwt_secret_key == "change-me-in-production":
    if settings.environment == "production":
        raise RuntimeError("JWT_SECRET_KEY não pode usar o valor padrão em produção. Defina JWT_SECRET_KEY no .env.")
    logger.warning("JWT_SECRET_KEY está usando o valor padrão. Defina JWT_SECRET_KEY no .env antes de ir para produção.")


# ============================================================================
# Schema do banco
# ============================================================================
# Antes havia aqui Base.metadata.create_all() em tempo de import, seguido de 26
# funções ensure_*() com DDL cru e dois backfills — cerca de 1.100 linhas
# executadas a CADA inicialização, em cada processo.
#
# Os problemas concretos disso:
#
#  * create_all só cria tabelas que faltam. Ele NUNCA altera uma tabela
#    existente, então toda coluna acrescentada depois da criação do banco
#    dependia de uma função ensure_* acertar, para sempre, em todo deploy.
#  * backfill_financeiro() fazia SELECT DISTINCT sobre a tabela `plantoes`
#    inteira, sem filtro de usuário, e chamava sync_mes por combinação — cada
#    um com três agregações e um commit. Em escala modesta, dezenas de milhares
#    de consultas antes de aceitar a primeira requisição.
#  * ensure_indexes() executava DROP INDEX de índices que deixaram de existir
#    havia muito tempo, a cada boot, para sempre.
#  * Com múltiplos workers, ALTER TABLE concorrente pega ACCESS EXCLUSIVE e
#    trava ou falha num deploy rolante.
#  * Não havia versionamento: era impossível saber quais alterações um banco já
#    tinha recebido, e não existia caminho de volta.
#
# O schema agora é versionado pelo Alembic (ver migrations/). Aplicar com:
#
#     alembic upgrade head
#
# Os testes continuam usando Base.metadata.create_all (ver tests/conftest.py):
# a suíte roda em SQLite e cria um banco novo a cada execução, onde migração
# incremental não tem o que fazer e só custaria tempo.
_MIGRACOES_PENDENTES_MSG = (
    "O banco não está na última migração. Rode `alembic upgrade head` antes de "
    "subir a aplicação — sem isso, colunas novas simplesmente não existem e o "
    "erro aparece como UndefinedColumn no meio de uma requisição."
)


def _revisao_do_banco() -> str | None:
    """Revisão atualmente aplicada no banco, ou None se o Alembic nunca rodou."""
    with engine.connect() as connection:
        existe = connection.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_name = 'alembic_version'"
            )
        ).scalar()
        if not existe:
            return None
        return connection.execute(text("SELECT version_num FROM alembic_version")).scalar()


def verificar_migracoes() -> None:
    """Confere se o banco está na última migração.

    Em produção, recusa subir — é a mesma postura das outras guardas deste
    arquivo (JWT_SECRET_KEY, DATABASE_URL, ALLOWED_ORIGINS): falhar na
    inicialização, com a instrução do que fazer, em vez de quebrar depois no
    meio de uma requisição do usuário.

    Fora de produção apenas avisa, porque o banco local pode estar
    deliberadamente desatualizado enquanto se escreve uma migração nova.
    """
    if engine.dialect.name != "postgresql":
        return

    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
        head = ScriptDirectory.from_config(cfg).get_current_head()
        atual = _revisao_do_banco()
    except Exception as exc:
        # AUD-044: em produção, não conseguir verificar (alembic.ini fora da
        # imagem, banco inacessível na subida) recusa subir — antes só avisava
        # e o servidor subia "saudável" sem saber o estado do schema.
        if settings.environment == "production":
            raise RuntimeError(
                "Não foi possível verificar o estado das migrações; em produção o servidor não sobe assim."
            ) from exc
        logger.warning("Não foi possível verificar o estado das migrações.", exc_info=True)
        return

    if atual == head:
        return

    detalhe = f"banco={atual or 'nenhuma migração aplicada'}, esperado={head}"
    if settings.environment == "production":
        raise RuntimeError(f"{_MIGRACOES_PENDENTES_MSG} ({detalhe})")
    logger.warning("%s (%s)", _MIGRACOES_PENDENTES_MSG, detalhe)


verificar_migracoes()


_notificacao_thread: threading.Thread | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Context manager que inicializa o worker de notificações quando o app sobe."""
    global _notificacao_thread
    _notificacao_thread = threading.Thread(target=notificacao_worker, daemon=True, name="notificacao-worker")
    _notificacao_thread.start()
    yield


_cors_origins = [o.strip() for o in settings.allowed_origins.split(",") if o.strip()]

if _cors_origins == ["*"]:
    if settings.environment == "production":
        raise RuntimeError(
            "ALLOWED_ORIGINS não pode ficar em '*' em produção. Defina ALLOWED_ORIGINS "
            "no .env com a lista real de origens (domínio do site/app admin, se houver)."
        )
    logger.warning("ALLOWED_ORIGINS está em '*'. Defina uma lista explícita antes de ir para produção.")

def urls_de_documentacao(environment: str) -> dict[str, str | None]:
    """A-24: /docs, /redoc e /openapi.json mapeiam todos os endpoints para quem
    quer atacar a API, então em produção ficam desligados (404). Fora dela
    continuam ligados, porque são a ferramenta de trabalho do desenvolvimento."""
    if environment == "production":
        return {"docs_url": None, "redoc_url": None, "openapi_url": None}
    return {"docs_url": "/docs", "redoc_url": "/redoc", "openapi_url": "/openapi.json"}


app = FastAPI(
    title=settings.app_name,
    lifespan=lifespan,
    **urls_de_documentacao(settings.environment),
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)
# A-18: sem mount de /uploads — a foto de perfil fica só no aparelho.
app.include_router(router)
app.include_router(politicas_router)
app.include_router(sync_router)
app.include_router(whatsapp_router)


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
                "code": "FORBIDDEN",
            },
        )

    logger.warning("HTTP exception: %s", exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "message": str(exc.detail), "data": None},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = []
    for err in exc.errors():
        sanitized = {k: v for k, v in err.items() if k not in ("ctx", "url")}
        if "ctx" in err:
            sanitized["ctx"] = {k: str(v) for k, v in err["ctx"].items()}
        errors.append(sanitized)
    logger.warning("Validação falhou: %s", errors)
    return JSONResponse(
        status_code=422,
        content={"success": False, "message": "Dados de entrada inválidos.", "data": errors},
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Erro interno não tratado")
    return JSONResponse(
        status_code=500,
        content={"success": False, "message": "Erro interno do servidor.", "data": None},
    )


def notificacao_worker() -> None:
    """Loop contínuo que processa notificações e limpa tokens revogados expirados."""
    cleanup_counter = 0
    while True:
        try:
            with SessionLocal() as db:
                NotificacaoService.processar_notificacoes(db)
        except Exception:
            logger.exception("Erro ao executar worker de notificações")

        cleanup_counter += 1
        if cleanup_counter >= 60:
            cleanup_counter = 0
            try:
                with SessionLocal() as db:
                    removed = TokenRevogadoRepository.cleanup_expired(db)
                    if removed:
                        logger.info("Tokens revogados expirados removidos: %d", removed)
            except Exception:
                logger.exception("Erro ao limpar tokens revogados expirados")

        time.sleep(60)


def _banco_responde() -> bool:
    """AUD-044: `SELECT 1` no banco. Antes o /health respondia 200 com o banco
    fora do ar, e o painel da hospedagem dizia que estava tudo bem."""
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        logger.exception("Banco de dados não respondeu ao /health")
        return False


@app.get("/health", tags=["health"])
def health_check():
    """Endpoint de saúde: banco respondendo e worker de notificações ativo."""
    worker_alive = _notificacao_thread is not None and _notificacao_thread.is_alive()
    banco_ok = _banco_responde()
    if not worker_alive:
        logger.error("Worker de notificações não está em execução")
    if not worker_alive or not banco_ok:
        return JSONResponse(
            status_code=503,
            content={
                "status": "degraded",
                "database": "up" if banco_ok else "down",
                "notifications_worker": "up" if worker_alive else "down",
            },
        )
    return {"status": "ok", "database": "up", "notifications_worker": "up"}
