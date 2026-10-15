"""Regra de negócio de plantões: criar, atualizar e excluir.

Extraído dos handlers de rota pelo mesmo motivo de LocalService,
BonificacaoService e TrocaService (auditoria A-13, 4/4). O SyncService chamava
`criar_plantao`, `atualizar_plantao` e `deletar_plantao` de
app.routes.plantao_routes como funções Python. Num handler, um parâmetro como
`aplicar_em: ... = Query("somente_este")` só vira a string "somente_este"
quando o FastAPI resolve a requisição; chamado direto, o valor é o próprio
objeto `Query`, diferente da string. Foi assim que excluir UM plantão pelo
sync passou a apagar a série inteira e os avulsos do mesmo local e horário
(AUD-001).

Aqui `aplicar_em` é obrigatório e sem default, e um valor fora dos dois
aceitos falha alto. Os métodos levantam erros de domínio (app.services.erros);
quem traduz para HTTP é a rota, e para SyncErro é o SyncService.
"""

import logging
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import Plantao, User
from app.repositories.plantao_repository import FinanceiroRepository, LocalRepository, PlantaoRepository
from app.schemas.plantao_schema import PlantaoCreate, PlantaoCreateWithUsuario, PlantaoUpdate
from app.services.erros import Conflito, NaoEncontrado, RequisicaoInvalida, SemPermissao
from app.services.pagamento_service import PagamentoService, ajustar_fim_intervalo
from app.services.plano_service import PlanoService

logger = logging.getLogger(__name__)

APLICAR_EM_VALIDOS = ("somente_este", "este_e_futuros")
_DURACAO_MAXIMA_HORAS = 48


def _exigir_aplicar_em(aplicar_em) -> None:
    # AUD-001: qualquer coisa diferente das duas strings (inclusive um
    # `Query(...)` vazado de um handler) é erro de programação, não "este e
    # futuros". Antes, o `else` tratava o desconhecido como o caso mais
    # destrutivo.
    if aplicar_em not in APLICAR_EM_VALIDOS:
        raise ValueError(f"aplicar_em inválido: {aplicar_em!r}")


class PlantaoService:
    @staticmethod
    def criar(db: Session, usuario: User, payload: PlantaoCreate) -> Plantao:
        PlanoService.limitar_plano_free(db, usuario)

        local = LocalRepository.get_by_id(db, payload.local_id)
        if local is None:
            raise NaoEncontrado("Local não encontrado")
        if local.usuario_id != usuario.id:
            raise SemPermissao("Local não pertence ao usuário autenticado")

        inicio_dt = payload.data_inicio
        fim_dt = ajustar_fim_intervalo(inicio_dt, payload.data_fim)

        duration = fim_dt - inicio_dt
        if duration.total_seconds() > _DURACAO_MAXIMA_HORAS * 3600:
            raise RequisicaoInvalida(
                f"A duração do plantão não pode ser maior que {_DURACAO_MAXIMA_HORAS} horas."
            )

        recorrencia = payload.recorrencia if payload.recorrencia != "Unico" else None
        if payload.pagamento_plantao is not None:
            pagamento_plantao = payload.pagamento_plantao
        elif local.dia_pagamento is not None:
            pagamento_plantao = PagamentoService.calcular_data_pagamento_plantao(
                data_plantao=payload.data_inicio.date(),
                indice_dia_pagamento=local.dia_pagamento,
                data_inicio_ciclo=local.data_inicio,
                data_final_ciclo=local.data_final,
                util_corrido=local.util_corrido,
            )
        else:
            pagamento_plantao = None

        try:
            plantao = PlantaoRepository.create(
                db,
                PlantaoCreateWithUsuario(
                    usuario_id=usuario.id,
                    local_id=payload.local_id,
                    valor=payload.valor if payload.valor is not None else Decimal("0"),
                    data_inicio=inicio_dt.date(),
                    data_fim=fim_dt.date(),
                    horario_inicio=inicio_dt.time(),
                    horario_fim=fim_dt.time(),
                    recorrencia=recorrencia,
                    sobreaviso=payload.sobreaviso,
                    descricao=payload.descricao,
                    notificacao_ativa=payload.notificacao_ativa,
                    notificacao_antecedencia_minutos=payload.notificacao_antecedencia_minutos,
                    executado=False,
                    pagamento_plantao=pagamento_plantao,

                    transferido=False,
                    uuid_cliente=payload.uuid_cliente,
                ),
            )
            FinanceiroRepository.sync_mes(db, plantao.local_id, plantao.data_inicio.month, plantao.data_inicio.year)
        except Exception:
            db.rollback()
            raise
        logger.info("Plantão criado para usuário %s no local %s", usuario.email, payload.local_id)
        return plantao

    @staticmethod
    def atualizar(
        db: Session,
        usuario: User,
        plantao_id: int,
        payload: PlantaoUpdate,
        *,
        aplicar_em: str,
    ) -> Plantao:
        _exigir_aplicar_em(aplicar_em)

        dados_atualizacao = payload.model_dump(exclude_unset=True)
        if not dados_atualizacao:
            raise RequisicaoInvalida("Nenhum campo enviado para atualização.")

        # `aplicar_em` chega como argumento explícito; o campo de mesmo nome do
        # payload não é coluna e não pode ir para o setattr abaixo.
        dados_atualizacao.pop("aplicar_em", None)
        if dados_atualizacao.get("recorrencia") == "Unico":
            dados_atualizacao["recorrencia"] = None

        plantao_atual = PlantaoRepository.get_by_id_and_user_incluindo_transferidos(db, plantao_id, usuario.id)
        if plantao_atual is None:
            raise NaoEncontrado("Plantão não encontrado")
        if plantao_atual.transferido:
            raise Conflito("Este plantão foi transferido e não pode mais ser editado pelo dono original.")

        local_destino_id = dados_atualizacao.get("local_id", plantao_atual.local_id)
        local = LocalRepository.get_by_id(db, local_destino_id)
        if local is None or local.usuario_id != usuario.id:
            raise NaoEncontrado("Local não encontrado")

        inicio_dt_atual = datetime.combine(plantao_atual.data_inicio, plantao_atual.horario_inicio)
        fim_dt_atual = datetime.combine(plantao_atual.data_fim, plantao_atual.horario_fim)

        inicio_dt = dados_atualizacao["data_inicio"] if "data_inicio" in dados_atualizacao else inicio_dt_atual
        fim_dt = dados_atualizacao["data_fim"] if "data_fim" in dados_atualizacao else fim_dt_atual
        fim_dt = ajustar_fim_intervalo(inicio_dt, fim_dt)

        # Persist adjusted values so _decompor_datetimes sees the correct dates
        if "data_inicio" in dados_atualizacao:
            dados_atualizacao["data_inicio"] = inicio_dt
        if "data_fim" in dados_atualizacao:
            dados_atualizacao["data_fim"] = fim_dt

        if (fim_dt - inicio_dt).total_seconds() > _DURACAO_MAXIMA_HORAS * 3600:
            raise RequisicaoInvalida("A duração do plantão não pode ser maior que 48 horas.")

        def _calcular_data_pagamento(data_plantao):
            if local.dia_pagamento is not None:
                return PagamentoService.calcular_data_pagamento_plantao(
                    data_plantao=data_plantao,
                    indice_dia_pagamento=local.dia_pagamento,
                    data_inicio_ciclo=local.data_inicio,
                    data_final_ciclo=local.data_final,
                    util_corrido=local.util_corrido,
                )
            return None

        if payload.pagamento_plantao is not None:
            nova_data_pagamento = payload.pagamento_plantao
        else:
            nova_data_pagamento = _calcular_data_pagamento(inicio_dt.date())
        dados_atualizacao["pagamento_plantao"] = nova_data_pagamento

        def _decompor_datetimes(dados: dict) -> dict:
            result = {k: v for k, v in dados.items() if k not in ("data_inicio", "data_fim")}
            if "data_inicio" in dados and dados["data_inicio"] is not None:
                result["data_inicio"] = dados["data_inicio"].date()
                result["horario_inicio"] = dados["data_inicio"].time()
            if "data_fim" in dados and dados["data_fim"] is not None:
                result["data_fim"] = dados["data_fim"].date()
                result["horario_fim"] = dados["data_fim"].time()
            return result

        _meses_sync: set[tuple[int, int, int]] = {(plantao_atual.local_id, plantao_atual.data_inicio.month, plantao_atual.data_inicio.year)}
        _novos_plantoes_criados: list = []

        try:
            if aplicar_em == "somente_este":
                plantao = PlantaoRepository.update_by_user(db, plantao_id, usuario.id, **_decompor_datetimes(dados_atualizacao))
                if plantao is None:
                    raise NaoEncontrado("Plantão não encontrado")
            else:
                plantoes_futuros = PlantaoRepository.list_futuros_da_serie(db, plantao_atual)
                for _p_fut in plantoes_futuros:
                    _meses_sync.add((_p_fut.local_id, _p_fut.data_inicio.month, _p_fut.data_inicio.year))
                if not plantoes_futuros:
                    raise NaoEncontrado("Série de plantões não encontrada")

                nova_recorrencia = dados_atualizacao.get("recorrencia", plantao_atual.recorrencia)
                data_ancora = plantao_atual.data_inicio
                delta_data = None
                if "data_inicio" in dados_atualizacao:
                    delta_data = dados_atualizacao["data_inicio"].date() - data_ancora
                plantoes_por_data = {p.data_inicio: p for p in plantoes_futuros}
                plantao_selecionado = plantoes_futuros[0]

                def _aplicar_updates(plantao_item: Plantao, data_referencia, forcar_recorrencia=None):
                    for chave, valor in dados_atualizacao.items():
                        if chave in ("data_inicio", "data_fim"):
                            continue
                        if hasattr(plantao_item, chave):
                            setattr(plantao_item, chave, valor)
                    new_date_inicio = data_referencia + delta_data if delta_data is not None else data_referencia
                    new_time_inicio = dados_atualizacao["data_inicio"].time() if "data_inicio" in dados_atualizacao else plantao_item.horario_inicio
                    if "data_fim" in dados_atualizacao:
                        new_fim_dt = dados_atualizacao["data_fim"]
                        ref_ini = dados_atualizacao.get("data_inicio", datetime.combine(plantao_item.data_inicio, plantao_item.horario_inicio))
                        overnight = (new_fim_dt.date() - ref_ini.date()).days
                        new_time_fim = new_fim_dt.time()
                    else:
                        overnight = (plantao_item.data_fim - plantao_item.data_inicio).days
                        new_time_fim = plantao_item.horario_fim
                    plantao_item.data_inicio = new_date_inicio
                    plantao_item.data_fim = new_date_inicio + timedelta(days=overnight)
                    plantao_item.horario_inicio = new_time_inicio
                    plantao_item.horario_fim = new_time_fim
                    if forcar_recorrencia is not None:
                        plantao_item.recorrencia = forcar_recorrencia
                    plantao_item.pagamento_plantao = _calcular_data_pagamento(plantao_item.data_inicio)

                if "recorrencia" in dados_atualizacao and nova_recorrencia is None:
                    for p in plantoes_futuros[1:]:
                        db.delete(p)
                    _aplicar_updates(plantao_selecionado, plantao_selecionado.data_inicio)

                elif plantao_atual.recorrencia == "Semanal" and nova_recorrencia == "Quinzenal":
                    for p in plantoes_futuros[1:]:
                        diferenca = (p.data_inicio - data_ancora).days
                        if diferenca % 14 != 0:
                            db.delete(p)
                        else:
                            _aplicar_updates(p, p.data_inicio, forcar_recorrencia="Quinzenal")
                    _aplicar_updates(plantao_selecionado, plantao_selecionado.data_inicio, forcar_recorrencia="Quinzenal")

                elif plantao_atual.recorrencia == "Quinzenal" and nova_recorrencia == "Semanal":
                    data_limite = local.data_final
                    _d, novos_count = data_ancora + timedelta(days=7), 0
                    while _d <= data_limite:
                        if _d not in plantoes_por_data:
                            novos_count += 1
                        _d += timedelta(days=7)
                    PlanoService.verificar_limite_serie(db, usuario, novos_count)

                    for p in plantoes_futuros:
                        _aplicar_updates(p, p.data_inicio, forcar_recorrencia="Semanal")

                    overnight_offset = (plantao_atual.data_fim - plantao_atual.data_inicio).days
                    if "data_fim" in dados_atualizacao and "data_inicio" in dados_atualizacao:
                        overnight_offset = (dados_atualizacao["data_fim"].date() - dados_atualizacao["data_inicio"].date()).days
                    proxima_data = data_ancora + timedelta(days=7)
                    while proxima_data <= data_limite:
                        if proxima_data not in plantoes_por_data:
                            novo_valor = dados_atualizacao.get("valor", plantao_atual.valor)
                            if isinstance(novo_valor, Decimal):
                                novo_valor = float(novo_valor)
                            novo_time_inicio = dados_atualizacao["data_inicio"].time() if "data_inicio" in dados_atualizacao else plantao_atual.horario_inicio
                            novo_time_fim = dados_atualizacao["data_fim"].time() if "data_fim" in dados_atualizacao else plantao_atual.horario_fim
                            novo_plantao = Plantao(
                                usuario_id=usuario.id,
                                local_id=dados_atualizacao.get("local_id", plantao_atual.local_id),
                                valor=novo_valor,
                                data_inicio=proxima_data,
                                data_fim=proxima_data + timedelta(days=overnight_offset),
                                horario_inicio=novo_time_inicio,
                                horario_fim=novo_time_fim,
                                recorrencia="Semanal",
                                sobreaviso=dados_atualizacao.get("sobreaviso", plantao_atual.sobreaviso),
                                notificacao_ativa=dados_atualizacao.get("notificacao_ativa", plantao_atual.notificacao_ativa),
                                notificacao_antecedencia_minutos=dados_atualizacao.get(
                                    "notificacao_antecedencia_minutos",
                                    plantao_atual.notificacao_antecedencia_minutos,
                                ),
                                notificado=False,
                                executado=False,
                                transferido=False,
                                recebido_via_transferencia=False,
                                pagamento_plantao=_calcular_data_pagamento(proxima_data),
                            )
                            db.add(novo_plantao)
                            _novos_plantoes_criados.append(novo_plantao)
                        proxima_data += timedelta(days=7)

                elif plantao_atual.recorrencia in (None, "Unico") and nova_recorrencia in {"Semanal", "Quinzenal"}:
                    intervalo_dias = 7 if nova_recorrencia == "Semanal" else 14
                    data_limite = local.data_final
                    _d2, novos_count2 = plantao_selecionado.data_inicio + timedelta(days=intervalo_dias), 0
                    while _d2 <= data_limite:
                        novos_count2 += 1
                        _d2 += timedelta(days=intervalo_dias)
                    PlanoService.verificar_limite_serie(db, usuario, novos_count2)

                    _aplicar_updates(plantao_selecionado, plantao_selecionado.data_inicio, forcar_recorrencia=nova_recorrencia)

                    overnight_offset2 = (plantao_atual.data_fim - plantao_atual.data_inicio).days
                    if "data_fim" in dados_atualizacao and "data_inicio" in dados_atualizacao:
                        overnight_offset2 = (dados_atualizacao["data_fim"].date() - dados_atualizacao["data_inicio"].date()).days
                    proxima_data = plantao_selecionado.data_inicio + timedelta(days=intervalo_dias)
                    while proxima_data <= data_limite:
                        novo_valor = dados_atualizacao.get("valor", plantao_atual.valor)
                        if isinstance(novo_valor, Decimal):
                            novo_valor = float(novo_valor)
                        novo_time_inicio = dados_atualizacao["data_inicio"].time() if "data_inicio" in dados_atualizacao else plantao_atual.horario_inicio
                        novo_time_fim = dados_atualizacao["data_fim"].time() if "data_fim" in dados_atualizacao else plantao_atual.horario_fim
                        novo_plantao = Plantao(
                            usuario_id=usuario.id,
                            local_id=dados_atualizacao.get("local_id", plantao_atual.local_id),
                            valor=novo_valor,
                            data_inicio=proxima_data,
                            data_fim=proxima_data + timedelta(days=overnight_offset2),
                            horario_inicio=novo_time_inicio,
                            horario_fim=novo_time_fim,
                            recorrencia=nova_recorrencia,
                            sobreaviso=dados_atualizacao.get("sobreaviso", plantao_atual.sobreaviso),
                            notificacao_ativa=dados_atualizacao.get("notificacao_ativa", plantao_atual.notificacao_ativa),
                            notificacao_antecedencia_minutos=dados_atualizacao.get(
                                "notificacao_antecedencia_minutos",
                                plantao_atual.notificacao_antecedencia_minutos,
                            ),
                            notificado=False,
                            executado=False,
                            transferido=False,
                            recebido_via_transferencia=False,
                            pagamento_plantao=_calcular_data_pagamento(proxima_data),
                        )
                        db.add(novo_plantao)
                        _novos_plantoes_criados.append(novo_plantao)
                        proxima_data += timedelta(days=intervalo_dias)

                else:
                    for p in plantoes_futuros:
                        _aplicar_updates(p, p.data_inicio)

                db.commit()
                db.refresh(plantao_selecionado)
                plantao = plantao_selecionado
        except Exception:
            db.rollback()
            raise

        logger.info("Plantão %s atualizado por %s", plantao_id, usuario.email)
        _meses_sync.add((plantao.local_id, plantao.data_inicio.month, plantao.data_inicio.year))
        for _p_novo in _novos_plantoes_criados:
            _meses_sync.add((_p_novo.local_id, _p_novo.data_inicio.month, _p_novo.data_inicio.year))
        for _local_s, _mes_s, _ano_s in _meses_sync:
            FinanceiroRepository.sync_mes(db, _local_s, _mes_s, _ano_s)
        return plantao

    @staticmethod
    def deletar(db: Session, usuario: User, plantao_id: int, *, aplicar_em: str) -> int:
        """Exclui o plantão (e, com aplicar_em="este_e_futuros", os futuros da
        mesma série). Devolve quantos foram excluídos."""
        _exigir_aplicar_em(aplicar_em)

        plantao_del = PlantaoRepository.get_by_id_and_user_incluindo_transferidos(db, plantao_id, usuario.id)
        if plantao_del is None:
            raise NaoEncontrado("Plantão não encontrado")
        if plantao_del.transferido:
            raise Conflito("Este plantão foi transferido e não pode mais ser editado pelo dono original.")

        if aplicar_em == "somente_este":
            plantoes_para_excluir = [plantao_del]
        else:
            # Mesmo critério de série do PATCH (list_futuros_da_serie): local +
            # horário + recorrência + data_inicio >= a deste plantão. Já filtra
            # transferido=False, então plantões já transferidos ficam de fora.
            plantoes_para_excluir = PlantaoRepository.list_futuros_da_serie(db, plantao_del)

        _meses_sync = {(p.local_id, p.data_inicio.month, p.data_inicio.year) for p in plantoes_para_excluir}
        for p in plantoes_para_excluir:
            PlantaoRepository.delete_by_user(db, p.id, usuario.id)
        for local_id, mes, ano in _meses_sync:
            FinanceiroRepository.sync_mes(db, local_id, mes, ano)

        logger.info(
            "Plantão %s deletado por %s (aplicar_em=%s, total=%d)",
            plantao_id, usuario.email, aplicar_em, len(plantoes_para_excluir),
        )
        return len(plantoes_para_excluir)
