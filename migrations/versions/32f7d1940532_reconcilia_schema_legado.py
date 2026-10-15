"""reconcilia o schema legado com os modelos (AUD-008)

Revision ID: 32f7d1940532
Revises: d97138d745a8
Create Date: 2026-10-15

O banco de desenvolvimento nasceu do antigo `create_all` + funções `ensure_*`
e depois recebeu `alembic stamp` na head, sem reconciliação. Resultado: 21
diferenças entre o banco e `app/models.py` (auditoria AUD-008 / C-11), entre
elas `trocas.local_id ON DELETE CASCADE` (excluir um local apagava o histórico
de troca do colega) e `usuarios.telefone NOT NULL` (o primeiro login Google/
Apple de conta nova falhava com IntegrityError).

Escrita à mão, não autogerada: cada passo CONFERE o estado antes de agir, para
servir aos dois bancos que existem:

- banco criado pelas migrations (já está como os modelos): quase tudo é
  no-op;
- banco legado: aplica a correção.

Nada é apagado em silêncio:

- Antes de criar uma FK, conta as linhas órfãs. Se houver, a migração FALHA
  com a lista de ids. Para `solicitacoes_troca.plantao_alvo_id`, o operador
  pode escolher explicitamente `-x orfaos_plantao_alvo=anular`: o id pendurado
  vira NULL (a solicitação, já respondida, continua no histórico). No banco de
  dev de 2026-10-15 havia 5 casos, todos solicitações já aceitas cujo plantão
  alvo foi excluído depois (sem a FK, a cascata do modelo nunca rodou).
- As estruturas mortas (`pagamento_status`, `usuarios.device_token`,
  `plantoes.pagamento_recebido`) só são removidas se estiverem vazias; senão a
  migração falha.
- `NOT NULL` só é imposto se não houver nulo; senão falha.

Fica fora daqui, de propósito:
- `plantoes.local_id` continua nulável no banco legado: AUD-006 passa a usar
  isso (desvincular o plantão recebido quando o dono do local o exclui) e a
  revisão seguinte alinha o modelo e o banco novo.
- `server_default now()` em alguns `atualizado_em` e `usuarios.senha_hash`
  sem tamanho: não afetam o comportamento e o `compare_metadata` não os acusa.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import context, op


# revision identifiers, used by Alembic.
revision: str = '32f7d1940532'
down_revision: Union[str, Sequence[str], None] = 'd97138d745a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _insp():
    return sa.inspect(op.get_bind())


def _contar(sql: str) -> int:
    return op.get_bind().execute(sa.text(sql)).scalar() or 0


def _ids(sql: str) -> list:
    return [linha[0] for linha in op.get_bind().execute(sa.text(sql))]


def _fks_da_coluna(tabela: str, coluna: str) -> list[dict]:
    return [fk for fk in _insp().get_foreign_keys(tabela) if fk["constrained_columns"] == [coluna]]


def _tem_indice(tabela: str, nome: str) -> bool:
    return any(ix["name"] == nome for ix in _insp().get_indexes(tabela))


def _criar_indice_se_faltar(tabela: str, coluna: str) -> None:
    nome = f"ix_{tabela}_{coluna}"
    if not _tem_indice(tabela, nome):
        op.create_index(nome, tabela, [coluna], unique=False)


def _remover_fks_duplicadas(tabela: str, coluna: str) -> None:
    """O legado tem `fk_<tabela>_<coluna>` E `<tabela>_<coluna>_fkey` na mesma
    coluna. Fica a do nome padrão (o que as migrations criam)."""
    fks = _fks_da_coluna(tabela, coluna)
    if len(fks) <= 1:
        return
    padrao = f"{tabela}_{coluna}_fkey"
    manter = next((fk for fk in fks if fk["name"] == padrao), fks[0])
    for fk in fks:
        if fk is not manter:
            op.drop_constraint(fk["name"], tabela, type_="foreignkey")


def _garantir_fk(tabela: str, coluna: str, alvo: str, ondelete: str, sql_orfaos: str) -> None:
    """Cria (ou recria com o ondelete certo) a FK da coluna. Falha se houver órfão."""
    fks = _fks_da_coluna(tabela, coluna)
    certas = [fk for fk in fks if (fk.get("options") or {}).get("ondelete", "").upper() == ondelete]
    if certas and len(fks) == 1:
        return
    orfaos = _ids(sql_orfaos)
    if orfaos:
        raise RuntimeError(
            f"AUD-008: {tabela}.{coluna} tem {len(orfaos)} linha(s) apontando para {alvo} "
            f"inexistente (ids de {tabela}: {orfaos}). A FK não pode ser criada. Nada foi alterado."
        )
    for fk in fks:
        op.drop_constraint(fk["name"], tabela, type_="foreignkey")
    op.create_foreign_key(f"{tabela}_{coluna}_fkey", tabela, alvo, [coluna], ["id"], ondelete=ondelete)


def _coluna(tabela: str, nome: str) -> dict | None:
    return next((c for c in _insp().get_columns(tabela) if c["name"] == nome), None)


def upgrade() -> None:
    # 1. FKs duplicadas no legado.
    _remover_fks_duplicadas("locais", "usuario_id")
    _remover_fks_duplicadas("plantoes", "usuario_id")

    # 2. trocas.local_id: CASCADE -> SET NULL. Com CASCADE, excluir um local
    #    apagava a linha de `trocas` do outro usuário (o local da troca é o de
    #    quem cedeu) e o vínculo de "devolver plantão" sumia (TROCA-04).
    _garantir_fk(
        "trocas", "local_id", "locais", "SET NULL",
        "SELECT t.id FROM trocas t WHERE t.local_id IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM locais l WHERE l.id = t.local_id) ORDER BY t.id",
    )

    # 3. solicitacoes_troca.plantao_alvo_id: FK CASCADE + índice.
    sql_orfaos_alvo = (
        "SELECT s.id FROM solicitacoes_troca s WHERE s.plantao_alvo_id IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM plantoes p WHERE p.id = s.plantao_alvo_id) ORDER BY s.id"
    )
    orfaos_alvo = _ids(sql_orfaos_alvo)
    if orfaos_alvo and context.get_x_argument(as_dictionary=True).get("orfaos_plantao_alvo") == "anular":
        # Escolha explícita do operador (ver docstring do módulo).
        print(f"AUD-008: anulando plantao_alvo_id órfão em solicitacoes_troca ids={orfaos_alvo}")
        op.execute(
            "UPDATE solicitacoes_troca s SET plantao_alvo_id = NULL WHERE s.plantao_alvo_id IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM plantoes p WHERE p.id = s.plantao_alvo_id)"
        )
    elif orfaos_alvo:
        raise RuntimeError(
            f"AUD-008: solicitacoes_troca.plantao_alvo_id aponta para plantão inexistente em "
            f"{len(orfaos_alvo)} linha(s) (ids: {orfaos_alvo}). Nada foi alterado. Revise essas "
            "solicitações e rode de novo, ou rode `alembic -x orfaos_plantao_alvo=anular upgrade head` "
            "para gravar NULL nesses ids (a solicitação continua no histórico)."
        )
    _garantir_fk("solicitacoes_troca", "plantao_alvo_id", "plantoes", "CASCADE", sql_orfaos_alvo)
    _criar_indice_se_faltar("solicitacoes_troca", "plantao_alvo_id")

    # 4. trocas.contraparte_usuario_id[_receber]: FK SET NULL + índices.
    for coluna in ("contraparte_usuario_id", "contraparte_usuario_id_receber"):
        _garantir_fk(
            "trocas", coluna, "usuarios", "SET NULL",
            f"SELECT t.id FROM trocas t WHERE t.{coluna} IS NOT NULL "
            f"AND NOT EXISTS (SELECT 1 FROM usuarios u WHERE u.id = t.{coluna}) ORDER BY t.id",
        )
        _criar_indice_se_faltar("trocas", coluna)

    # 5. Índices faltantes. trocas.plantao_id_* entram em TODO pull
    #    (_plantoes_com_troca faz OR ... IN (...) nessas colunas).
    _criar_indice_se_faltar("trocas", "plantao_id_transferido")
    _criar_indice_se_faltar("trocas", "plantao_id_recebido")
    _criar_indice_se_faltar("bonificacoes", "id")
    _criar_indice_se_faltar("bonificacoes", "local_id")

    # 6. Nulidade conforme o modelo.
    telefone = _coluna("usuarios", "telefone")
    if telefone is not None and not telefone["nullable"]:
        # Conta OAuth nova nasce sem telefone; NOT NULL quebrava o 1º login.
        op.alter_column("usuarios", "telefone", existing_type=sa.String(length=30), nullable=True)
    for tabela, coluna, tipo in (
        ("usuarios", "criado_em", sa.DateTime(timezone=True)),
        ("plantoes", "data_inicio", sa.Date()),
    ):
        info = _coluna(tabela, coluna)
        if info is not None and info["nullable"]:
            nulos = _contar(f"SELECT count(*) FROM {tabela} WHERE {coluna} IS NULL")
            if nulos:
                raise RuntimeError(f"AUD-008: {tabela}.{coluna} tem {nulos} nulo(s); NOT NULL não aplicado. Nada foi alterado.")
            op.alter_column(tabela, coluna, existing_type=tipo, nullable=False)

    # 7. Estruturas mortas (removidas dos modelos em 57ad5e3 e d2c049e), só se vazias.
    if _insp().has_table("pagamento_status"):
        linhas = _contar("SELECT count(*) FROM pagamento_status")
        if linhas:
            raise RuntimeError(f"AUD-008: pagamento_status tem {linhas} linha(s); não removida. Nada foi alterado.")
        op.drop_table("pagamento_status")
    if _coluna("usuarios", "device_token") is not None:
        preenchidos = _contar("SELECT count(*) FROM usuarios WHERE device_token IS NOT NULL")
        if preenchidos:
            raise RuntimeError(f"AUD-008: usuarios.device_token preenchido em {preenchidos} linha(s); não removida.")
        op.drop_column("usuarios", "device_token")
    if _coluna("plantoes", "pagamento_recebido") is not None:
        marcados = _contar("SELECT count(*) FROM plantoes WHERE pagamento_recebido")
        if marcados:
            raise RuntimeError(f"AUD-008: plantoes.pagamento_recebido = true em {marcados} linha(s); não removida.")
        op.drop_column("plantoes", "pagamento_recebido")


def downgrade() -> None:
    """Não reverte, de propósito.

    Tudo o que esta revisão faz é levar o banco legado ao schema que as
    revisões anteriores já declaram (num banco criado por elas, o upgrade é
    quase todo no-op). "Desfazer" significaria recriar FKs duplicadas, voltar
    `trocas.local_id` para CASCADE, que apaga histórico de troca de outro
    usuário, e `usuarios.telefone` para NOT NULL, que quebra o login OAuth —
    ou seja, reintroduzir os defeitos. As estruturas mortas removidas estavam
    vazias. O downgrade roda sem erro e deixa um schema válido para a revisão
    d97138d745a8.
    """
    pass
