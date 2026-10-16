"""normaliza o e-mail dos usuários (AUD-058)

Revision ID: 92b4d29ea4bf
Revises: 969110c7e106
Create Date: 2026-10-15

A partir desta versão todo e-mail de usuário é gravado sem espaços nas pontas
e em minúsculas (listener em app/models.py). As linhas antigas podem ter sido
gravadas como vieram pelo cadastro legado (`POST /api/usuarios`, removido no
AUD-011) — e o UNIQUE do Postgres diferencia caixa, então 'Maria@x' e
'maria@x' podem ser duas contas.

Esta migração só NORMALIZA: se duas contas colidem depois da normalização, ela
FALHA listando os ids, sem alterar nada. Decidir qual conta fica (e o que
fazer com os dados da outra) é do operador, não de uma migração.

Downgrade: no-op. A grafia original não foi guardada, e voltar a permitir
caixa mista reintroduziria o defeito.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '92b4d29ea4bf'
down_revision: Union[str, Sequence[str], None] = '969110c7e106'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conexao = op.get_bind()
    colisoes = conexao.execute(sa.text(
        "SELECT lower(trim(email)) AS normalizado, string_agg(id::text, ',' ORDER BY id) AS ids "
        "FROM usuarios GROUP BY lower(trim(email)) HAVING count(*) > 1"
    )).all()
    if colisoes:
        detalhe = "; ".join(f"ids {ids}" for _, ids in colisoes)
        raise RuntimeError(
            "AUD-058: há contas cujo e-mail só difere por maiúsculas/espaços "
            f"({detalhe}). Resolva as duplicatas à mão antes de migrar."
        )
    conexao.execute(sa.text(
        "UPDATE usuarios SET email = lower(trim(email)) WHERE email <> lower(trim(email))"
    ))


def downgrade() -> None:
    pass
