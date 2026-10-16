"""amizade única por par não ordenado (A-08, pendência)

Revision ID: 4e1b7c9a2d36
Revises: 08c8c3d741e1
Create Date: 2026-10-16

A->B e B->A criados ao mesmo tempo passavam os dois pela checagem da rota
(get_relacao_existente) e gravavam duas linhas para o mesmo par. Índice único
de expressão sobre o par NÃO ordenado: LEAST/GREATEST no Postgres; no SQLite,
os min()/max() escalares (o model gera o mesmo, ver app/models.py).

Conferido antes: amizades só tinha a CHECK de status e os índices simples das
duas colunas; nenhum índice ou constraint sobre o par.

Duplicatas já existentes NÃO são apagadas: a migração conta os pares
repetidos e falha listando os ids, para alguém decidir qual linha fica.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '4e1b7c9a2d36'
down_revision: Union[str, Sequence[str], None] = '08c8c3d741e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDICE = "uq_amizades_par"


def _funcoes() -> tuple[str, str]:
    if op.get_bind().dialect.name == "sqlite":
        return "min", "max"
    return "LEAST", "GREATEST"


def upgrade() -> None:
    menor, maior = _funcoes()
    par = f"{menor}(solicitante_usuario_id, destinatario_usuario_id), {maior}(solicitante_usuario_id, destinatario_usuario_id)"
    duplicatas = op.get_bind().execute(sa.text(
        "SELECT id, solicitante_usuario_id, destinatario_usuario_id FROM amizades a "
        f"WHERE (SELECT COUNT(*) FROM amizades b WHERE "
        f"{menor}(b.solicitante_usuario_id, b.destinatario_usuario_id) = {menor}(a.solicitante_usuario_id, a.destinatario_usuario_id) "
        f"AND {maior}(b.solicitante_usuario_id, b.destinatario_usuario_id) = {maior}(a.solicitante_usuario_id, a.destinatario_usuario_id)) > 1 "
        "ORDER BY id"
    )).all()
    if duplicatas:
        linhas = ", ".join(f"id={d.id} ({d.solicitante_usuario_id}->{d.destinatario_usuario_id})" for d in duplicatas)
        raise RuntimeError(
            f"amizades tem {len(duplicatas)} linha(s) em pares repetidos: {linhas}. "
            "Decida qual linha de cada par fica (e as configurações de amigo correspondentes) "
            "antes de aplicar esta migração; ela não apaga dado."
        )
    op.execute(f"CREATE UNIQUE INDEX {_INDICE} ON amizades ({par})")


def downgrade() -> None:
    op.drop_index(_INDICE, table_name="amizades")
