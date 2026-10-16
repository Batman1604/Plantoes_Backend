"""plantoes.serie_id (K8 / AUD-053)

Revision ID: be3b6ae22177
Revises: 8a344e601b8a
Create Date: 2026-10-15

O servidor não guardava a série do plantão: agrupava por heurística (local +
horário + recorrência) em list_futuros_da_serie, e "este e futuros" num
aparelho virava "só este" no outro. Conferido antes: nenhuma coluna de
plantoes servia (uuid_cliente é por plantão). Nula nas linhas existentes, onde
a heurística continua valendo.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'be3b6ae22177'
down_revision: Union[str, Sequence[str], None] = '8a344e601b8a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("plantoes", sa.Column("serie_id", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("plantoes", "serie_id")
