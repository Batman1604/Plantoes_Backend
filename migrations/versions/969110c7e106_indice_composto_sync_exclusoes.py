"""índice composto em sync_exclusoes (A-05)

Revision ID: 969110c7e106
Revises: a6db921de6c8
Create Date: 2026-10-15

SyncRepository.foi_excluido consulta (usuario_id, entidade, uuid_cliente) a
cada item novo de todo push, numa tabela que só cresce (os tombstones não são
podados). A poda com regra de retenção depende do cliente forçar pull completo
quando o cursor for mais velho que a retenção, e fica para depois.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '969110c7e106'
down_revision: Union[str, Sequence[str], None] = 'a6db921de6c8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NOME = "ix_sync_exclusoes_usuario_entidade_uuid"


def upgrade() -> None:
    existentes = {ix["name"] for ix in sa.inspect(op.get_bind()).get_indexes("sync_exclusoes")}
    if _NOME not in existentes:
        op.create_index(_NOME, "sync_exclusoes", ["usuario_id", "entidade", "uuid_cliente"], unique=False)


def downgrade() -> None:
    op.drop_index(_NOME, table_name="sync_exclusoes")
