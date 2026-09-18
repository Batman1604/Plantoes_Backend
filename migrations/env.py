"""Ambiente do Alembic.

A URL do banco NÃO fica no alembic.ini: vem de app.core.config, a mesma fonte
que a aplicação usa (DATABASE_URL, do .env ou do ambiente). Duplicar a URL num
segundo arquivo é como se acaba migrando o banco errado.
"""

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# Importar os modelos registra todas as tabelas em Base.metadata, que é o que o
# autogenerate compara com o banco.
from app import models  # noqa: F401
from app.core.config import settings
from app.core.database import Base

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Gera o SQL sem conectar — permite revisar o que será executado antes de
    mexer num banco de produção (`alembic upgrade head --sql`)."""
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # compare_type detecta mudança de TIPO de coluna, não só presença.
            # Sem isso, trocar Numeric(10,2) por outro tipo passaria batido no
            # autogenerate.
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
