"""telefone normalizado indexado

Revision ID: 7287d665b814
Revises: 09a7c5244080
Create Date: 2026-09-27 21:06:26.407396

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7287d665b814'
down_revision: Union[str, Sequence[str], None] = '09a7c5244080'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('usuarios', sa.Column('telefone_normalizado', sa.String(length=20), nullable=True))
    op.create_index(op.f('ix_usuarios_telefone_normalizado'), 'usuarios', ['telefone_normalizado'], unique=False)

    # Preenche as linhas que já existem.
    #
    # O autogenerate cria a coluna mas não migra dado — e sem este passo todo
    # usuário já cadastrado ficaria com telefone_normalizado nulo, ou seja,
    # invisível na busca por telefone até editar o próprio perfil. Falha
    # silenciosa: ninguém receberia erro, as pessoas simplesmente não seriam
    # encontradas.
    #
    # A regra é a mesma de app.models.normalizar_telefone: só dígitos, e sem o
    # DDI 55 quando o número fica longo demais por causa dele. Repetida aqui em
    # SQL de propósito — uma migração não deve importar código da aplicação, que
    # muda com o tempo e deixaria de descrever o que esta revisão fez.
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """
            UPDATE usuarios
               SET telefone_normalizado = NULLIF(
                   CASE
                       WHEN length(regexp_replace(telefone, '\D', '', 'g')) > 11
                        AND regexp_replace(telefone, '\D', '', 'g') LIKE '55%'
                       THEN substring(regexp_replace(telefone, '\D', '', 'g') FROM 3)
                       ELSE regexp_replace(telefone, '\D', '', 'g')
                   END, '')
             WHERE telefone IS NOT NULL
            """
        )


def downgrade() -> None:
    op.drop_index(op.f('ix_usuarios_telefone_normalizado'), table_name='usuarios')
    op.drop_column('usuarios', 'telefone_normalizado')
