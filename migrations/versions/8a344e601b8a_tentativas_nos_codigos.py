"""contador de tentativas nos códigos de reset e de telefone (AUD-032)

Revision ID: 8a344e601b8a
Revises: 3091fb7f2ef0
Create Date: 2026-10-15

Os códigos de 6 dígitos de redefinição de senha e de verificação de telefone
não tinham contador de erros: o único freio era o limite por IP e por rota.
Conferido antes: só `whatsapp_sessoes.tentativas_codigo` existia (outra
tabela, do WhatsApp). Entra `tentativas` nas duas tabelas, com 0 nas linhas
existentes.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '8a344e601b8a'
down_revision: Union[str, Sequence[str], None] = '3091fb7f2ef0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABELAS = ("codigos_reset_senha", "verificacoes_telefone")


def upgrade() -> None:
    for tabela in _TABELAS:
        op.add_column(tabela, sa.Column("tentativas", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    for tabela in _TABELAS:
        op.drop_column(tabela, "tentativas")
