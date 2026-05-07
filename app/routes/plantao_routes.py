import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import Plantao
from app.core.security import create_access_token, decode_access_token
from app.repositories.plantao_repository import (
    LocalRepository,
    PlantaoRepository,
    TokenRevogadoRepository,
    UserRepository,
)
from app.schemas.plantao_schema import (
    APIResponse,
    AuthResponse,
    AuthStatusResponse,
    FeedbackCreate,
    FinanceiroData,
    LocalCreate,
    LocalCreateWithUsuario,
    LocalResponse,
    LocalUpdate,
    PlantaoCreate,
    PlantaoCreateWithUsuario,
    PlantaoExecutadoUpdate,
    PlantaoPagamentoRecebidoUpdate,
    PlantaoResponse,
    PlantaoUpdate,
    UserCreate,
    UserLogin,
    UserResponse,
)
from app.services.financeiro_service import FinanceiroService
from app.services.pagamento_service import PagamentoService, ajustar_fim_intervalo
from app.services.plano_service import PlanoService
from app.services.feedback_service import FeedbackService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["plantoes"])
security = HTTPBearer(auto_error=False)


def unauthorized_exception() -> HTTPException:
    return HTTPException(status_code=401, detail="Não autenticado")


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: Session = Depends(get_db),
):
    if credentials is None:
        raise unauthorized_exception()

    token_payload = decode_access_token(credentials.credentials)
    if token_payload is None:
        raise unauthorized_exception()

    user_id = int(token_payload["user_id"])
    token_id = str(token_payload["jti"])
    if TokenRevogadoRepository.is_revoked(db, token_id):
        raise unauthorized_exception()

    user = UserRepository.get_by_id(db, user_id)
    if user is None:
        raise unauthorized_exception()

    return user


@router.post(
    "/usuarios",
    response_model=APIResponse[UserResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Criar usuário",
    description="Cria um novo usuário para acessar o sistema.",
)
def criar_usuario(payload: UserCreate, db: Session = Depends(get_db)):
    try:
        user = UserRepository.create(db, payload)
        logger.info("Usuário criado: %s", user.email)
        return APIResponse(success=True, message="Usuário criado com sucesso.", data=UserResponse.model_validate(user))
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail="Email já cadastrado") from exc


@router.post(
    "/auth/login",
    response_model=APIResponse[AuthResponse],
    summary="Autenticar usuário",
    description="Autentica o usuário e retorna um token de acesso JWT.",
)
def autenticar_usuario(payload: UserLogin, db: Session = Depends(get_db)):
    user = UserRepository.authenticate(db, payload)
    if user is None:
        raise HTTPException(status_code=401, detail="Credenciais inválidas")

    access_token = create_access_token(user.id)
    logger.info("Login realizado: %s", user.email)
    return APIResponse(
        success=True,
        message="Login realizado com sucesso.",
        data=AuthResponse(autenticado=True, access_token=access_token, usuario=UserResponse.model_validate(user)),
    )


@router.get("/auth/status", response_model=AuthStatusResponse)
def status_autenticacao(current_user=Depends(get_current_user)):
    return AuthStatusResponse(autenticado=True, usuario=UserResponse.model_validate(current_user))


@router.post("/auth/logout", response_model=AuthStatusResponse)
def logout_usuario(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: Session = Depends(get_db),
):
    if credentials is None:
        raise unauthorized_exception()

    token_payload = decode_access_token(credentials.credentials)
    if token_payload is None:
        raise unauthorized_exception()

    try:
        TokenRevogadoRepository.revoke(
            db,
            jti=str(token_payload["jti"]),
            usuario_id=int(token_payload["user_id"]),
            expira_em=token_payload["exp"] if isinstance(token_payload["exp"], datetime) else datetime.now(UTC),
        )
    except IntegrityError:
        db.rollback()
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="Falha ao processar logout") from exc

    return AuthStatusResponse(autenticado=False, usuario=None)


@router.post(
    "/feedback",
    response_model=APIResponse[None],
    summary="Enviar feedback",
    description="Envia um feedback do usuário autenticado por e-mail para a equipe de desenvolvimento.",
)
def enviar_feedback(payload: FeedbackCreate, current_user=Depends(get_current_user)):
    try:
        FeedbackService.enviar_feedback(current_user, payload.mensagem)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Falha ao enviar feedback do usuário %s", current_user.email)
        raise HTTPException(status_code=500, detail="Não foi possível enviar o feedback no momento.") from exc

    return APIResponse(success=True, message="Feedback enviado com sucesso.", data=None)


@router.get("/usuarios", response_model=UserResponse)
def listar_usuarios(current_user=Depends(get_current_user)):
    # RETORNA APENAS O PRÓPRIO USUÁRIO (SEGURANÇA)
    return UserResponse.model_validate(current_user)


@router.post(
    "/usuarios/upgrade",
    response_model=APIResponse[UserResponse],
    summary="Upgrade para premium",
    description="Atualiza o plano do usuário autenticado para premium.",
)
def upgrade_usuario(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    atualizado = PlanoService.upgrade_para_premium(current_user)
    db.add(current_user)
    db.commit()
    db.refresh(current_user)

    logger.info("Plano atualizado para %s: %s", current_user.plano, current_user.email)
    return APIResponse(
        success=True,
        message="Usuário atualizado para premium" if atualizado else "Usuário já possui plano premium",
        data=UserResponse.model_validate(current_user),
    )


@router.post(
    "/locais",
    response_model=LocalResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Criar local",
    description="Cria um local para o usuário autenticado. O usuário não pode ser informado no corpo da requisição.",
)
def criar_local(payload: LocalCreate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    local_data = payload.model_dump()
    local_data["usuario_id"] = current_user.id
    local = LocalRepository.create(db, LocalCreateWithUsuario.model_validate(local_data))
    logger.info("Local criado para usuário %s: %s", current_user.email, local.nome_local)
    return local


@router.get(
    "/locais",
    response_model=list[LocalResponse],
    summary="Listar locais",
    description="Retorna os locais cadastrados pelo usuário autenticado.",
)
def listar_locais(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return LocalRepository.list_by_user(db, current_user.id)


@router.patch(
    "/locais/{local_id}",
    response_model=APIResponse[LocalResponse],
    summary="Atualizar local",
    description="Atualiza os campos de um local pertencente ao usuário autenticado.",
)
def atualizar_local(
    local_id: int,
    payload: LocalUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    dados_atualizacao = payload.model_dump(exclude_unset=True)
    if not dados_atualizacao:
        raise HTTPException(status_code=400, detail="Nenhum campo enviado para atualização.")

    local_atual = LocalRepository.get_by_id(db, local_id)
    if local_atual is None or local_atual.usuario_id != current_user.id:
        raise HTTPException(status_code=404, detail="Local não encontrado")
    dia_pagamento_anterior = local_atual.dia_pagamento
    dia_pagamento_novo = dados_atualizacao.get("dia_pagamento", dia_pagamento_anterior)
    util_corrido_anterior = local_atual.util_corrido
    util_corrido_novo = dados_atualizacao.get("util_corrido", util_corrido_anterior)
    recalcular_pagamento = (dia_pagamento_novo != dia_pagamento_anterior) or (util_corrido_novo != util_corrido_anterior)

    try:
        for key, value in dados_atualizacao.items():
            if hasattr(local_atual, key):
                setattr(local_atual, key, value)

        if recalcular_pagamento and dia_pagamento_novo is not None:
            plantoes = PlantaoRepository.list_ids_e_datas_por_local(db, local_id)
            if plantoes:
                atualizacoes = [
                    {
                        "id": plantao_id,
                        "pagamento_plantao": PagamentoService.calcular_data_pagamento_por_dia_mes(
                            data_plantao=data_plantao,
                            dia_pagamento=dia_pagamento_novo,
                            util_corrido=util_corrido_novo,
                        ),
                    }
                    for plantao_id, data_plantao in plantoes
                ]
                db.bulk_update_mappings(Plantao, atualizacoes)

        db.commit()
        db.refresh(local_atual)
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="Erro ao atualizar local") from exc

    logger.info("Local %s atualizado por %s", local_id, current_user.email)
    return APIResponse(
        success=True,
        message="Local atualizado com sucesso.",
        data=LocalResponse.model_validate(local_atual),
    )


@router.delete(
    "/locais/{local_id}",
    response_model=APIResponse[None],
    summary="Deletar local",
    description="Remove um local do usuário autenticado e seus plantões associados.",
)
def deletar_local(
    local_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    deletado = LocalRepository.delete_by_user(db, local_id, current_user.id)
    if not deletado:
        raise HTTPException(status_code=404, detail="Local não encontrado")

    logger.info("Local %s deletado por %s", local_id, current_user.email)
    return APIResponse(
        success=True,
        message="Local deletado com sucesso.",
        data=None,
    )


@router.post(
    "/plantoes",
    response_model=APIResponse[PlantaoResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Criar plantão",
    description="Cria um plantão para o usuário autenticado, validando duração e horários.",
)
def criar_plantao(payload: PlantaoCreate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    PlanoService.limitar_plano_free(db, current_user)

    local = LocalRepository.get_by_id(db, payload.local_id)
    if local is None:
        raise HTTPException(status_code=404, detail="Local não encontrado")
    if local.usuario_id != current_user.id:
        raise HTTPException(status_code=403, detail="Local não pertence ao usuário autenticado")

    inicio_dt = datetime.combine(payload.data, payload.horario_inicio)
    fim_dt = ajustar_fim_intervalo(inicio_dt, datetime.combine(payload.data, payload.horario_fim))

    max_duration_hours = 48
    duration = fim_dt - inicio_dt
    if duration.total_seconds() > max_duration_hours * 3600:
        raise HTTPException(
            status_code=400,
            detail=f"A duração do plantão não pode ser maior que {max_duration_hours} horas.",
        )

    # Verificar conflito
    plantao_conflitante = PlantaoRepository.get_conflito(
        db,
        usuario_id=current_user.id,
        inicio=inicio_dt,
        fim=fim_dt,
    )

    # Se houver conflito e acao_conflito for None
    if plantao_conflitante and payload.acao_conflito is None:
        raise HTTPException(
            status_code=409,
            detail="Conflito de horário detectado. Escolha uma ação para resolver: 'manter', 'ajustar' ou 'cancelar'."
        )

    # Se acao_conflito == "cancelar"
    if payload.acao_conflito == "cancelar":
        logger.info("Criação de plantão cancelada por usuário %s", current_user.email)
        return APIResponse(
            success=True,
            message="Criação de plantão cancelada conforme solicitado.",
            data=None,
        )

    # Se acao_conflito == "ajustar"
    if payload.acao_conflito == "ajustar" and plantao_conflitante:
        try:
            # Atualizar o horario_fim do plantão conflitante para o horario_inicio do novo
            PlantaoRepository.update(
                db,
                plantao_conflitante.id,
                horario_fim=payload.horario_inicio
            )
            logger.info(
                "Plantão %s ajustado para resolver conflito com novo plantão do usuário %s",
                plantao_conflitante.id,
                current_user.email
            )
        except Exception as exc:
            db.rollback()
            raise HTTPException(status_code=500, detail="Erro ao ajustar plantão conflitante") from exc

    # Criar plantão (acao_conflito == "manter" ou "ajustar" com ajuste realizado)
    data = payload.model_dump()
    if data.get("recorrencia") == "Unico":
        data["recorrencia"] = None
    data["usuario_id"] = current_user.id
    data["executado"] = False
    data["pagamento_recebido"] = False
    if local.dia_pagamento is not None:
        data["pagamento_plantao"] = PagamentoService.calcular_data_pagamento_plantao(
            data_plantao=payload.data,
            indice_dia_pagamento=local.dia_pagamento,
            data_inicio_ciclo=local.data_inicio,
            data_final_ciclo=local.data_final,
            util_corrido=local.util_corrido,
        )

    try:
        plantao = PlantaoRepository.create(db, PlantaoCreateWithUsuario.model_validate(data))
        logger.info("Plantão criado para usuário %s no local %s", current_user.email, payload.local_id)
        return APIResponse(
            success=True,
            message="Plantão criado com sucesso.",
            data=PlantaoResponse.model_validate(plantao),
        )
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="Erro ao criar plantão") from exc


@router.get(
    "/plantoes",
    response_model=list[PlantaoResponse],
    summary="Listar plantões",
    description="Retorna os plantões do usuário autenticado.",
)
def listar_plantoes(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return PlantaoRepository.list_by_user(db, current_user.id)


@router.patch(
    "/plantoes/{plantao_id}",
    response_model=APIResponse[PlantaoResponse],
    summary="Atualizar plantão",
    description="Atualiza os campos de um plantão pertencente ao usuário autenticado.",
)
def atualizar_plantao(
    plantao_id: int,
    payload: PlantaoUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    dados_atualizacao = payload.model_dump(exclude_unset=True)
    if not dados_atualizacao:
        raise HTTPException(status_code=400, detail="Nenhum campo enviado para atualização.")

    aplicar_em = dados_atualizacao.pop("aplicar_em", "somente_este")
    if dados_atualizacao.get("recorrencia") == "Unico":
        dados_atualizacao["recorrencia"] = None

    plantao_atual = PlantaoRepository.get_by_id_and_user(db, plantao_id, current_user.id)
    if plantao_atual is None:
        raise HTTPException(status_code=404, detail="Plantão não encontrado")

    local_destino_id = dados_atualizacao.get("local_id", plantao_atual.local_id)
    local = LocalRepository.get_by_id(db, local_destino_id)
    if local is None or local.usuario_id != current_user.id:
        raise HTTPException(status_code=404, detail="Local não encontrado")

    data_ref = dados_atualizacao.get("data", plantao_atual.data)
    horario_inicio_ref = dados_atualizacao.get("horario_inicio", plantao_atual.horario_inicio)
    horario_fim_ref = dados_atualizacao.get("horario_fim", plantao_atual.horario_fim)

    inicio_dt = datetime.combine(data_ref, horario_inicio_ref)
    fim_dt = ajustar_fim_intervalo(inicio_dt, datetime.combine(data_ref, horario_fim_ref))
    if (fim_dt - inicio_dt).total_seconds() > 48 * 3600:
        raise HTTPException(status_code=400, detail="A duração do plantão não pode ser maior que 48 horas.")

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

    nova_data_pagamento = _calcular_data_pagamento(data_ref)
    dados_atualizacao["pagamento_plantao"] = nova_data_pagamento
    if nova_data_pagamento != plantao_atual.pagamento_plantao:
        dados_atualizacao["pagamento_recebido"] = False

    if local.dia_pagamento is None:
        dados_atualizacao["pagamento_recebido"] = False

    try:
        if aplicar_em == "somente_este":
            plantao = PlantaoRepository.update_by_user(db, plantao_id, current_user.id, **dados_atualizacao)
            if plantao is None:
                raise HTTPException(status_code=404, detail="Plantão não encontrado")
        else:
            plantoes_futuros = PlantaoRepository.list_futuros_da_serie(db, plantao_atual)
            if not plantoes_futuros:
                raise HTTPException(status_code=404, detail="Série de plantões não encontrada")

            nova_recorrencia = dados_atualizacao.get("recorrencia", plantao_atual.recorrencia)
            data_ancora = plantao_atual.data
            delta_data = None
            if "data" in dados_atualizacao:
                delta_data = dados_atualizacao["data"] - data_ancora
            plantoes_por_data = {p.data: p for p in plantoes_futuros}
            plantao_selecionado = plantoes_futuros[0]

            def _aplicar_updates(plantao_item: Plantao, data_referencia, forcar_recorrencia=None):
                for chave, valor in dados_atualizacao.items():
                    if chave == "data":
                        continue
                    setattr(plantao_item, chave, valor)
                if delta_data is not None:
                    plantao_item.data = data_referencia + delta_data
                if forcar_recorrencia is not None:
                    plantao_item.recorrencia = forcar_recorrencia
                plantao_item.pagamento_plantao = _calcular_data_pagamento(plantao_item.data)
                plantao_item.pagamento_recebido = False

            if "recorrencia" in dados_atualizacao and nova_recorrencia is None:
                for p in plantoes_futuros[1:]:
                    db.delete(p)
                _aplicar_updates(plantao_selecionado, plantao_selecionado.data)

            elif plantao_atual.recorrencia == "Semanal" and nova_recorrencia == "Quinzenal":
                for p in plantoes_futuros[1:]:
                    diferenca = (p.data - data_ancora).days
                    if diferenca % 14 != 0:
                        db.delete(p)
                    else:
                        _aplicar_updates(p, p.data, forcar_recorrencia="Quinzenal")
                _aplicar_updates(plantao_selecionado, plantao_selecionado.data, forcar_recorrencia="Quinzenal")

            elif plantao_atual.recorrencia == "Quinzenal" and nova_recorrencia == "Semanal":
                for p in plantoes_futuros:
                    _aplicar_updates(p, p.data, forcar_recorrencia="Semanal")

                data_limite = local.data_final
                proxima_data = data_ancora + timedelta(days=7)
                while proxima_data <= data_limite:
                    if proxima_data not in plantoes_por_data:
                        novo_valor = dados_atualizacao.get("valor", plantao_atual.valor)
                        if isinstance(novo_valor, Decimal):
                            novo_valor = float(novo_valor)
                        novo_plantao = Plantao(
                            usuario_id=current_user.id,
                            local_id=dados_atualizacao.get("local_id", plantao_atual.local_id),
                            valor=novo_valor,
                            data=proxima_data,
                            horario_inicio=dados_atualizacao.get("horario_inicio", plantao_atual.horario_inicio),
                            horario_fim=dados_atualizacao.get("horario_fim", plantao_atual.horario_fim),
                            recorrencia="Semanal",
                            sobreaviso=dados_atualizacao.get("sobreaviso", plantao_atual.sobreaviso),
                            notificacao_ativa=dados_atualizacao.get("notificacao_ativa", plantao_atual.notificacao_ativa),
                            notificacao_antecedencia_minutos=dados_atualizacao.get(
                                "notificacao_antecedencia_minutos",
                                plantao_atual.notificacao_antecedencia_minutos,
                            ),
                            notificado=False,
                            executado=False,
                            pagamento_plantao=_calcular_data_pagamento(proxima_data),
                            pagamento_recebido=False,
                        )
                        db.add(novo_plantao)
                    proxima_data += timedelta(days=7)

            elif plantao_atual.recorrencia in (None, "Unico") and nova_recorrencia in {"Semanal", "Quinzenal"}:
                intervalo_dias = 7 if nova_recorrencia == "Semanal" else 14
                _aplicar_updates(plantao_selecionado, plantao_selecionado.data, forcar_recorrencia=nova_recorrencia)

                data_limite = local.data_final
                proxima_data = plantao_selecionado.data + timedelta(days=intervalo_dias)
                while proxima_data <= data_limite:
                    novo_valor = dados_atualizacao.get("valor", plantao_atual.valor)
                    if isinstance(novo_valor, Decimal):
                        novo_valor = float(novo_valor)
                    novo_plantao = Plantao(
                        usuario_id=current_user.id,
                        local_id=dados_atualizacao.get("local_id", plantao_atual.local_id),
                        valor=novo_valor,
                        data=proxima_data,
                        horario_inicio=dados_atualizacao.get("horario_inicio", plantao_atual.horario_inicio),
                        horario_fim=dados_atualizacao.get("horario_fim", plantao_atual.horario_fim),
                        recorrencia=nova_recorrencia,
                        sobreaviso=dados_atualizacao.get("sobreaviso", plantao_atual.sobreaviso),
                        notificacao_ativa=dados_atualizacao.get("notificacao_ativa", plantao_atual.notificacao_ativa),
                        notificacao_antecedencia_minutos=dados_atualizacao.get(
                            "notificacao_antecedencia_minutos",
                            plantao_atual.notificacao_antecedencia_minutos,
                        ),
                        notificado=False,
                        executado=False,
                        pagamento_plantao=_calcular_data_pagamento(proxima_data),
                        pagamento_recebido=False,
                    )
                    db.add(novo_plantao)
                    proxima_data += timedelta(days=intervalo_dias)

            else:
                for p in plantoes_futuros:
                    _aplicar_updates(p, p.data)

            db.commit()
            db.refresh(plantao_selecionado)
            plantao = plantao_selecionado
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="Erro ao atualizar plantão") from exc

    logger.info("Plantão %s atualizado por %s", plantao_id, current_user.email)
    return APIResponse(
        success=True,
        message="Plantão atualizado com sucesso.",
        data=PlantaoResponse.model_validate(plantao),
    )


@router.patch(
    "/plantoes/{plantao_id}/executado",
    response_model=APIResponse[PlantaoResponse],
    summary="Atualizar status de execução do plantão",
    description="Atualiza o campo executado de um plantão pertencente ao usuário autenticado.",
)
def atualizar_executado_plantao(
    plantao_id: int,
    payload: PlantaoExecutadoUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    plantao = PlantaoRepository.update_executado(db, plantao_id, current_user.id, payload.executado)
    if plantao is None:
        raise HTTPException(status_code=404, detail="Plantão não encontrado")

    logger.info(
        "Status executado do plantão %s atualizado para %s por %s",
        plantao_id,
        payload.executado,
        current_user.email,
    )
    return APIResponse(
        success=True,
        message="Status executado atualizado com sucesso.",
        data=PlantaoResponse.model_validate(plantao),
    )


@router.patch(
    "/plantoes/{plantao_id}/pagamento-recebido",
    response_model=APIResponse[PlantaoResponse],
    summary="Atualizar status de recebimento do pagamento",
    description="Atualiza se o pagamento do dia do plantão foi recebido.",
)
def atualizar_pagamento_recebido_plantao(
    plantao_id: int,
    payload: PlantaoPagamentoRecebidoUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    plantao = PlantaoRepository.get_by_id_and_user(db, plantao_id, current_user.id)
    if plantao is None:
        raise HTTPException(status_code=404, detail="Plantão não encontrado")

    if plantao.pagamento_plantao is None:
        raise HTTPException(status_code=400, detail="Este plantão não possui dia de pagamento vinculado")

    plantao = PlantaoRepository.update_by_user(
        db,
        plantao_id,
        current_user.id,
        pagamento_recebido=payload.pagamento_recebido,
    )
    if plantao is None:
        raise HTTPException(status_code=404, detail="Plantão não encontrado")

    logger.info(
        "Status de pagamento recebido do plantão %s atualizado para %s por %s",
        plantao_id,
        payload.pagamento_recebido,
        current_user.email,
    )
    return APIResponse(
        success=True,
        message="Status de pagamento recebido atualizado com sucesso.",
        data=PlantaoResponse.model_validate(plantao),
    )


@router.delete(
    "/plantoes/{plantao_id}",
    response_model=APIResponse[None],
    summary="Deletar plantão",
    description="Remove um plantão pertencente ao usuário autenticado.",
)
def deletar_plantao(
    plantao_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    deletado = PlantaoRepository.delete_by_user(db, plantao_id, current_user.id)
    if not deletado:
        raise HTTPException(status_code=404, detail="Plantão não encontrado")

    logger.info("Plantão %s deletado por %s", plantao_id, current_user.email)
    return APIResponse(
        success=True,
        message="Plantão deletado com sucesso.",
        data=None,
    )


@router.get(
    "/financeiro/mensal",
    response_model=APIResponse[FinanceiroData],
    summary="Resumo financeiro mensal",
    description="Retorna o total e a quantidade de plantões do mês solicitado para o usuário autenticado.",
)
def obter_total_financeiro_mensal(
    mes: int = Query(..., ge=1, le=12, description="Mês do relatório (1-12)."),
    ano: int = Query(..., ge=1, description="Ano do relatório."),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    PlanoService.exigir_premium(current_user)
    resultado = FinanceiroService.calcular_total_mensal(db, current_user.id, mes, ano)
    return APIResponse(success=True, message="Resumo financeiro mensal gerado com sucesso.", data=FinanceiroData.model_validate(resultado))


@router.get(
    "/financeiro/anual",
    response_model=APIResponse[FinanceiroData],
    summary="Resumo financeiro anual",
    description="Retorna o total e a quantidade de plantões do ano solicitado para o usuário autenticado.",
)
def obter_total_financeiro_anual(
    ano: int = Query(..., ge=1, description="Ano do relatório."),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    PlanoService.exigir_premium(current_user)
    resultado = FinanceiroService.calcular_total_anual(db, current_user.id, ano)
    return APIResponse(success=True, message="Resumo financeiro anual gerado com sucesso.", data=FinanceiroData.model_validate(resultado))


@router.get(
    "/financeiro/mensal/local",
    response_model=APIResponse[FinanceiroData],
    summary="Resumo financeiro mensal por local",
    description="Retorna o total e a quantidade de plantões de um local específico no mês solicitado.",
)
def obter_total_financeiro_mensal_por_local(
    local_id: int = Query(..., description="ID do local."),
    mes: int = Query(..., ge=1, le=12, description="Mês do relatório (1-12)."),
    ano: int = Query(..., ge=1, description="Ano do relatório."),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    PlanoService.exigir_premium(current_user)
    local = LocalRepository.get_by_id(db, local_id)
    if local is None or local.usuario_id != current_user.id:
        raise HTTPException(status_code=404, detail="Local não encontrado")
    resultado = FinanceiroService.calcular_total_mensal_por_local(db, current_user.id, local_id, mes, ano)
    return APIResponse(success=True, message="Resumo financeiro mensal do local gerado com sucesso.", data=FinanceiroData.model_validate(resultado))
