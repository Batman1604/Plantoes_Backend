"""Montagem das respostas de plantão, solicitação de troca e configuração de amigo.

Estas funções viviam em app.routes.plantao_routes e o SyncService as importava
de lá, junto com os handlers de plantão — a camada de serviço dependia da de
apresentação (ver auditoria A-13). Saíram para cá sem mudança de comportamento:
a rota e o sync montam o MESMO contrato a partir do mesmo código.
"""

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from decimal import Decimal

from app.models import AmigoConfiguracao, Plantao, SolicitacaoTroca, Troca
from app.schemas.plantao_schema import AmigoConfiguracaoResponse, PlantaoResponse, PlantaoTrocaResumo


def _serializar_plantao(plantao: Plantao, troca: Troca | None) -> PlantaoResponse:
    """Monta o contrato de plantão a partir de uma troca JÁ resolvida.

    Separado da busca para que a listagem possa resolver todas as trocas de uma
    vez — ver _plantoes_com_troca.
    """
    result = PlantaoResponse.model_validate(plantao).model_dump()
    if plantao.local_id is None and troca is not None and troca.local and not result["local_nome"]:
        # AUD-006 (contrato K1): o dono do local o excluiu e o plantão
        # recebido foi desvinculado; o nome do hospital ficou na troca.
        result["local_nome"] = troca.local
    if troca is None:
        return PlantaoResponse.model_validate(result)

    if troca.plantao_id_transferido == plantao.id:
        result["tipo_troca"] = "transferido"
        result["troca"] = {
            "id": troca.id,
            "papel": "transferido",
            "contraparte_nome": troca.nome_transferir or "",
            "contraparte_telefone": troca.telefone_transferir or "",
            "contraparte_observacao": troca.observacao_transferir,
            "contraparte_usuario_id": troca.contraparte_usuario_id,
            "plantao_relacionado_id": troca.plantao_id_recebido,
        }
    else:
        result["tipo_troca"] = "recebido"
        result["troca"] = {
            "id": troca.id,
            "papel": "recebido",
            "contraparte_nome": troca.nome_receber or "",
            "contraparte_telefone": troca.telefone_receber or "",
            "contraparte_observacao": troca.observacao_receber,
            "contraparte_usuario_id": troca.contraparte_usuario_id_receber,
            "plantao_relacionado_id": troca.plantao_id_transferido,
        }
    return PlantaoResponse.model_validate(result)


def _plantao_com_troca(db: Session, plantao: Plantao) -> PlantaoResponse:
    """Serializa UM plantão, buscando a troca vinculada. Para listas, use
    _plantoes_com_troca: esta função faz uma consulta por chamada."""
    troca = db.scalar(
        select(Troca).where(
            or_(
                Troca.plantao_id_transferido == plantao.id,
                Troca.plantao_id_recebido == plantao.id,
            )
        ).order_by(Troca.id.desc())
    )
    return _serializar_plantao(plantao, troca)


def _plantoes_com_troca(db: Session, plantoes: list[Plantao]) -> list[PlantaoResponse]:
    """Serializa uma lista de plantões com UMA consulta de trocas, não uma por
    plantão.

    A listagem chamava _plantao_com_troca em laço, o que dava uma consulta por
    plantão: medido em 205 consultas para 200 plantões em GET /api/plantoes, e
    o mesmo padrão em GET /api/sync/pull — que o cliente chama de forma
    recorrente (ver auditoria A-03).
    """
    if not plantoes:
        return []

    ids = [p.id for p in plantoes]
    # Ordenado por id crescente para que, ao montar o dicionário, a troca de id
    # MAIOR sobrescreva a menor — mesmo resultado do order_by(id.desc()) seguido
    # de "pega o primeiro" que a versão por plantão fazia.
    trocas = db.scalars(
        select(Troca)
        .where(
            or_(
                Troca.plantao_id_transferido.in_(ids),
                Troca.plantao_id_recebido.in_(ids),
            )
        )
        .order_by(Troca.id.asc())
    ).all()

    troca_por_plantao: dict[int, Troca] = {}
    for troca in trocas:
        if troca.plantao_id_transferido is not None:
            troca_por_plantao[troca.plantao_id_transferido] = troca
        if troca.plantao_id_recebido is not None:
            troca_por_plantao[troca.plantao_id_recebido] = troca

    return [_serializar_plantao(p, troca_por_plantao.get(p.id)) for p in plantoes]


def _serializar_amigo_configuracao(config) -> AmigoConfiguracaoResponse:
    return AmigoConfiguracaoResponse(
        id=config.id,
        usuario_id=config.usuario_id,
        amigo_usuario_id=config.amigo_usuario_id,
        modo_visibilidade=config.modo_visibilidade,
        mostrar_valores=config.mostrar_valores,
        cor=config.cor,
        locais_permitidos=[item.local_id for item in config.locais_permitidos],
        atualizado_em=config.atualizado_em,
    )


def _plantao_troca_resumo(plantao: Plantao) -> PlantaoTrocaResumo:
    return PlantaoTrocaResumo(
        id=plantao.id,
        local_nome=plantao.local.nome_local if plantao.local else "",
        data_inicio=plantao.data_inicio,
        horario_inicio=plantao.horario_inicio,
        data_fim=plantao.data_fim,
        horario_fim=plantao.horario_fim,
        valor=plantao.valor,
    )


def _alvos_com_valor_oculto(db: Session, visualizador_id: int, solicitacoes) -> set[int]:
    """AUD-029/AUD-062: ids das solicitações cujo plantão ALVO (do destinatário)
    chega a quem pediu sem o valor, porque o destinatário escondeu os valores
    dele para essa pessoa (mostrar_valores=false). Só o alvo: o plantão que o
    remetente oferece ele mesmo escolheu mostrar.
    """
    candidatas = [
        s for s in solicitacoes
        if s.remetente_usuario_id == visualizador_id and s.plantao_alvo_id is not None
    ]
    if not candidatas:
        return set()
    donos_que_ocultam = set(db.scalars(
        select(AmigoConfiguracao.usuario_id).where(
            AmigoConfiguracao.amigo_usuario_id == visualizador_id,
            AmigoConfiguracao.usuario_id.in_({s.destinatario_usuario_id for s in candidatas}),
            AmigoConfiguracao.mostrar_valores.is_(False),
        )
    ))
    return {s.id for s in candidatas if s.destinatario_usuario_id in donos_que_ocultam}


def _plantao_recebido_troca_resumo(solicitacao: SolicitacaoTroca, ocultar_valor: bool = False) -> PlantaoTrocaResumo | None:
    if solicitacao.plantao_alvo_id is not None:
        if solicitacao.plantao_alvo is None:
            return None
        resumo = _plantao_troca_resumo(solicitacao.plantao_alvo)
        if ocultar_valor:
            resumo.valor = Decimal("0")
        return resumo

    if solicitacao.plantao_recebido_local_id is None:
        return None
    local = solicitacao.plantao_recebido_local
    return PlantaoTrocaResumo(
        id=None,
        local_nome=local.nome_local if local else "",
        data_inicio=solicitacao.plantao_recebido_data_inicio,
        horario_inicio=solicitacao.plantao_recebido_horario_inicio,
        data_fim=solicitacao.plantao_recebido_data_fim,
        horario_fim=solicitacao.plantao_recebido_horario_fim,
        valor=solicitacao.plantao_recebido_valor,
    )
