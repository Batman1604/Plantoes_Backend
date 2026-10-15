"""Montagem das respostas de plantão, solicitação de troca e configuração de amigo.

Estas funções viviam em app.routes.plantao_routes e o SyncService as importava
de lá, junto com os handlers de plantão — a camada de serviço dependia da de
apresentação (ver auditoria A-13). Saíram para cá sem mudança de comportamento:
a rota e o sync montam o MESMO contrato a partir do mesmo código.
"""

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Plantao, SolicitacaoTroca, Troca
from app.schemas.plantao_schema import AmigoConfiguracaoResponse, PlantaoResponse, PlantaoTrocaResumo


def _serializar_plantao(plantao: Plantao, troca: Troca | None) -> PlantaoResponse:
    """Monta o contrato de plantão a partir de uma troca JÁ resolvida.

    Separado da busca para que a listagem possa resolver todas as trocas de uma
    vez — ver _plantoes_com_troca.
    """
    result = PlantaoResponse.model_validate(plantao).model_dump()
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


def _plantao_recebido_troca_resumo(solicitacao: SolicitacaoTroca) -> PlantaoTrocaResumo | None:
    if solicitacao.plantao_alvo_id is not None:
        return _plantao_troca_resumo(solicitacao.plantao_alvo) if solicitacao.plantao_alvo else None

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
