"""usuarios.sessoes_invalidas_antes_de (AUD-031)

Revision ID: 3091fb7f2ef0
Revises: 92b4d29ea4bf
Create Date: 2026-10-15

Trocar ou redefinir a senha não derrubava as sessões abertas: a revogação só
existia por jti (o token do próprio aparelho, no logout). Nenhuma coluna
existente servia (conferido em usuarios e tokens_revogados), então entra uma
nova: tokens com `iat` anterior a ela são recusados. Nula = nada a recusar.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '3091fb7f2ef0'
down_revision: Union[str, Sequence[str], None] = '92b4d29ea4bf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("usuarios", sa.Column("sessoes_invalidas_antes_de", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("usuarios", "sessoes_invalidas_antes_de")
