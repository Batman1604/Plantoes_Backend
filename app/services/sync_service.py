import logging
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from pydantic import BaseModel, ValidationError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import User
from app.repositories.plantao_repository import (
    AmigoConfiguracaoRepository,
    AmizadeRepository,
    BonificacaoRepository,
    LocalRepository,
    PlantaoRepository,
    SolicitacaoTrocaRepository,
    SyncRepository,
    TrocaRepository,
)
from app.schemas.plantao_schema import (
    AmizadeAmigoResumo,
    AmizadeListItem,
    BonificacaoCreate,
    BonificacaoResponse,
    BonificacaoUpdate,
    LocalCreate,
    LocalResponse,
    LocalUpdate,
    PessoaTrocaResumo,
    PlantaoCreate,
    PlantaoRecebidoTrocaCreate,
    PlantaoUpdate,
    SolicitacaoTrocaListItem,
    SyncBonificacaoItem,
    SyncConflito,
    SyncErro,
    SyncExclusaoItem,
    SyncItemResultado,
    SyncLocalItem,
    SyncPlantaoItem,
    SyncPullResponse,
    SyncPushResponse,
    SyncTrocaItem,
    TrocaOperacaoCreate,
    TrocaResponse,
)

from app.services.bonificacao_service import BonificacaoService
from app.services.erros import ErroDeDominio
from app.services.troca_service import TrocaService
from app.services.local_service import LocalService

# A-13 (4/4): todas as entidades passam por serviços próprios. Este módulo não
# importa mais nada de app.routes — antes chamava os handlers de plantão como
# funções Python, e um default `Query(...)` vazado fez a exclusão de UM plantão
# apagar a série inteira (AUD-001).
from app.services.plantao_service import PlantaoService
from app.services.serializacao import (
    _plantoes_com_troca,
    _alvos_com_valor_oculto,
    _plantao_recebido_troca_resumo,
    _plantao_troca_resumo,
    _serializar_amigo_configuracao,
)

logger = logging.getLogger(__name__)

# AUD-023: quanto o `since` do pull é recuado para cobrir commits que
# terminaram depois do cursor anterior.
_MARGEM_DO_CURSOR = timedelta(seconds=5)


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


def _detalhe_de_validacao(exc: ValidationError) -> str:
    """Texto legível de uma ValidationError do pydantic, campo a campo."""
    partes = []
    for erro in exc.errors():
        campo = ".".join(str(parte) for parte in erro.get("loc", ()))
        partes.append(f"{campo}: {erro['msg']}" if campo else erro["msg"])
    return "; ".join(partes)


# AUD-007: falhas que viram erro DO ITEM (SyncErro), sem derrubar o lote.
# ValidationError cobre o *Create/*Update montado a partir de um item que passou
# no Sync*Item mas viola uma regra da criação (duração > 48 h, fim antes do
# início). SQLAlchemyError cobre a falha de banco ao gravar o item; o lote
# segue com rollback só deste item e a causa vai para o log.
_FALHAS_DO_ITEM = (ErroDeDominio, HTTPException, ValidationError, SQLAlchemyError)


def _erro_de_excecao(exc: Exception) -> tuple[str, int]:
    """Extrai (mensagem, status_code) de uma falha ao aplicar um item do lote de push.
    IntegrityError não tem .detail/.status_code (não é um HTTPException) — cobre
    principalmente uma corrida de uuid_cliente duplicado entre dois pushes quase
    simultâneos do mesmo dispositivo (ex.: retry após timeout de rede colidindo com a
    tentativa original ainda em voo). Sempre devolve algo serializável em SyncErro, para
    que ESTE item vire um erro isolado (com retry pelo próprio SyncEngine no cliente) em
    vez de derrubar o lote inteiro com um 500 — ver SyncRoutes.push.

    Chamada sempre de dentro do `except`, então logger.exception leva o traceback."""
    if isinstance(exc, ErroDeDominio):
        return exc.mensagem, exc.status_http
    if isinstance(exc, HTTPException):
        return str(exc.detail), exc.status_code
    if isinstance(exc, ValidationError):
        return _detalhe_de_validacao(exc), 422
    if isinstance(exc, IntegrityError):
        return "Conflito ao sincronizar este item (tente novamente).", 409
    logger.exception("Falha de banco ao aplicar item do push")
    return "Erro ao gravar este item no servidor (tente novamente).", 500


def _validar_itens(modelo: type[BaseModel], entidade: str, brutos, resultado: SyncPushResponse) -> list:
    """AUD-007 (contrato K3): valida cada item do push separadamente. O item
    fora do schema vira SyncErro 422 com o motivo; os outros seguem. Antes a
    validação era do corpo inteiro e um item ruim dava 422 no lote todo."""
    validos = []
    for bruto in brutos or []:
        if isinstance(bruto, modelo):
            validos.append(bruto)
            continue
        try:
            validos.append(modelo.model_validate(bruto))
        except ValidationError as exc:
            uuid_cliente = bruto.get("uuid_cliente") if isinstance(bruto, dict) else None
            resultado.erros.append(SyncErro(
                entidade=entidade, uuid_cliente=str(uuid_cliente or ""),
                detalhe=_detalhe_de_validacao(exc), status_code=422,
            ))
    return validos


class SyncService:
    """Sincronização offline: POST /api/sync/push (envia mudanças feitas no dispositivo) e
    GET /api/sync/pull (busca mudanças feitas na nuvem). Ver docstring dos schemas Sync* para
    o contrato completo e a regra de conflito (last-write-wins por atualizado_em)."""

    @staticmethod
    def push(
        db: Session,
        current_user: User,
        locais: list[SyncLocalItem | dict],
        plantoes: list[SyncPlantaoItem | dict],
        trocas: list[SyncTrocaItem | dict] | None = None,
        bonificacoes: list[SyncBonificacaoItem | dict] | None = None,
    ) -> SyncPushResponse:
        resultado = SyncPushResponse(servidor_timestamp=datetime.now(UTC))
        uuid_para_local_id: dict[str, int] = {}
        uuid_para_plantao_id: dict[str, int] = {}

        for item in _validar_itens(SyncLocalItem, "local", locais, resultado):
            SyncService._push_local(db, current_user, item, resultado, uuid_para_local_id)

        for item in _validar_itens(SyncPlantaoItem, "plantao", plantoes, resultado):
            SyncService._push_plantao(db, current_user, item, resultado, uuid_para_local_id, uuid_para_plantao_id)

        # Trocas/bonificações são processadas por último de propósito: uma troca ou
        # bonificação pode referenciar (via uuid_cliente) um plantão criado neste mesmo lote.
        for item in _validar_itens(SyncTrocaItem, "troca", trocas, resultado):
            SyncService._push_troca(db, current_user, item, resultado, uuid_para_local_id, uuid_para_plantao_id)

        for item in _validar_itens(SyncBonificacaoItem, "bonificacao", bonificacoes, resultado):
            SyncService._push_bonificacao(db, current_user, item, resultado, uuid_para_plantao_id)

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
                # AUD-014 (contrato K2): excluir o que não existe (nunca subiu,
                # já excluído aqui ou em outro aparelho) é idempotente. Sem
                # resposta nenhuma, o cliente reenviava a entrada para sempre.
                resultado.locais_deletados.append(SyncItemResultado(uuid_cliente=item.uuid_cliente, id=item.id))
                return
            if _as_utc(existente.atualizado_em) > _as_utc(item.atualizado_em):
                resultado.conflitos.append(SyncConflito(
                    entidade="local", uuid_cliente=item.uuid_cliente, id=existente.id,
                    motivo="O servidor tem uma versão mais recente deste local.",
                    servidor_atualizado_em=existente.atualizado_em,
                ))
                return
            try:
                LocalService.deletar(db, current_user, existente.id)
            except _FALHAS_DO_ITEM as exc:
                db.rollback()
                detalhe, status_code = _erro_de_excecao(exc)
                resultado.erros.append(SyncErro(entidade="local", uuid_cliente=item.uuid_cliente, detalhe=detalhe, status_code=status_code))
                return
            resultado.locais_deletados.append(SyncItemResultado(uuid_cliente=item.uuid_cliente, id=existente.id))
            return

        if existente is None:
            if SyncRepository.foi_excluido(db, current_user.id, "local", item.uuid_cliente):
                # Este uuid_cliente já foi excluído em outro dispositivo — não recria
                # (ver docstring de _erro_de_excecao/foi_excluido). O cliente reconhece
                # status_code=410 e converge para o estado "excluído" localmente também,
                # sem exigir ação manual do usuário (ver SyncEngine._applyPushResponse).
                resultado.erros.append(SyncErro(
                    entidade="local", uuid_cliente=item.uuid_cliente,
                    detalhe="Este local foi excluído permanentemente em outro dispositivo.",
                    status_code=410,
                ))
                return
            try:
                # AUD-007: montar o *Create dentro do try — os validadores dele
                # (ex.: data_inicio > data_final) viram erro deste item.
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
                criado = LocalService.criar(db, current_user, payload)
            except _FALHAS_DO_ITEM as exc:
                db.rollback()
                detalhe, status_code = _erro_de_excecao(exc)
                resultado.erros.append(SyncErro(entidade="local", uuid_cliente=item.uuid_cliente, detalhe=detalhe, status_code=status_code))
                return
            uuid_para_local_id[item.uuid_cliente] = criado.id
            resultado.locais_criados.append(SyncItemResultado(uuid_cliente=item.uuid_cliente, id=criado.id))
            return

        uuid_para_local_id[item.uuid_cliente] = existente.id
        if _as_utc(existente.atualizado_em) >= _as_utc(item.atualizado_em):
            resultado.conflitos.append(SyncConflito(
                entidade="local", uuid_cliente=item.uuid_cliente, id=existente.id,
                motivo="O servidor tem uma versão igual ou mais recente deste local.",
                servidor_atualizado_em=existente.atualizado_em,
            ))
            return

        try:
            payload = LocalUpdate(
                nome_local=item.nome_local,
                data_inicio=item.data_inicio,
                data_final=item.data_final,
                dia_pagamento=item.dia_pagamento,
                util_corrido=item.util_corrido,
                cor=item.cor,
                endereco_local=item.endereco_local,
            )
            LocalService.atualizar(db, current_user, existente.id, payload)
        except _FALHAS_DO_ITEM as exc:
            db.rollback()
            detalhe, status_code = _erro_de_excecao(exc)
            resultado.erros.append(SyncErro(entidade="local", uuid_cliente=item.uuid_cliente, detalhe=detalhe, status_code=status_code))
            return
        resultado.locais_atualizados.append(SyncItemResultado(uuid_cliente=item.uuid_cliente, id=existente.id))

    @staticmethod
    def _push_plantao(
        db: Session,
        current_user: User,
        item: SyncPlantaoItem,
        resultado: SyncPushResponse,
        uuid_para_local_id: dict,
        uuid_para_plantao_id: dict,
    ) -> None:
        existente = None
        if item.id is not None:
            existente = PlantaoRepository.get_by_id_and_user_incluindo_transferidos(db, item.id, current_user.id)
        if existente is None and item.uuid_cliente:
            existente = PlantaoRepository.get_by_uuid_cliente(db, item.uuid_cliente, current_user.id)
        if existente is not None and item.uuid_cliente:
            uuid_para_plantao_id[item.uuid_cliente] = existente.id

        if item.deletado:
            if existente is None:
                # AUD-014 (contrato K2): excluir o que não existe (nunca subiu,
                # já excluído aqui ou em outro aparelho) é idempotente. Sem
                # resposta nenhuma, o cliente reenviava a entrada para sempre.
                resultado.plantoes_deletados.append(SyncItemResultado(uuid_cliente=item.uuid_cliente, id=item.id))
                return
            if _as_utc(existente.atualizado_em) > _as_utc(item.atualizado_em):
                resultado.conflitos.append(SyncConflito(
                    entidade="plantao", uuid_cliente=item.uuid_cliente, id=existente.id,
                    motivo="O servidor tem uma versão mais recente deste plantão.",
                    servidor_atualizado_em=existente.atualizado_em,
                ))
                return
            try:
                # AUD-001: o sync exclui sempre UM plantão — o item que o
                # aparelho mandou. `aplicar_em` é obrigatório no serviço.
                PlantaoService.deletar(db, current_user, existente.id, aplicar_em="somente_este")
            except _FALHAS_DO_ITEM as exc:
                db.rollback()
                detalhe, status_code = _erro_de_excecao(exc)
                resultado.erros.append(SyncErro(entidade="plantao", uuid_cliente=item.uuid_cliente, detalhe=detalhe, status_code=status_code))
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
            if SyncRepository.foi_excluido(db, current_user.id, "plantao", item.uuid_cliente):
                resultado.erros.append(SyncErro(
                    entidade="plantao", uuid_cliente=item.uuid_cliente,
                    detalhe="Este plantão foi excluído permanentemente em outro dispositivo.",
                    status_code=410,
                ))
                return
            try:
                # AUD-007: PlantaoCreate recusa duração > 48 h; montado fora do
                # try, a ValidationError virava 500 do lote inteiro depois de os
                # itens anteriores já estarem gravados.
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
                    serie_id=item.serie_id,  # K8
                )
                criado = PlantaoService.criar(db, current_user, payload)
            except _FALHAS_DO_ITEM as exc:
                db.rollback()
                detalhe, status_code = _erro_de_excecao(exc)
                resultado.erros.append(SyncErro(entidade="plantao", uuid_cliente=item.uuid_cliente, detalhe=detalhe, status_code=status_code))
                return
            uuid_para_plantao_id[item.uuid_cliente] = criado.id
            resultado.plantoes_criados.append(SyncItemResultado(uuid_cliente=item.uuid_cliente, id=criado.id))
            return

        if _as_utc(existente.atualizado_em) >= _as_utc(item.atualizado_em):
            resultado.conflitos.append(SyncConflito(
                entidade="plantao", uuid_cliente=item.uuid_cliente, id=existente.id,
                motivo="O servidor tem uma versão igual ou mais recente deste plantão.",
                servidor_atualizado_em=existente.atualizado_em,
            ))
            return

        # K8: cliente antigo não manda serie_id; só quem manda pode alterá-lo
        # (sem isto, a edição vinda de um aparelho desatualizado apagaria a série).
        serie = {"serie_id": item.serie_id} if "serie_id" in item.model_fields_set else {}
        try:
            payload = PlantaoUpdate(
                **serie,
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
            PlantaoService.atualizar(db, current_user, existente.id, payload, aplicar_em="somente_este")
        except _FALHAS_DO_ITEM as exc:
            db.rollback()
            detalhe, status_code = _erro_de_excecao(exc)
            resultado.erros.append(SyncErro(entidade="plantao", uuid_cliente=item.uuid_cliente, detalhe=detalhe, status_code=status_code))
            return
        resultado.plantoes_atualizados.append(SyncItemResultado(uuid_cliente=item.uuid_cliente, id=existente.id))

    @staticmethod
    def _resolver_troca_existente(db: Session, current_user: User, item: SyncTrocaItem):
        existente = None
        if item.id is not None:
            candidata = TrocaRepository.get_by_id(db, item.id)
            if candidata is not None and candidata.usuario_id == current_user.id:
                existente = candidata
        if existente is None and item.uuid_cliente:
            existente = TrocaRepository.get_by_uuid_cliente(db, item.uuid_cliente, current_user.id)
        return existente

    @staticmethod
    def _push_troca(
        db: Session,
        current_user: User,
        item: SyncTrocaItem,
        resultado: SyncPushResponse,
        uuid_para_local_id: dict,
        uuid_para_plantao_id: dict,
    ) -> None:
        existente = SyncService._resolver_troca_existente(db, current_user, item)

        if item.deletado:
            if existente is None:
                # AUD-014 (contrato K2): excluir o que não existe (nunca subiu,
                # já excluído aqui ou em outro aparelho) é idempotente. Sem
                # resposta nenhuma, o cliente reenviava a entrada para sempre.
                resultado.trocas_deletadas.append(SyncItemResultado(uuid_cliente=item.uuid_cliente, id=item.id))
                return
            if _as_utc(existente.atualizado_em) > _as_utc(item.atualizado_em):
                resultado.conflitos.append(SyncConflito(
                    entidade="troca", uuid_cliente=item.uuid_cliente, id=existente.id,
                    motivo="O servidor tem uma versão mais recente desta troca.",
                    servidor_atualizado_em=existente.atualizado_em,
                ))
                return
            try:
                TrocaService.deletar(db, current_user, existente.id)
            except _FALHAS_DO_ITEM as exc:
                db.rollback()
                detalhe, status_code = _erro_de_excecao(exc)
                resultado.erros.append(SyncErro(entidade="troca", uuid_cliente=item.uuid_cliente, detalhe=detalhe, status_code=status_code))
                return
            resultado.trocas_deletadas.append(SyncItemResultado(uuid_cliente=item.uuid_cliente, id=existente.id))
            return

        if existente is not None:
            # Não existe rota de edição para trocas (ver docstring de SyncTrocaItem): um
            # reenvio do mesmo uuid_cliente é o caso idempotente (retry após timeout de rede,
            # por exemplo) — devolve o estado já persistido em vez de reexecutar o efeito
            # colateral de transferência, que marcaria o plantão como transferido de novo e
            # poderia criar um segundo "recebido" duplicado.
            resultado.trocas_criadas.append(SyncItemResultado(uuid_cliente=item.uuid_cliente, id=existente.id))
            return

        if SyncRepository.foi_excluido(db, current_user.id, "troca", item.uuid_cliente):
            resultado.erros.append(SyncErro(
                entidade="troca", uuid_cliente=item.uuid_cliente,
                detalhe="Esta troca foi excluída permanentemente em outro dispositivo.",
                status_code=410,
            ))
            return

        plantao_transferido_id = item.plantao_transferido_id
        if plantao_transferido_id is None and item.plantao_transferido_uuid_cliente is not None:
            plantao_transferido_id = uuid_para_plantao_id.get(item.plantao_transferido_uuid_cliente)
            if plantao_transferido_id is None:
                plantao_existente = PlantaoRepository.get_by_uuid_cliente(db, item.plantao_transferido_uuid_cliente, current_user.id)
                plantao_transferido_id = plantao_existente.id if plantao_existente else None
        if plantao_transferido_id is None:
            resultado.erros.append(SyncErro(
                entidade="troca", uuid_cliente=item.uuid_cliente,
                detalhe="Plantão transferido (plantao_transferido_id ou plantao_transferido_uuid_cliente) não encontrado.",
                status_code=404,
            ))
            return

        recebido_local_id = None
        if item.plantao_recebido is not None:
            recebido_item = item.plantao_recebido
            recebido_local_id = recebido_item.local_id
            if recebido_local_id is None and recebido_item.local_uuid_cliente is not None:
                recebido_local_id = uuid_para_local_id.get(recebido_item.local_uuid_cliente)
                if recebido_local_id is None:
                    local_existente = LocalRepository.get_by_uuid_cliente(db, recebido_item.local_uuid_cliente, current_user.id)
                    recebido_local_id = local_existente.id if local_existente else None
            if recebido_local_id is None:
                resultado.erros.append(SyncErro(
                    entidade="troca", uuid_cliente=item.uuid_cliente,
                    detalhe="Local do plantão recebido (local_id ou local_uuid_cliente) não encontrado.", status_code=404,
                ))
                return

        try:
            # AUD-007: só a montagem do payload entrou no try (a regra da troca
            # não mudou) — PlantaoRecebidoTrocaCreate recusa fim antes do início.
            plantao_recebido_payload = None
            if item.plantao_recebido is not None:
                recebido_item = item.plantao_recebido
                plantao_recebido_payload = PlantaoRecebidoTrocaCreate(
                    local_id=recebido_local_id,
                    data_inicio=recebido_item.data_inicio,
                    horario_inicio=recebido_item.horario_inicio,
                    data_fim=recebido_item.data_fim,
                    horario_fim=recebido_item.horario_fim,
                    valor=recebido_item.valor,
                    sobreaviso=recebido_item.sobreaviso,
                    descricao=recebido_item.descricao,
                    recebido_de=recebido_item.recebido_de,
                    uuid_cliente=recebido_item.uuid_cliente,
                )
            payload = TrocaOperacaoCreate(
                plantao_transferido_id=plantao_transferido_id,
                transferido_para=item.transferido_para,
                plantao_recebido=plantao_recebido_payload,
                uuid_cliente=item.uuid_cliente,
            )
            registrada = TrocaService.registrar(db, current_user, payload)
        except _FALHAS_DO_ITEM as exc:
            db.rollback()
            detalhe, status_code = _erro_de_excecao(exc)
            resultado.erros.append(SyncErro(entidade="troca", uuid_cliente=item.uuid_cliente, detalhe=detalhe, status_code=status_code))
            return
        if item.plantao_recebido is not None and item.plantao_recebido.uuid_cliente:
            uuid_para_plantao_id[item.plantao_recebido.uuid_cliente] = registrada.plantao_recebido.id
        resultado.trocas_criadas.append(SyncItemResultado(uuid_cliente=item.uuid_cliente, id=registrada.troca.id))

    @staticmethod
    def _resolver_bonificacao_existente(db: Session, current_user: User, item: SyncBonificacaoItem):
        existente = None
        if item.id is not None:
            candidata = BonificacaoRepository.get_by_id(db, item.id)
            if candidata is not None:
                plantao_dono = PlantaoRepository.get_by_id_and_user(db, candidata.plantao_id, current_user.id)
                if plantao_dono is not None:
                    existente = candidata
        if existente is None and item.uuid_cliente:
            existente = BonificacaoRepository.get_by_uuid_cliente(db, item.uuid_cliente, current_user.id)
        return existente

    @staticmethod
    def _push_bonificacao(
        db: Session,
        current_user: User,
        item: SyncBonificacaoItem,
        resultado: SyncPushResponse,
        uuid_para_plantao_id: dict,
    ) -> None:
        existente = SyncService._resolver_bonificacao_existente(db, current_user, item)

        if item.deletado:
            if existente is None:
                # AUD-014 (contrato K2): excluir o que não existe (nunca subiu,
                # já excluído aqui ou em outro aparelho) é idempotente. Sem
                # resposta nenhuma, o cliente reenviava a entrada para sempre.
                resultado.bonificacoes_deletadas.append(SyncItemResultado(uuid_cliente=item.uuid_cliente, id=item.id))
                return
            if _as_utc(existente.atualizado_em) > _as_utc(item.atualizado_em):
                resultado.conflitos.append(SyncConflito(
                    entidade="bonificacao", uuid_cliente=item.uuid_cliente, id=existente.id,
                    motivo="O servidor tem uma versão mais recente desta bonificação.",
                    servidor_atualizado_em=existente.atualizado_em,
                ))
                return
            try:
                BonificacaoService.deletar(db, current_user, existente.id)
            except _FALHAS_DO_ITEM as exc:
                db.rollback()
                detalhe, status_code = _erro_de_excecao(exc)
                resultado.erros.append(SyncErro(entidade="bonificacao", uuid_cliente=item.uuid_cliente, detalhe=detalhe, status_code=status_code))
                return
            resultado.bonificacoes_deletadas.append(SyncItemResultado(uuid_cliente=item.uuid_cliente, id=existente.id))
            return

        plantao_id = item.plantao_id
        if plantao_id is None and item.plantao_uuid_cliente is not None:
            plantao_id = uuid_para_plantao_id.get(item.plantao_uuid_cliente)
            if plantao_id is None:
                plantao_existente = PlantaoRepository.get_by_uuid_cliente(db, item.plantao_uuid_cliente, current_user.id)
                plantao_id = plantao_existente.id if plantao_existente else None
        if plantao_id is None:
            resultado.erros.append(SyncErro(
                entidade="bonificacao", uuid_cliente=item.uuid_cliente,
                detalhe="Plantão referenciado (plantao_id ou plantao_uuid_cliente) não encontrado.", status_code=404,
            ))
            return

        if existente is None:
            if SyncRepository.foi_excluido(db, current_user.id, "bonificacao", item.uuid_cliente):
                resultado.erros.append(SyncErro(
                    entidade="bonificacao", uuid_cliente=item.uuid_cliente,
                    detalhe="Esta bonificação foi excluída permanentemente em outro dispositivo.",
                    status_code=410,
                ))
                return
            try:
                payload = BonificacaoCreate(
                    plantao_id=plantao_id,
                    local_id=item.local_id,
                    valor=item.valor,
                    descricao=item.descricao,
                    uuid_cliente=item.uuid_cliente,
                )
                criada = BonificacaoService.criar(db, current_user, payload)
            except _FALHAS_DO_ITEM as exc:
                db.rollback()
                detalhe, status_code = _erro_de_excecao(exc)
                resultado.erros.append(SyncErro(entidade="bonificacao", uuid_cliente=item.uuid_cliente, detalhe=detalhe, status_code=status_code))
                return
            resultado.bonificacoes_criadas.append(SyncItemResultado(uuid_cliente=item.uuid_cliente, id=criada.id))
            return

        if _as_utc(existente.atualizado_em) >= _as_utc(item.atualizado_em):
            resultado.conflitos.append(SyncConflito(
                entidade="bonificacao", uuid_cliente=item.uuid_cliente, id=existente.id,
                motivo="O servidor tem uma versão igual ou mais recente desta bonificação.",
                servidor_atualizado_em=existente.atualizado_em,
            ))
            return

        try:
            payload = BonificacaoUpdate(valor=item.valor, descricao=item.descricao)
            BonificacaoService.atualizar(db, current_user, existente.id, payload)
        except _FALHAS_DO_ITEM as exc:
            db.rollback()
            detalhe, status_code = _erro_de_excecao(exc)
            resultado.erros.append(SyncErro(entidade="bonificacao", uuid_cliente=item.uuid_cliente, detalhe=detalhe, status_code=status_code))
            return
        resultado.bonificacoes_atualizadas.append(SyncItemResultado(uuid_cliente=item.uuid_cliente, id=existente.id))

    @staticmethod
    def pull(db: Session, current_user: User, desde: datetime | None) -> SyncPullResponse:
        # O filtro por `desde` acontece em SQL, não mais em Python. A versão
        # anterior carregava TODAS as linhas do usuário em toda chamada e
        # descartava em memória — custo proporcional ao histórico inteiro mesmo
        # quando nada tinha mudado, e o cliente chama isto a cada poucos minutos
        # (ver auditoria A-04). A divergência de fuso entre Postgres e SQLite,
        # que era o motivo original de filtrar em Python, é resolvida por
        # instante_para_o_banco, no repositório.
        #
        # AUD-023 (contrato K6): o cursor devolvido é tirado ANTES das
        # consultas, e o `since` recebido é recuado em _MARGEM_DO_CURSOR. Antes
        # o servidor_timestamp era tirado no fim: uma escrita concorrente
        # (outro aparelho, um amigo aceitando troca) com atualizado_em
        # carimbado no flush e commitada depois destas consultas ficava com
        # atualizado_em < cursor e nunca mais voltava no incremental. A
        # margem reentrega os últimos segundos; o cliente aplica de forma
        # idempotente.
        servidor_timestamp = datetime.now(UTC)
        desde = _as_utc(desde)
        if desde is not None:
            desde = desde - _MARGEM_DO_CURSOR

        locais_alterados = LocalRepository.list_by_user(db, current_user.id, desde=desde)
        plantoes_alterados = PlantaoRepository.list_by_user(db, current_user.id, desde=desde)
        trocas_alteradas = TrocaRepository.list_by_usuario(db, current_user.id, desde=desde)
        bonificacoes_alteradas = BonificacaoRepository.list_by_usuario(db, current_user.id, desde=desde)
        exclusoes = SyncRepository.list_exclusoes(db, current_user.id, desde=desde)

        # Estas três seguem filtradas em Python porque a condição envolve DUAS
        # colunas (criado_em/atualizado_em; criado_em/respondido_em), e são
        # listas naturalmente curtas: amizades e configurações por amigo não
        # crescem com o uso do app, ao contrário de plantões e tombstones.
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
                amigo=AmizadeAmigoResumo.da_amizade(outro, amizade.status),
                configuracao_id=config.id if config else None,
                modo_visibilidade=config.modo_visibilidade if config else None,
                mostrar_valores=config.mostrar_valores if config else None,
                cor=config.cor if config else None,
                locais_permitidos=[loc.local_id for loc in config.locais_permitidos] if config else [],
                criado_em=amizade.criado_em,
            ))

        ocultos = _alvos_com_valor_oculto(db, current_user.id, solicitacoes_alteradas)
        itens_solicitacao = [
            SolicitacaoTrocaListItem(
                id=s.id,
                tipo=s.tipo,
                status=s.status,
                papel="remetente" if s.remetente_usuario_id == current_user.id else "destinatario",
                remetente=PessoaTrocaResumo(id=s.remetente.id, nome=s.remetente.nome),
                destinatario=PessoaTrocaResumo(id=s.destinatario.id, nome=s.destinatario.nome),
                plantao_transferido=_plantao_troca_resumo(s.plantao_transferido) if s.plantao_transferido else None,
                plantao_recebido=_plantao_recebido_troca_resumo(s, ocultar_valor=s.id in ocultos),
                criado_em=s.criado_em,
            )
            for s in solicitacoes_alteradas
        ]

        return SyncPullResponse(
            servidor_timestamp=servidor_timestamp,
            locais=[LocalResponse.model_validate(l) for l in locais_alterados],
            plantoes=_plantoes_com_troca(db, plantoes_alterados),
            amizades=itens_amizade,
            amigo_configuracoes=[_serializar_amigo_configuracao(c) for c in configs_alteradas],
            solicitacoes_troca=itens_solicitacao,
            trocas=[TrocaResponse.model_validate(t) for t in trocas_alteradas],
            bonificacoes=[BonificacaoResponse.model_validate(b) for b in bonificacoes_alteradas],
            exclusoes=[
                SyncExclusaoItem(entidade=e.entidade, id=e.entidade_id, uuid_cliente=e.uuid_cliente, deletado_em=e.deletado_em)
                for e in exclusoes
            ],
        )
