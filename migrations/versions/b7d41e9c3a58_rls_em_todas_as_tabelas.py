"""liga RLS em todas as tabelas do schema public

Revision ID: b7d41e9c3a58
Revises: 5c2e8f1a9b47
Create Date: 2026-10-17

Segunda camada sobre a 5c2e8f1a9b47 (que tirou os privilégios de anon e
authenticated): com RLS ligado e nenhuma política, a Data API do Supabase não
devolve linha nenhuma mesmo que um GRANT volte por engano. O painel do
Supabase também marca tabela sem RLS como exposta.

O backend não é afetado: conecta como o dono das tabelas (o papel postgres,
que no Supabase ainda tem BYPASSRLS), e o dono só passa pelo RLS com FORCE ROW
LEVEL SECURITY, que não é usado. Num Postgres comum vale o mesmo, porque quem
migra é o dono.

Pega toda tabela que existe em public na hora (inclusive alembic_version e
legadas fora dos models). Tabela criada DEPOIS nasce sem RLS: a migração que
criar uma deve ligá-lo também.

Só no PostgreSQL; no SQLite dos testes é no-op.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'b7d41e9c3a58'
down_revision: Union[str, Sequence[str], None] = '5c2e8f1a9b47'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _em_todas_as_tabelas(comando: str) -> None:
    # quote_ident em vez de format('%I'): o "%" seria escapado como "%%" pelo
    # SQLAlchemy (paramstyle do psycopg) e chegaria assim ao Postgres.
    op.execute(f"""
        DO $$
        DECLARE
            tabela record;
        BEGIN
            FOR tabela IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
                EXECUTE 'ALTER TABLE public.' || quote_ident(tabela.tablename) || ' {comando} ROW LEVEL SECURITY';
            END LOOP;
        END
        $$;
    """)


def upgrade() -> None:
    # get_context, e não get_bind: funciona também no modo --sql (sem conexão).
    if op.get_context().dialect.name != "postgresql":
        return
    _em_todas_as_tabelas("ENABLE")


def downgrade() -> None:
    if op.get_context().dialect.name != "postgresql":
        return
    _em_todas_as_tabelas("DISABLE")
