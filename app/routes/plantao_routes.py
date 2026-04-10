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
    PlantaoCreate,
    PlantaoCreateWithUsuario,
    PlantaoResponse,
    UserCreate,
    UserLogin,
    UserResponse,
)
from app.services.financeiro_service import FinanceiroService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["plantoes"])
security = HTTPBearer(auto_error=False)



def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: Session = Depends(get_db),
):
    if credentials is None:
        raise HTTPException(status_code=401, detail="Token de acesso ausente")

    token_payload = decode_access_token(credentials.credentials)
    if token_payload is None:
        raise HTTPException(status_code=401, detail="Token inválido ou expirado")

    user_id = int(token_payload["user_id"])
    token_id = str(token_payload["jti"])
    if TokenRevogadoRepository.is_revoked(db, token_id):
        raise HTTPException(status_code=401, detail="Token revogado")

    user = UserRepository.get_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Usuário do token não encontrado")

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
        raise HTTPException(status_code=401, detail="Token de acesso ausente")

    token_payload = decode_access_token(credentials.credentials)
    if token_payload is None:
        raise HTTPException(status_code=401, detail="Token inválido ou expirado")

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
    fim_dt = datetime.combine(payload.data, payload.horario_fim)

    if fim_dt <= inicio_dt:
        raise HTTPException(status_code=400, detail="Horário de fim deve ser maior que o horário de início")

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
