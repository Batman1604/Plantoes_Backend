"""fecha as tabelas para a Data API do Supabase

Revision ID: 5c2e8f1a9b47
Revises: 4e1b7c9a2d36
Create Date: 2026-10-16

No Supabase, os privilégios padrão do schema public concedem tudo aos papéis
anon e authenticated, que são os da Data API (PostgREST). Conferido depois do
primeiro upgrade head no projeto: as 18 tabelas, usuarios e
codigos_reset_senha incluídas, tinham SELECT para anon e nenhuma com RLS. Com a
Data API ligada, a chave anon, que é pública, lia e escrevia tudo.

O backend acessa o banco só como o dono das tabelas, então nada aqui o afeta.
Revoga o que já existe e muda os privilégios padrão do papel que roda as
migrações, para que tabelas criadas depois também nasçam fechadas.

Só age onde o papel anon existe: num Postgres comum não há o que revogar.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '5c2e8f1a9b47'
down_revision: Union[str, Sequence[str], None] = '4e1b7c9a2d36'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon')
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
                REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon, authenticated;
                REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated;
                ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM anon, authenticated;
                ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM anon, authenticated;
            END IF;
        END
        $$;
    """)


def downgrade() -> None:
    # Sem volta de propósito: reabrir as tabelas para a chave pública não é um
    # estado que alguém deva obter por um downgrade.
    pass
