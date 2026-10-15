"""plantoes.local_id nulável com ON DELETE SET NULL (AUD-006)

Revision ID: a6db921de6c8
Revises: 32f7d1940532
Create Date: 2026-10-15

Decisão do usuário para o AUD-006: quando quem passou o plantão exclui o
local (ou a conta), o plantão de quem recebeu CONTINUA, com local_id nulo e o
nome do hospital preservado na troca. Para isso a coluna precisa aceitar nulo
e a FK não pode apagar a linha: `ON DELETE SET NULL`. Os plantões do próprio
dono continuam sendo excluídos — pelo ORM, com tombstone, e, se a exclusão vier
da conta, pela FK de plantoes.usuario_id.

O banco legado já tinha a coluna nulável; a FK dele chama
`fk_plantoes_local_id`. Os dois casos são tratados conferindo o estado.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a6db921de6c8'
down_revision: Union[str, Sequence[str], None] = '32f7d1940532'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _fks_local_id():
    return [
        fk for fk in sa.inspect(op.get_bind()).get_foreign_keys("plantoes")
        if fk["constrained_columns"] == ["local_id"]
    ]


def _recriar_fk(ondelete: str) -> None:
    for fk in _fks_local_id():
        op.drop_constraint(fk["name"], "plantoes", type_="foreignkey")
    op.create_foreign_key("plantoes_local_id_fkey", "plantoes", "locais", ["local_id"], ["id"], ondelete=ondelete)


def upgrade() -> None:
    op.alter_column("plantoes", "local_id", existing_type=sa.Integer(), nullable=True)
    _recriar_fk("SET NULL")


def downgrade() -> None:
    nulos = op.get_bind().execute(sa.text("SELECT count(*) FROM plantoes WHERE local_id IS NULL")).scalar()
    if nulos:
        raise RuntimeError(
            f"plantoes.local_id é nulo em {nulos} linha(s) — plantões recebidos cujo local foi "
            "excluído pelo dono. Voltar a NOT NULL exigiria apagá-los; downgrade recusado."
        )
    _recriar_fk("CASCADE")
    op.alter_column("plantoes", "local_id", existing_type=sa.Integer(), nullable=False)
