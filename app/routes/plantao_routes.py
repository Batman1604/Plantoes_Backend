import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import get_db
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
    FinanceiroData,
    LocalCreate,
    LocalCreateWithUsuario,
    LocalResponse,
    LocalUpdate,
    PlantaoCreate,
    PlantaoCreateWithUsuario,
    PlantaoExecutadoUpdate,
    PlantaoResponse,
    PlantaoUpdate,
    UserCreate,
    UserLogin,
    UserResponse,
)
from app.services.financeiro_service import FinanceiroService
from app.services.pagamento_service import PagamentoService, ajustar_fim_intervalo

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


@router.get("/usuarios", response_model=UserResponse)
def listar_usuarios(current_user=Depends(get_current_user)):
    # RETORNA APENAS O PRÓPRIO USUÁRIO (SEGURANÇA)
    return UserResponse.model_validate(current_user)


@router.post(
    "/locais",
    response_model=LocalResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Criar local",
    description="Cria um local para o usuário autenticado. O usuário não pode ser informado no corpo da requisição.",
)
def criar_local(payload: LocalCreate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    local_data = payload.model_dump()
    if payload.dia_pagamento is not None:
        local_data["dia_pagamento"] = PagamentoService.calcular_dia_util(
            payload.data_inicio.year,
            payload.data_inicio.month,
            payload.dia_pagamento,
        )
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

    data_inicio = dados_atualizacao.get("data_inicio", local_atual.data_inicio)
    dia_pagamento = dados_atualizacao.get("dia_pagamento", local_atual.dia_pagamento)
    if dia_pagamento is not None:
        dados_atualizacao["dia_pagamento"] = PagamentoService.calcular_dia_util(
            data_inicio.year,
            data_inicio.month,
            dia_pagamento,
        )

    local = LocalRepository.update_by_user(db, local_id, current_user.id, **dados_atualizacao)
    if local is None:
        raise HTTPException(status_code=404, detail="Local não encontrado")

    logger.info("Local %s atualizado por %s", local_id, current_user.email)
    return APIResponse(
        success=True,
        message="Local atualizado com sucesso.",
        data=LocalResponse.model_validate(local),
    )


@router.post(
    "/plantoes",
    response_model=APIResponse[PlantaoResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Criar plantão",
    description="Cria um plantão para o usuário autenticado, validando duração e horários.",
)
def criar_plantao(payload: PlantaoCreate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
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
    data["usuario_id"] = current_user.id
    data["executado"] = False
    if local.dia_pagamento is not None:
        data["pagamento_plantao"] = PagamentoService.calcular_data_pagamento_plantao(
            payload.data,
            local.dia_pagamento,
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

    if local.dia_pagamento is not None:
        dados_atualizacao["pagamento_plantao"] = PagamentoService.calcular_data_pagamento_plantao(
            data_ref,
            local.dia_pagamento,
        )
    else:
        dados_atualizacao["pagamento_plantao"] = None

    plantao = PlantaoRepository.update_by_user(db, plantao_id, current_user.id, **dados_atualizacao)
    if plantao is None:
        raise HTTPException(status_code=404, detail="Plantão não encontrado")

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
    resultado = FinanceiroService.calcular_total_anual(db, current_user.id, ano)
    return APIResponse(success=True, message="Resumo financeiro anual gerado com sucesso.", data=FinanceiroData.model_validate(resultado))
