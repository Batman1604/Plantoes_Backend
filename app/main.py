import logging
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select, text
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import settings
from app import models  # noqa: F401
from app.core.database import Base, SessionLocal, engine
from app.routes.plantao_routes import router
from app.routes.politicas_routes import router as politicas_router
from app.routes.whatsapp_routes import router as whatsapp_router
from app.repositories.plantao_repository import TokenRevogadoRepository
from app.services.notificacao_service import NotificacaoService

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

if settings.jwt_secret_key == "change-me-in-production":
    logger.warning("JWT_SECRET_KEY está usando o valor padrão. Defina JWT_SECRET_KEY no .env antes de ir para produção.")

if "DeusAbutre" in settings.database_url:
    logger.warning("DATABASE_URL está usando credenciais padrão. Defina DATABASE_URL no .env antes de ir para produção.")

Base.metadata.create_all(bind=engine)


# Funções de migração leve para criar colunas, índices e constraints conforme necessário
# em diferentes dialects de banco de dados.
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


def ensure_util_corrido_column() -> None:
    if engine.dialect.name == "sqlite":
        with engine.begin() as connection:
            locais_cols = connection.execute(text("PRAGMA table_info(locais)")).all()
            if not any(column[1] == "util_corrido" for column in locais_cols):
                connection.execute(text("ALTER TABLE locais ADD COLUMN util_corrido BOOLEAN NOT NULL DEFAULT 1"))
        return

    if engine.dialect.name == "postgresql":
        with engine.begin() as connection:
            util_corrido_exists = connection.execute(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'locais' AND column_name = 'util_corrido'"
                )
            ).scalar() is not None
            if not util_corrido_exists:
                connection.execute(
                    text("ALTER TABLE locais ADD COLUMN util_corrido BOOLEAN NOT NULL DEFAULT TRUE")
                )
        return


ensure_util_corrido_column()


def ensure_indexes() -> None:
    if engine.dialect.name == "sqlite":
        with engine.begin() as connection:
            connection.execute(text(
                "DROP INDEX IF EXISTS ix_plantoes_data"
            ))
            connection.execute(text(
                "DROP INDEX IF EXISTS ix_plantoes_usuario_data"
            ))
            connection.execute(text(
                "DROP INDEX IF EXISTS ix_plantoes_usuario_local_data"
            ))
            cols = connection.execute(text("PRAGMA table_info(plantoes)")).all()
            col_names = {c[1] for c in cols}
            if "data_fim" not in col_names:
                connection.execute(text("ALTER TABLE plantoes ADD COLUMN data_fim DATE"))
                connection.execute(text(
                    "UPDATE plantoes SET data_fim = "
                    "CASE WHEN horario_fim < horario_inicio THEN date(data_inicio, '+1 day') "
                    "ELSE data_inicio END"
                ))
            if "descricao" not in col_names:
                connection.execute(text("ALTER TABLE plantoes ADD COLUMN descricao TEXT"))
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_locais_usuario_id ON locais (usuario_id)"
            ))
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_plantoes_usuario_id ON plantoes (usuario_id)"
            ))
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_plantoes_local_id ON plantoes (local_id)"
            ))
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_plantoes_data_inicio ON plantoes (data_inicio)"
            ))
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_plantoes_usuario_data_inicio ON plantoes (usuario_id, data_inicio)"
            ))
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_financeiro_local_id ON financeiro (local_id)"
            ))
        return

    if engine.dialect.name == "postgresql":
        with engine.begin() as connection:
            connection.execute(text(
                "DROP INDEX IF EXISTS ix_plantoes_data"
            ))
            connection.execute(text(
                "DROP INDEX IF EXISTS ix_plantoes_usuario_data"
            ))
            connection.execute(text(
                "DROP INDEX IF EXISTS ix_plantoes_usuario_local_data"
            ))
            connection.execute(text(
                "ALTER TABLE plantoes ADD COLUMN IF NOT EXISTS data_fim DATE"
            ))
            connection.execute(text(
                "UPDATE plantoes SET data_fim = "
                "CASE WHEN horario_fim < horario_inicio THEN data_inicio + INTERVAL '1 day' "
                "ELSE data_inicio END "
                "WHERE data_fim IS NULL"
            ))
            connection.execute(text(
                "ALTER TABLE plantoes ALTER COLUMN data_fim SET NOT NULL"
            ))
            connection.execute(text(
                "ALTER TABLE plantoes ADD COLUMN IF NOT EXISTS descricao TEXT"
            ))
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_locais_usuario_id ON locais (usuario_id)"
            ))
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_plantoes_usuario_id ON plantoes (usuario_id)"
            ))
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_plantoes_local_id ON plantoes (local_id)"
            ))
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_plantoes_data_inicio ON plantoes (data_inicio)"
            ))
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_plantoes_usuario_data_inicio ON plantoes (usuario_id, data_inicio)"
            ))
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_financeiro_local_id ON financeiro (local_id)"
            ))
        return


ensure_indexes()


def ensure_email_verificado_e_device_token_columns() -> None:
    if engine.dialect.name == "sqlite":
        with engine.begin() as connection:
            cols = connection.execute(text("PRAGMA table_info(usuarios)")).all()
            col_names = {c[1] for c in cols}
            if "email_verificado" not in col_names:
                connection.execute(
                    text("ALTER TABLE usuarios ADD COLUMN email_verificado BOOLEAN NOT NULL DEFAULT 0")
                )
            if "device_token" not in col_names:
                connection.execute(
                    text("ALTER TABLE usuarios ADD COLUMN device_token VARCHAR(255) NULL")
                )
            if "avatar" not in col_names:
                connection.execute(text("ALTER TABLE usuarios ADD COLUMN avatar VARCHAR(255) NULL"))
            if "profissao" not in col_names:
                connection.execute(text("ALTER TABLE usuarios ADD COLUMN profissao VARCHAR(255) NULL"))
        return

    if engine.dialect.name == "postgresql":
        with engine.begin() as connection:
            ev_exists = connection.execute(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'usuarios' AND column_name = 'email_verificado'"
                )
            ).scalar() is not None
            if not ev_exists:
                connection.execute(
                    text("ALTER TABLE usuarios ADD COLUMN email_verificado BOOLEAN NOT NULL DEFAULT FALSE")
                )
            dt_exists = connection.execute(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'usuarios' AND column_name = 'device_token'"
                )
            ).scalar() is not None
            if not dt_exists:
                connection.execute(
                    text("ALTER TABLE usuarios ADD COLUMN device_token VARCHAR(255) NULL")
                )
            connection.execute(text(
                "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS avatar VARCHAR(255) NULL"
            ))
            connection.execute(text(
                "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS profissao VARCHAR(255) NULL"
            ))
        return


ensure_email_verificado_e_device_token_columns()


def ensure_financeiro_table() -> None:
    if engine.dialect.name == "sqlite":
        with engine.begin() as connection:
            table_exists = connection.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='financeiro'")
            ).fetchone()
            if not table_exists:
                connection.execute(text(
                    "CREATE TABLE financeiro ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "local_id INTEGER NOT NULL REFERENCES locais(id) ON DELETE CASCADE, "
                    "valor NUMERIC(10,2) NOT NULL, "
                    "data DATE NOT NULL"
                    ")"
                ))
        return

    if engine.dialect.name == "postgresql":
        with engine.begin() as connection:
            table_exists = connection.execute(
                text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name = 'financeiro'"
                )
            ).scalar() is not None
            if not table_exists:
                connection.execute(text(
                    "CREATE TABLE financeiro ("
                    "id SERIAL PRIMARY KEY, "
                    "local_id INTEGER NOT NULL REFERENCES locais(id) ON DELETE CASCADE, "
                    "valor NUMERIC(10,2) NOT NULL, "
                    "data DATE NOT NULL"
                    ")"
                ))
        return


ensure_financeiro_table()


def ensure_transferido_column() -> None:
    if engine.dialect.name == "sqlite":
        with engine.begin() as connection:
            cols = connection.execute(text("PRAGMA table_info(plantoes)")).all()
            col_names = {c[1] for c in cols}
            if "transferido" not in col_names:
                connection.execute(text("ALTER TABLE plantoes ADD COLUMN transferido BOOLEAN NOT NULL DEFAULT 0"))
            if "recebido_via_transferencia" not in col_names:
                connection.execute(text("ALTER TABLE plantoes ADD COLUMN recebido_via_transferencia BOOLEAN NOT NULL DEFAULT 0"))
        return

    if engine.dialect.name == "postgresql":
        with engine.begin() as connection:
            exists = connection.execute(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'plantoes' AND column_name = 'transferido'"
                )
            ).scalar() is not None
            if not exists:
                connection.execute(text("ALTER TABLE plantoes ADD COLUMN transferido BOOLEAN NOT NULL DEFAULT FALSE"))
            connection.execute(text(
                "ALTER TABLE plantoes ADD COLUMN IF NOT EXISTS recebido_via_transferencia BOOLEAN NOT NULL DEFAULT FALSE"
            ))
        return


ensure_transferido_column()


def ensure_telefone_verificado_e_verificacoes_table() -> None:
    if engine.dialect.name == "sqlite":
        with engine.begin() as connection:
            cols = connection.execute(text("PRAGMA table_info(usuarios)")).all()
            col_names = {c[1] for c in cols}
            if "telefone_verificado" not in col_names:
                connection.execute(
                    text("ALTER TABLE usuarios ADD COLUMN telefone_verificado BOOLEAN NOT NULL DEFAULT 0")
                )
            table_exists = connection.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='verificacoes_telefone'")
            ).fetchone()
            if not table_exists:
                connection.execute(text(
                    "CREATE TABLE verificacoes_telefone ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "usuario_id INTEGER NOT NULL UNIQUE REFERENCES usuarios(id) ON DELETE CASCADE, "
                    "codigo VARCHAR(6) NOT NULL, "
                    "expira_em DATETIME NOT NULL"
                    ")"
                ))
                connection.execute(text("CREATE INDEX ix_verificacoes_telefone_usuario_id ON verificacoes_telefone (usuario_id)"))
        return

    if engine.dialect.name == "postgresql":
        with engine.begin() as connection:
            tv_exists = connection.execute(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'usuarios' AND column_name = 'telefone_verificado'"
                )
            ).scalar() is not None
            if not tv_exists:
                connection.execute(
                    text("ALTER TABLE usuarios ADD COLUMN telefone_verificado BOOLEAN NOT NULL DEFAULT FALSE")
                )
            connection.execute(text(
                "CREATE TABLE IF NOT EXISTS verificacoes_telefone ("
                "id SERIAL PRIMARY KEY, "
                "usuario_id INTEGER NOT NULL UNIQUE REFERENCES usuarios(id) ON DELETE CASCADE, "
                "codigo VARCHAR(6) NOT NULL, "
                "expira_em TIMESTAMPTZ NOT NULL"
                ")"
            ))
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_verificacoes_telefone_usuario_id ON verificacoes_telefone (usuario_id)"
            ))
        return


ensure_telefone_verificado_e_verificacoes_table()


def ensure_trocas_table() -> None:
    _new_cols = ("local", "data_inicio", "data_fim", "horario_inicio", "horario_fim")

    if engine.dialect.name == "sqlite":
        with engine.begin() as connection:
            cols = connection.execute(text("PRAGMA table_info(trocas)")).all()
            col_names = {c[1] for c in cols}
            # Recriar se a tabela não existir ou faltar colunas novas
            if not col_names or not all(c in col_names for c in _new_cols):
                connection.execute(text("DROP TABLE IF EXISTS trocas"))
                connection.execute(text(
                    "CREATE TABLE trocas ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "local_id INTEGER NULL REFERENCES locais(id) ON DELETE SET NULL, "
                    "nome_transferir VARCHAR(120) NULL, "
                    "nome_receber VARCHAR(120) NULL, "
                    "telefone_transferir VARCHAR(30) NULL, "
                    "telefone_receber VARCHAR(30) NULL, "
                    "local VARCHAR(120) NULL, "
                    "data_inicio DATE NULL, "
                    "data_fim DATE NULL, "
                    "horario_inicio TIME NULL, "
                    "horario_fim TIME NULL"
                    ")"
                ))
                connection.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_trocas_local_id ON trocas (local_id)"
                ))
        return

    if engine.dialect.name == "postgresql":
        with engine.begin() as connection:
            connection.execute(text("""
                CREATE TABLE IF NOT EXISTS trocas (
                    id SERIAL PRIMARY KEY,
                    local_id INTEGER NULL REFERENCES locais(id) ON DELETE SET NULL,
                    nome_transferir VARCHAR(120) NULL,
                    nome_receber VARCHAR(120) NULL,
                    telefone_transferir VARCHAR(30) NULL,
                    telefone_receber VARCHAR(30) NULL,
                    local VARCHAR(120) NULL,
                    data_inicio DATE NULL,
                    data_fim DATE NULL,
                    horario_inicio TIME NULL,
                    horario_fim TIME NULL
                )
            """))
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_trocas_local_id ON trocas (local_id)"
            ))
            # Remover NOT NULL de colunas antigas (caso a tabela já existia)
            for col in ("local_id", "nome_transferir", "nome_receber", "telefone_transferir", "telefone_receber"):
                try:
                    connection.execute(text(f"ALTER TABLE trocas ALTER COLUMN {col} DROP NOT NULL"))
                except Exception:
                    pass
            # Adicionar colunas novas se não existirem
            for col, tipo in (
                ("local", "VARCHAR(120)"),
                ("data_inicio", "DATE"),
                ("data_fim", "DATE"),
                ("horario_inicio", "TIME"),
                ("horario_fim", "TIME"),
            ):
                connection.execute(text(f"ALTER TABLE trocas ADD COLUMN IF NOT EXISTS {col} {tipo} NULL"))
        return


ensure_trocas_table()


def ensure_bonificacoes_table() -> None:
    if engine.dialect.name == "sqlite":
        with engine.begin() as connection:
            cols = connection.execute(text("PRAGMA table_info(bonificacoes)")).all()
            col_names = {c[1] for c in cols}
            # Schema antigo = tem local_id mas NÃO tem plantao_id
            if "local_id" in col_names and "plantao_id" not in col_names:
                connection.execute(text("DROP TABLE bonificacoes"))
                col_names = set()
            if not col_names:
                connection.execute(text(
                    "CREATE TABLE bonificacoes ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "plantao_id INTEGER NOT NULL REFERENCES plantoes(id) ON DELETE CASCADE, "
                    "local_id INTEGER NULL REFERENCES locais(id) ON DELETE SET NULL, "
                    "valor NUMERIC(10,2) NOT NULL, "
                    "descricao TEXT NULL"
                    ")"
                ))
                connection.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_bonificacoes_plantao_id ON bonificacoes (plantao_id)"
                ))
            else:
                if "descricao" not in col_names:
                    connection.execute(text("ALTER TABLE bonificacoes ADD COLUMN descricao TEXT NULL"))
                if "local_id" not in col_names:
                    connection.execute(text("ALTER TABLE bonificacoes ADD COLUMN local_id INTEGER NULL REFERENCES locais(id) ON DELETE SET NULL"))
        return

    if engine.dialect.name == "postgresql":
        with engine.begin() as connection:
            # Schema antigo = tem local_id mas NÃO tem plantao_id
            old_schema = connection.execute(text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'bonificacoes' AND column_name = 'local_id'"
            )).scalar() and not connection.execute(text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'bonificacoes' AND column_name = 'plantao_id'"
            )).scalar()
            if old_schema:
                connection.execute(text("DROP TABLE bonificacoes"))
            connection.execute(text("""
                CREATE TABLE IF NOT EXISTS bonificacoes (
                    id SERIAL PRIMARY KEY,
                    plantao_id INTEGER NOT NULL REFERENCES plantoes(id) ON DELETE CASCADE,
                    local_id INTEGER NULL REFERENCES locais(id) ON DELETE SET NULL,
                    valor NUMERIC(10,2) NOT NULL,
                    descricao TEXT NULL
                )
            """))
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_bonificacoes_plantao_id ON bonificacoes (plantao_id)"
            ))
            connection.execute(text(
                "ALTER TABLE bonificacoes ADD COLUMN IF NOT EXISTS descricao TEXT NULL"
            ))
            connection.execute(text(
                "ALTER TABLE bonificacoes ADD COLUMN IF NOT EXISTS local_id INTEGER NULL REFERENCES locais(id) ON DELETE SET NULL"
            ))
        return


ensure_bonificacoes_table()


def ensure_financeiro_em_atraso_column() -> None:
    if engine.dialect.name == "sqlite":
        with engine.begin() as connection:
            cols = connection.execute(text("PRAGMA table_info(financeiro)")).fetchall()
            col_names = [c[1] for c in cols]
            if "em_atraso" not in col_names:
                connection.execute(text("ALTER TABLE financeiro ADD COLUMN em_atraso BOOLEAN NOT NULL DEFAULT 0"))
        return

    if engine.dialect.name == "postgresql":
        with engine.begin() as connection:
            connection.execute(text(
                "ALTER TABLE financeiro ADD COLUMN IF NOT EXISTS em_atraso BOOLEAN NOT NULL DEFAULT FALSE"
            ))
        return


ensure_financeiro_em_atraso_column()


def backfill_financeiro() -> None:
    """Sincroniza financeiro para todos os plantões existentes — chamado uma vez na inicialização."""
    from app.repositories.plantao_repository import FinanceiroRepository

    with SessionLocal() as db:
        combos = db.execute(
            select(
                models.Plantao.local_id,
                func.extract("month", models.Plantao.data_inicio).label("mes"),
                func.extract("year", models.Plantao.data_inicio).label("ano"),
            ).distinct()
        ).fetchall()

        for row in combos:
            FinanceiroRepository.sync_mes(db, row.local_id, int(row.mes), int(row.ano))


backfill_financeiro()



_notificacao_thread: threading.Thread | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Context manager que inicializa o worker de notificações quando o app sobe."""
    global _notificacao_thread
    _notificacao_thread = threading.Thread(target=notificacao_worker, daemon=True, name="notificacao-worker")
    _notificacao_thread.start()
    yield


_cors_origins = [o.strip() for o in settings.allowed_origins.split(",") if o.strip()]

_uploads_dir = Path("uploads/avatars")
_uploads_dir.mkdir(parents=True, exist_ok=True)

app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")
app.include_router(router)
app.include_router(politicas_router)
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


@app.get("/health", tags=["health"])
def health_check():
    """Endpoint de saúde que verifica se o aplicativo e o worker de notificações estão ativos."""
    worker_alive = _notificacao_thread is not None and _notificacao_thread.is_alive()
    if not worker_alive:
        logger.error("Worker de notificações não está em execução")
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "notifications_worker": "down"},
        )
    return {"status": "ok", "notifications_worker": "up"}
