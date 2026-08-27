from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import User
from app.repositories.plantao_repository import (
    AmigoConfiguracaoRepository,
    AmizadeRepository,
    LocalRepository,
    PlantaoRepository,
    SolicitacaoTrocaRepository,
    SyncRepository,
    TrocaRepository,
)
from app.schemas.plantao_schema import (
    AmizadeAmigoResumo,
    AmizadeListItem,
    LocalCreate,
    LocalResponse,
    LocalUpdate,
    PessoaTrocaResumo,
    PlantaoCreate,
    PlantaoUpdate,
    SolicitacaoTrocaListItem,
    SyncConflito,
    SyncErro,
    SyncExclusaoItem,
    SyncItemResultado,
    SyncLocalItem,
    SyncPlantaoItem,
    SyncPullResponse,
    SyncPushResponse,
    TrocaResponse,
)

# Reutiliza a lógica de negócio já validada dos endpoints normais (limite do plano free,
# recálculo de pagamento, série recorrente, etc.) chamando as próprias
# funções de rota como funções Python comuns — os parâmetros com Depends(...) são apenas
# valores padrão e são ignorados quando db/current_user são passados explicitamente. Isso
# evita reimplementar essas regras (e desalinhar sync do restante do sistema).
from app.routes.plantao_routes import (
    _plantao_com_troca,
    _plantao_recebido_troca_resumo,
    _plantao_troca_resumo,
    _serializar_amigo_configuracao,
    atualizar_local,
    atualizar_plantao,
    criar_local,
    criar_plantao,
    deletar_local,
    deletar_plantao,
)


def _as_utc(value: datetime | None) -> datetime | None:
    """SQLite não preserva timezone (mesmo em colunas DateTime(timezone=True), os valores
    voltam naive), enquanto Postgres devolve aware — e o que vem do payload/query string via
    Pydantic normalmente é aware. Trata naive como UTC (é sempre o que o app grava, via
    datetime.now(UTC)) para que comparações funcionem de forma idêntica nos dois dialects."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


class SyncService:
    """Sincronização offline: POST /api/sync/push (envia mudanças feitas no dispositivo) e
    GET /api/sync/pull (busca mudanças feitas na nuvem). Ver docstring dos schemas Sync* para
    o contrato completo e a regra de conflito (last-write-wins por atualizado_em)."""

    @staticmethod
    def push(db: Session, current_user: User, locais: list[SyncLocalItem], plantoes: list[SyncPlantaoItem]) -> SyncPushResponse:
        resultado = SyncPushResponse(servidor_timestamp=datetime.now(UTC))
        uuid_para_local_id: dict[str, int] = {}

        for item in locais:
            SyncService._push_local(db, current_user, item, resultado, uuid_para_local_id)

        for item in plantoes:
            SyncService._push_plantao(db, current_user, item, resultado, uuid_para_local_id)

        resultado.servidor_timestamp = datetime.now(UTC)
        return resultado

    @staticmethod
    def _resolver_local_existente(db: Session, current_user: User, item: SyncLocalItem):
        existente = None
        if item.id is not None:
            existente = LocalRepository.get_by_id(db, item.id)
            if existente is not None and existente.usuario_id != current_user.id:
                # Nunca deixa o item de outro usuário "vazar" para o resto da função — o
                # comportamento passa a ser idêntico ao de um id desconhecido (ver seção de
                # segurança: alterar um id não dá acesso a dado de outro usuário).
                existente = None
        if existente is None and item.uuid_cliente:
            existente = LocalRepository.get_by_uuid_cliente(db, item.uuid_cliente, current_user.id)
        return existente

    @staticmethod
    def _push_local(db: Session, current_user: User, item: SyncLocalItem, resultado: SyncPushResponse, uuid_para_local_id: dict) -> None:
        existente = SyncService._resolver_local_existente(db, current_user, item)

        if item.deletado:
            if existente is None:
                return
            if _as_utc(existente.atualizado_em) > _as_utc(item.atualizado_em):
                resultado.conflitos.append(SyncConflito(
                    entidade="local", uuid_cliente=item.uuid_cliente, id=existente.id,
                    motivo="O servidor tem uma versão mais recente deste local.",
                    servidor_atualizado_em=existente.atualizado_em,
                ))
                return
            try:
                deletar_local(local_id=existente.id, db=db, current_user=current_user)
            except HTTPException as exc:
                resultado.erros.append(SyncErro(entidade="local", uuid_cliente=item.uuid_cliente, detalhe=str(exc.detail), status_code=exc.status_code))
                return
            resultado.locais_deletados.append(SyncItemResultado(uuid_cliente=item.uuid_cliente, id=existente.id))
            return

        if existente is None:
            payload = LocalCreate(
                nome_local=item.nome_local,
                data_inicio=item.data_inicio,
                data_final=item.data_final,
                dia_pagamento=item.dia_pagamento,
                util_corrido=item.util_corrido,
                cor=item.cor,
                endereco_local=item.endereco_local,
                uuid_cliente=item.uuid_cliente,
            )
            try:
                resposta = criar_local(payload=payload, db=db, current_user=current_user)
            except HTTPException as exc:
                resultado.erros.append(SyncErro(entidade="local", uuid_cliente=item.uuid_cliente, detalhe=str(exc.detail), status_code=exc.status_code))
                return
            uuid_para_local_id[item.uuid_cliente] = resposta.data.id
            resultado.locais_criados.append(SyncItemResultado(uuid_cliente=item.uuid_cliente, id=resposta.data.id))
            return

        uuid_para_local_id[item.uuid_cliente] = existente.id
        if _as_utc(existente.atualizado_em) >= _as_utc(item.atualizado_em):
            resultado.conflitos.append(SyncConflito(
                entidade="local", uuid_cliente=item.uuid_cliente, id=existente.id,
                motivo="O servidor tem uma versão igual ou mais recente deste local.",
                servidor_atualizado_em=existente.atualizado_em,
            ))
            return

        payload = LocalUpdate(
            nome_local=item.nome_local,
            data_inicio=item.data_inicio,
            data_final=item.data_final,
            dia_pagamento=item.dia_pagamento,
            util_corrido=item.util_corrido,
            cor=item.cor,
            endereco_local=item.endereco_local,
        )
        try:
            atualizar_local(local_id=existente.id, payload=payload, db=db, current_user=current_user)
        except HTTPException as exc:
            resultado.erros.append(SyncErro(entidade="local", uuid_cliente=item.uuid_cliente, detalhe=str(exc.detail), status_code=exc.status_code))
            return
        resultado.locais_atualizados.append(SyncItemResultado(uuid_cliente=item.uuid_cliente, id=existente.id))

    @staticmethod
    def _push_plantao(db: Session, current_user: User, item: SyncPlantaoItem, resultado: SyncPushResponse, uuid_para_local_id: dict) -> None:
        existente = None
        if item.id is not None:
            existente = PlantaoRepository.get_by_id_and_user_incluindo_transferidos(db, item.id, current_user.id)
        if existente is None and item.uuid_cliente:
            existente = PlantaoRepository.get_by_uuid_cliente(db, item.uuid_cliente, current_user.id)

        if item.deletado:
            if existente is None:
                return
            if _as_utc(existente.atualizado_em) > _as_utc(item.atualizado_em):
                resultado.conflitos.append(SyncConflito(
                    entidade="plantao", uuid_cliente=item.uuid_cliente, id=existente.id,
                    motivo="O servidor tem uma versão mais recente deste plantão.",
                    servidor_atualizado_em=existente.atualizado_em,
                ))
                return
            try:
                deletar_plantao(plantao_id=existente.id, db=db, current_user=current_user)
            except HTTPException as exc:
                resultado.erros.append(SyncErro(entidade="plantao", uuid_cliente=item.uuid_cliente, detalhe=str(exc.detail), status_code=exc.status_code))
                return
            resultado.plantoes_deletados.append(SyncItemResultado(uuid_cliente=item.uuid_cliente, id=existente.id))
            return

        local_id = item.local_id
        if local_id is None and item.local_uuid_cliente is not None:
            local_id = uuid_para_local_id.get(item.local_uuid_cliente)
            if local_id is None:
                local_existente = LocalRepository.get_by_uuid_cliente(db, item.local_uuid_cliente, current_user.id)
                local_id = local_existente.id if local_existente else None
        if local_id is None:
            resultado.erros.append(SyncErro(
                entidade="plantao", uuid_cliente=item.uuid_cliente,
                detalhe="Local referenciado (local_id ou local_uuid_cliente) não encontrado.", status_code=404,
            ))
            return

        if existente is None:
            payload = PlantaoCreate(
                valor=item.valor,
                data_inicio=item.data_inicio,
                data_fim=item.data_fim,
                recorrencia=item.recorrencia,
                sobreaviso=item.sobreaviso,
                descricao=item.descricao,
                local_id=local_id,
                pagamento_plantao=item.pagamento_plantao,
                notificacao_ativa=item.notificacao_ativa,
                notificacao_antecedencia_minutos=item.notificacao_antecedencia_minutos,
                uuid_cliente=item.uuid_cliente,
            )
            try:
                resposta = criar_plantao(payload=payload, db=db, current_user=current_user)
            except HTTPException as exc:
                resultado.erros.append(SyncErro(entidade="plantao", uuid_cliente=item.uuid_cliente, detalhe=str(exc.detail), status_code=exc.status_code))
                return
            resultado.plantoes_criados.append(SyncItemResultado(uuid_cliente=item.uuid_cliente, id=resposta.data.id))
            return

        if _as_utc(existente.atualizado_em) >= _as_utc(item.atualizado_em):
            resultado.conflitos.append(SyncConflito(
                entidade="plantao", uuid_cliente=item.uuid_cliente, id=existente.id,
                motivo="O servidor tem uma versão igual ou mais recente deste plantão.",
                servidor_atualizado_em=existente.atualizado_em,
            ))
            return

        payload = PlantaoUpdate(
            valor=item.valor,
            data_inicio=item.data_inicio,
            data_fim=item.data_fim,
            recorrencia=item.recorrencia,
            sobreaviso=item.sobreaviso,
            descricao=item.descricao,
            local_id=local_id,
            notificacao_ativa=item.notificacao_ativa,
            notificacao_antecedencia_minutos=item.notificacao_antecedencia_minutos,
            pagamento_plantao=item.pagamento_plantao,
            aplicar_em="somente_este",
        )
        try:
            atualizar_plantao(plantao_id=existente.id, payload=payload, db=db, current_user=current_user)
        except HTTPException as exc:
            resultado.erros.append(SyncErro(entidade="plantao", uuid_cliente=item.uuid_cliente, detalhe=str(exc.detail), status_code=exc.status_code))
            return
        resultado.plantoes_atualizados.append(SyncItemResultado(uuid_cliente=item.uuid_cliente, id=existente.id))

    @staticmethod
    def pull(db: Session, current_user: User, desde: datetime | None) -> SyncPullResponse:
        # Filtragem em Python (não em SQL) de propósito: compara datetimes já normalizados
        # para UTC via _as_utc, evitando divergência entre o dialect de produção (Postgres,
        # aware) e o de teste/alguns ambientes de desenvolvimento (SQLite, naive) — ver
        # _as_utc para detalhes. Os volumes por usuário aqui são naturalmente pequenos.
        desde = _as_utc(desde)

        locais = LocalRepository.list_by_user(db, current_user.id)
        locais_alterados = [l for l in locais if desde is None or _as_utc(l.atualizado_em) > desde]

        plantoes = PlantaoRepository.list_by_user(db, current_user.id)
        plantoes_alterados = [p for p in plantoes if desde is None or _as_utc(p.atualizado_em) > desde]

        amizades = AmizadeRepository.list_by_usuario(db, current_user.id)
        amizades_alteradas = [
            a for a in amizades
            if desde is None or _as_utc(a.atualizado_em) > desde or _as_utc(a.criado_em) > desde
        ]

        configs = AmigoConfiguracaoRepository.list_by_usuario(db, current_user.id)
        configs_alteradas = [c for c in configs if desde is None or _as_utc(c.atualizado_em) > desde]

        solicitacoes = SolicitacaoTrocaRepository.list_by_usuario(db, current_user.id)
        solicitacoes_alteradas = [
            s for s in solicitacoes
            if desde is None or _as_utc(s.criado_em) > desde
            or (s.respondido_em is not None and _as_utc(s.respondido_em) > desde)
        ]

        trocas = TrocaRepository.list_by_usuario(db, current_user.id)
        trocas_alteradas = [
            t for t in trocas if desde is None or (t.criado_em is not None and _as_utc(t.criado_em) > desde)
        ]

        exclusoes = [
            e for e in SyncRepository.list_exclusoes(db, current_user.id)
            if desde is None or _as_utc(e.deletado_em) > desde
        ]

        itens_amizade = []
        for amizade in amizades_alteradas:
            if amizade.solicitante_usuario_id == current_user.id:
                papel, outro = "solicitante", amizade.destinatario
            else:
                papel, outro = "destinatario", amizade.solicitante
            config = next((c for c in configs if c.amigo_usuario_id == outro.id), None)
            itens_amizade.append(AmizadeListItem(
                id=amizade.id,
                status=amizade.status,
                papel=papel,
                amigo=AmizadeAmigoResumo.model_validate(outro),
                configuracao_id=config.id if config else None,
                modo_visibilidade=config.modo_visibilidade if config else None,
                mostrar_valores=config.mostrar_valores if config else None,
                cor=config.cor if config else None,
                locais_permitidos=[loc.local_id for loc in config.locais_permitidos] if config else [],
                criado_em=amizade.criado_em,
            ))

        itens_solicitacao = [
            SolicitacaoTrocaListItem(
                id=s.id,
                tipo=s.tipo,
                status=s.status,
                papel="remetente" if s.remetente_usuario_id == current_user.id else "destinatario",
                remetente=PessoaTrocaResumo(id=s.remetente.id, nome=s.remetente.nome),
                destinatario=PessoaTrocaResumo(id=s.destinatario.id, nome=s.destinatario.nome),
                plantao_transferido=_plantao_troca_resumo(s.plantao_transferido) if s.plantao_transferido else None,
                plantao_recebido=_plantao_recebido_troca_resumo(s),
                criado_em=s.criado_em,
            )
            for s in solicitacoes_alteradas
        ]

        return SyncPullResponse(
            servidor_timestamp=datetime.now(UTC),
            locais=[LocalResponse.model_validate(l) for l in locais_alterados],
            plantoes=[_plantao_com_troca(db, p) for p in plantoes_alterados],
            amizades=itens_amizade,
            amigo_configuracoes=[_serializar_amigo_configuracao(c) for c in configs_alteradas],
            solicitacoes_troca=itens_solicitacao,
            trocas=[TrocaResponse.model_validate(t) for t in trocas_alteradas],
            exclusoes=[
                SyncExclusaoItem(entidade=e.entidade, id=e.entidade_id, uuid_cliente=e.uuid_cliente, deletado_em=e.deletado_em)
                for e in exclusoes
            ],
        )
