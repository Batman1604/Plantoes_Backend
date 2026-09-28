"""uuid_cliente para plantoes criados no servidor

Revision ID: d97138d745a8
Revises: 7287d665b814
Create Date: 2026-09-28 19:51:36.315499

Plantão criado NO SERVIDOR não tem dispositivo por trás para gerar o
uuid_cliente: é o caso de aceitar uma transferência e de aceitar uma
solicitação de troca, que criam o plantão do destinatário. Esses caminhos
gravavam a coluna como nula, e o Postgres aceita vários NULL numa coluna
UNIQUE, então nada reclamava.

O app, recebendo nulo no pull, gerava um uuid aleatório NOVO a cada ciclo e
o gravava por cima da linha. Como a fila de sincronização referencia a linha
por uuid_cliente, qualquer edição feita num plantão recebido ficava órfã: o
push não encontrava a linha, pulava em silêncio, e a entrada nunca saía da
fila — a alteração não chegava ao servidor e o contador de pendências não
zerava mais, o que ainda impedia o usuário de sair da conta.

O modelo passou a ter default para a coluna, o que resolve daqui para a
frente. Esta revisão cuida das linhas que já existem.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'd97138d745a8'
down_revision: Union[str, Sequence[str], None] = '7287d665b814'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Dá um uuid_cliente a todo plantão que ficou sem."""
    # gen_random_uuid() é nativa do Postgres desde a 13 — sem extensão.
    op.execute(
        """
        UPDATE plantoes
           SET uuid_cliente = gen_random_uuid()::text
         WHERE uuid_cliente IS NULL
        """
    )


def downgrade() -> None:
    """Não reverte: qual linha era nula antes deixa de ser distinguível.

    Zerar a coluna de volta apagaria também o uuid de plantões que nasceram
    com um legítimo, e é justamente essa identidade que a sincronização usa
    para casar a fila com a linha. Reverter reintroduziria o defeito que a
    revisão corrige, então o caminho de volta é deliberadamente vazio.
    """
    pass
