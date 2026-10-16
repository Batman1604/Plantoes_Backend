"""contador de tentativas no código de cadastro (padrão do AUD-032)

Revision ID: 08c8c3d741e1
Revises: be3b6ae22177
Create Date: 2026-10-16

O código de 6 dígitos de /auth/confirmar-cadastro não tinha contador de
erros: o único freio era o limite por IP. Conferido antes: cadastros_pendentes
não tem coluna equivalente (só os códigos de reset e de telefone ganharam
`tentativas` em 8a344e601b8a). Entra `tentativas`, com 0 nas linhas existentes.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '08c8c3d741e1'
down_revision: Union[str, Sequence[str], None] = 'be3b6ae22177'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("cadastros_pendentes", sa.Column("tentativas", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("cadastros_pendentes", "tentativas")
