import logging
import secrets
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from io import BytesIO
from pathlib import Path

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, UploadFile, status
from app.core.rate_limiter import rate_limit
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from PIL import Image
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import Plantao
from app.core.security import (
    create_access_token,
    create_purpose_token,
    decode_access_token,
    decode_purpose_token,
    verify_password,
)
from app.repositories.plantao_repository import (
    FinanceiroRepository,
    LocalRepository,
    PlantaoRepository,
    TokenRevogadoRepository,
    BonificacaoRepository,
    TransferenciaRepository,
    TrocaRepository,
    UserRepository,
    VerificacaoTelefoneRepository,
)
from app.schemas.plantao_schema import (
    APIResponse,
    AlterarSenhaRequest,
    FinanceiroCreate,
    FinanceiroEmAtrasoUpdate,
    FinanceiroRecordResponse,
    PerfilUpdate,
    AuthResponse,
    AuthStatusResponse,
    DeviceTokenUpdate,
    FeedbackCreate,
    FinanceiroData,
    GraficoMensalItem,
    UpgradeWebhookRequest,
    LocalCreate,
    LocalCreateWithUsuario,
    LocalResponse,
    LocalUpdate,
    PlantaoCreate,
    PlantaoCreateWithUsuario,
    PlantaoExecutadoUpdate,
    PlantaoResponse,
    PlantaoUpdate,
    ResetPasswordRequest,
    SenhaResetConfirmar,
    SenhaResetRequest,
    TransferenciaAceitar,
    TransferenciaCreate,
    TransferenciaResponse,
    BonificacaoCreate,
    BonificacaoResponse,
    BonificacaoUpdate,
    TrocaCreate,
    TrocaResponse,
    UserCreate,
    UserLogin,
    UserResponse,
    VerificarTelefoneRequest,
)
from app.services.email_service import EmailService
from app.services.feedback_service import FeedbackService
from app.services.financeiro_service import FinanceiroService
from app.core.config import settings
from app.services.pagamento_service import PagamentoService, ajustar_fim_intervalo
from app.services.plano_service import PlanoService
from app.services.sms_service import SmsService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["plantoes"])
security = HTTPBearer(auto_error=False)


def unauthorized_exception() -> HTTPException:
    return HTTPException(status_code=401, detail="Não autenticado")


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: Session = Depends(get_db),
):
    """Dependência que valida o JWT e retorna o usuário autenticado."""
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
    response_model=APIResponse[AuthResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Criar usuário",
    description="Cria um novo usuário e retorna o token de acesso para uso imediato.",
)
def criar_usuario(payload: UserCreate, db: Session = Depends(get_db), _=Depends(rate_limit(5, 60))):
    conflitos = []
    if UserRepository.get_by_email(db, payload.email):
        conflitos.append("Email")
    if payload.telefone and UserRepository.get_by_phone(db, payload.telefone):
        conflitos.append("Telefone")
    if conflitos:
        detalhe = " e ".join(conflitos) + (" já cadastrado" if len(conflitos) == 1 else " já cadastrados")
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detalhe)

    try:
        user = UserRepository.create(db, payload)
        logger.info("Usuário criado: %s", user.email)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email já cadastrado") from exc

    try:
        token_verificacao = create_purpose_token(user.email, "email_verification", expire_minutes=24 * 60)
        EmailService.enviar_verificacao(user.email, token_verificacao)
    except Exception:
        logger.warning("Falha ao enviar email de verificação para %s", user.email)

    if user.telefone:
        _disparar_verificacao_telefone(db, user.id, user.telefone)

    access_token = create_access_token(user.id)
    return APIResponse(
        success=True,
        message="Usuário criado com sucesso.",
        data=AuthResponse(autenticado=True, access_token=access_token, usuario=UserResponse.model_validate(user)),
    )


def _disparar_verificacao_telefone(db: Session, usuario_id: int, telefone: str) -> None:
    try:
        codigo = f"{secrets.randbelow(1_000_000):06d}"
        expira_em = datetime.now(UTC) + timedelta(minutes=2)
        VerificacaoTelefoneRepository.upsert(db, usuario_id, codigo, expira_em)
        SmsService.enviar_codigo_verificacao(telefone, codigo)
        logger.info("Código de verificação enviado para %s", telefone)
    except Exception:
        logger.warning("Falha ao enviar código de verificação para %s", telefone)


@router.post(
    "/auth/login",
    response_model=APIResponse[AuthResponse],
    summary="Autenticar usuário",
    description="Autentica o usuário e retorna um token de acesso JWT.",
)
def autenticar_usuario(payload: UserLogin, db: Session = Depends(get_db), _=Depends(rate_limit(10, 60))):
    """Endpoint de login que retorna um JWT válido para o usuário."""
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
    "/auth/refresh",
    response_model=APIResponse[AuthResponse],
    summary="Renovar token",
    description="Revoga o token atual e emite um novo com validade estendida.",
)
def refresh_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    token_payload = decode_access_token(credentials.credentials)
    try:
        TokenRevogadoRepository.revoke(
            db,
            jti=str(token_payload["jti"]),
            usuario_id=int(token_payload["user_id"]),
            expira_em=token_payload["exp"],
        )
    except IntegrityError:
        db.rollback()

    new_token = create_access_token(current_user.id)
    return APIResponse(
        success=True,
        message="Token renovado com sucesso.",
        data=AuthResponse(autenticado=True, access_token=new_token, usuario=UserResponse.model_validate(current_user)),
    )


@router.get(
    "/auth/verificar-email",
    response_model=APIResponse[None],
    summary="Verificar email",
    description="Verifica o email do usuário usando o token enviado por email.",
)
def verificar_email(token: str = Query(...), db: Session = Depends(get_db)):
    email = decode_purpose_token(token, "email_verification")
    if not email:
        raise HTTPException(status_code=400, detail="Token inválido ou expirado.")
    user = UserRepository.get_by_email(db, email)
    if not user:
        raise HTTPException(status_code=400, detail="Token inválido ou expirado.")
    UserRepository.marcar_email_verificado(db, user.id)
    return APIResponse(success=True, message="Email verificado com sucesso.", data=None)


@router.post(
    "/auth/reenviar-verificacao",
    response_model=APIResponse[None],
    summary="Reenviar verificação de email",
)
def reenviar_verificacao(current_user=Depends(get_current_user)):
    if current_user.email_verificado:
        raise HTTPException(status_code=400, detail="Email já verificado.")
    try:
        token = create_purpose_token(current_user.email, "email_verification", expire_minutes=24 * 60)
        EmailService.enviar_verificacao(current_user.email, token)
    except Exception:
        logger.warning("Falha ao reenviar verificação para %s", current_user.email)
    return APIResponse(success=True, message="Email de verificação enviado.", data=None)


@router.post(
    "/auth/verificar-telefone",
    response_model=APIResponse[None],
    summary="Verificar número de telefone",
    description="Confirma o código de 6 dígitos recebido via WhatsApp e marca o telefone como verificado.",
)
def verificar_telefone(
    payload: VerificarTelefoneRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
    _=Depends(rate_limit(5, 120)),
):
    if current_user.telefone_verificado:
        raise HTTPException(status_code=400, detail="Telefone já verificado.")
    if not current_user.telefone:
        raise HTTPException(status_code=400, detail="Nenhum telefone cadastrado nesta conta.")

    verificacao = VerificacaoTelefoneRepository.get_valido(db, current_user.id, payload.codigo)
    if verificacao is None:
        raise HTTPException(status_code=400, detail="Código inválido ou expirado.")

    current_user.telefone_verificado = True
    db.add(current_user)
    VerificacaoTelefoneRepository.delete_by_user(db, current_user.id)
    db.commit()

    logger.info("Telefone verificado para usuário %s", current_user.email)
    return APIResponse(success=True, message="Telefone verificado com sucesso.", data=None)


@router.post(
    "/auth/reenviar-verificacao-telefone",
    response_model=APIResponse[None],
    summary="Reenviar código de verificação de telefone",
    description="Gera e envia um novo código de 6 dígitos via WhatsApp para o telefone cadastrado.",
)
def reenviar_verificacao_telefone(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
    _=Depends(rate_limit(3, 60)),
):
    if current_user.telefone_verificado:
        raise HTTPException(status_code=400, detail="Telefone já verificado.")
    if not current_user.telefone:
        raise HTTPException(status_code=400, detail="Nenhum telefone cadastrado nesta conta.")

    _disparar_verificacao_telefone(db, current_user.id, current_user.telefone)
    return APIResponse(success=True, message="Código reenviado para o seu WhatsApp.", data=None)


@router.post(
    "/auth/esqueci-senha",
    response_model=APIResponse[None],
    summary="Solicitar redefinição de senha",
    description="Envia um email com token para redefinição de senha. Sempre retorna 200 por segurança.",
)
def esqueci_senha(payload: SenhaResetRequest, db: Session = Depends(get_db), _=Depends(rate_limit(3, 60))):
    user = UserRepository.get_by_email(db, payload.email)
    if user:
        try:
            token = create_purpose_token(user.email, "password_reset", expire_minutes=60)
            EmailService.enviar_reset_senha(user.email, token)
        except Exception:
            logger.warning("Falha ao enviar email de reset para %s", payload.email)
    return APIResponse(success=True, message="Se o email existir, as instruções foram enviadas.", data=None)


@router.post(
    "/auth/redefinir-senha",
    response_model=APIResponse[None],
    summary="Redefinir senha com token",
)
def redefinir_senha(payload: SenhaResetConfirmar, db: Session = Depends(get_db), _=Depends(rate_limit(5, 60))):
    email = decode_purpose_token(payload.token, "password_reset")
    if not email:
        raise HTTPException(status_code=400, detail="Token inválido ou expirado.")
    user = UserRepository.get_by_email(db, email)
    if not user:
        raise HTTPException(status_code=400, detail="Token inválido ou expirado.")
    UserRepository.update_senha(db, user.id, payload.nova_senha)
    logger.info("Senha redefinida via token para %s", email)
    return APIResponse(success=True, message="Senha redefinida com sucesso.", data=None)


@router.post(
    "/auth/forgot-password",
    response_model=APIResponse[None],
    summary="Solicitar redefinição de senha (EN)",
    description="Envia um email com link para redefinição de senha. Sempre retorna 200 por segurança.",
)
def forgot_password(payload: SenhaResetRequest, db: Session = Depends(get_db), _=Depends(rate_limit(3, 60))):
    user = UserRepository.get_by_email(db, payload.email)
    if user:
        try:
            token = create_purpose_token(user.email, "password_reset", expire_minutes=60)
            EmailService.enviar_reset_senha(user.email, token)
        except Exception:
            logger.warning("Falha ao enviar email de reset para %s", payload.email)
    return APIResponse(success=True, message="Se o email existir, as instruções foram enviadas.", data=None)


@router.post(
    "/auth/reset-password",
    response_model=APIResponse[None],
    summary="Redefinir senha com token (EN)",
)
def reset_password(payload: ResetPasswordRequest, db: Session = Depends(get_db), _=Depends(rate_limit(5, 60))):
    email = decode_purpose_token(payload.token, "password_reset")
    if not email:
        raise HTTPException(status_code=400, detail="Token inválido ou expirado.")
    user = UserRepository.get_by_email(db, email)
    if not user:
        raise HTTPException(status_code=400, detail="Token inválido ou expirado.")
    UserRepository.update_senha(db, user.id, payload.newPassword)
    logger.info("Senha redefinida via token para %s", email)
    return APIResponse(success=True, message="Senha redefinida com sucesso.", data=None)


@router.patch(
    "/usuarios/senha",
    response_model=APIResponse[None],
    summary="Alterar senha",
    description="Altera a senha do usuário autenticado.",
)
def alterar_senha(
    payload: AlterarSenhaRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    if not verify_password(payload.senha_atual, current_user.senha_hash or ""):
        raise HTTPException(status_code=400, detail="Senha atual incorreta.")
    UserRepository.update_senha(db, current_user.id, payload.nova_senha)
    logger.info("Senha alterada para %s", current_user.email)
    return APIResponse(success=True, message="Senha alterada com sucesso.", data=None)


@router.delete(
    "/usuarios",
    response_model=APIResponse[None],
    summary="Excluir conta",
    description="Exclui permanentemente a conta do usuário autenticado e todos os seus dados.",
)
def deletar_conta(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    token_payload = decode_access_token(credentials.credentials)
    if token_payload:
        try:
            TokenRevogadoRepository.revoke(
                db,
                jti=str(token_payload["jti"]),
                usuario_id=int(token_payload["user_id"]),
                expira_em=token_payload["exp"],
            )
        except IntegrityError:
            db.rollback()

    UserRepository.delete(db, current_user.id)
    logger.info("Conta excluída: %s", current_user.email)
    return APIResponse(success=True, message="Conta excluída com sucesso.", data=None)


@router.put(
    "/usuarios/device-token",
    response_model=APIResponse[None],
    summary="Registrar device token para push notifications",
)
def registrar_device_token(
    payload: DeviceTokenUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    UserRepository.update_device_token(db, current_user.id, payload.device_token)
    return APIResponse(success=True, message="Device token registrado.", data=None)


@router.patch(
    "/usuarios/perfil",
    response_model=APIResponse[UserResponse],
    summary="Atualizar perfil do usuário",
    description="Atualiza avatar e/ou profissão do usuário autenticado.",
)
def atualizar_perfil(
    payload: PerfilUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    dados = payload.model_dump(exclude_unset=True)
    if not dados:
        return APIResponse(success=True, message="Nenhum campo para atualizar.", data=UserResponse.model_validate(current_user))
    user = UserRepository.update_perfil(db, current_user.id, **dados)
    return APIResponse(success=True, message="Perfil atualizado com sucesso.", data=UserResponse.model_validate(user))


_AVATAR_DIR = Path("uploads/avatars")
_TIPOS_PERMITIDOS = {"image/jpeg", "image/png", "image/webp"}
_TAMANHO_MAX_BYTES = 5 * 1024 * 1024  # 5 MB


@router.post(
    "/usuarios/avatar",
    response_model=APIResponse[UserResponse],
    summary="Upload de foto de perfil",
    description="Recebe uma imagem da galeria (JPG/PNG/WebP, máx 5 MB), redimensiona para 400×400 e salva como foto de perfil.",
)
async def upload_avatar(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    if file.content_type not in _TIPOS_PERMITIDOS:
        raise HTTPException(status_code=400, detail="Formato inválido. Envie JPG, PNG ou WebP.")

    contents = await file.read()
    if len(contents) > _TAMANHO_MAX_BYTES:
        raise HTTPException(status_code=400, detail="Imagem muito grande. Máximo permitido: 5 MB.")

    try:
        img = Image.open(BytesIO(contents)).convert("RGB")
        w, h = img.size
        size = min(w, h)
        img = img.crop(((w - size) // 2, (h - size) // 2, (w + size) // 2, (h + size) // 2))
        img = img.resize((400, 400), Image.LANCZOS)
    except Exception:
        raise HTTPException(status_code=400, detail="Arquivo de imagem inválido ou corrompido.")

    _AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    dest = _AVATAR_DIR / f"{current_user.id}.webp"
    buf = BytesIO()
    img.save(buf, format="WEBP", quality=85)
    dest.write_bytes(buf.getvalue())

    avatar_url = f"/uploads/avatars/{current_user.id}.webp"
    user = UserRepository.update_perfil(db, current_user.id, avatar=avatar_url)
    logger.info("Avatar atualizado para usuário %s", current_user.email)
    return APIResponse(success=True, message="Foto de perfil atualizada com sucesso.", data=UserResponse.model_validate(user))


@router.delete(
    "/usuarios/avatar",
    response_model=APIResponse[UserResponse],
    summary="Remover foto de perfil",
    description="Remove a foto de perfil do usuário autenticado.",
)
def remover_avatar(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    dest = _AVATAR_DIR / f"{current_user.id}.webp"
    if dest.exists():
        dest.unlink()
    user = UserRepository.update_perfil(db, current_user.id, avatar=None)
    logger.info("Avatar removido para usuário %s", current_user.email)
    return APIResponse(success=True, message="Foto de perfil removida.", data=UserResponse.model_validate(user))


@router.post(
    "/bonificacoes",
    response_model=APIResponse[BonificacaoResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Criar bonificação",
    description="Registra uma bonificação vinculada a um plantão do usuário autenticado.",
)
def criar_bonificacao(
    payload: BonificacaoCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    plantao = PlantaoRepository.get_by_id_and_user(db, payload.plantao_id, current_user.id)
    if plantao is None:
        raise HTTPException(status_code=404, detail="Plantão não encontrado.")
    bonificacao = BonificacaoRepository.create(db, payload)
    FinanceiroRepository.sync_mes(db, plantao.local_id, plantao.data_inicio.month, plantao.data_inicio.year)
    return APIResponse(success=True, message="Bonificação criada com sucesso.", data=BonificacaoResponse.model_validate(bonificacao))


@router.get(
    "/bonificacoes",
    response_model=APIResponse[list[BonificacaoResponse]],
    summary="Listar bonificações por plantão",
    description="Retorna as bonificações registradas para um plantão do usuário autenticado.",
)
def listar_bonificacoes(
    plantao_id: int = Query(..., description="ID do plantão."),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    plantao = PlantaoRepository.get_by_id_and_user(db, plantao_id, current_user.id)
    if plantao is None:
        raise HTTPException(status_code=404, detail="Plantão não encontrado.")
    bonificacoes = BonificacaoRepository.list_by_plantao(db, plantao_id)
    return APIResponse(success=True, message="Bonificações listadas com sucesso.", data=[BonificacaoResponse.model_validate(b) for b in bonificacoes])


@router.delete(
    "/bonificacoes/{bonificacao_id}",
    response_model=APIResponse[None],
    summary="Deletar bonificação",
    description="Remove uma bonificação registrada.",
)
def deletar_bonificacao(
    bonificacao_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    bonificacao = BonificacaoRepository.get_by_id(db, bonificacao_id)
    if bonificacao is None:
        raise HTTPException(status_code=404, detail="Bonificação não encontrada.")
    plantao = PlantaoRepository.get_by_id_and_user(db, bonificacao.plantao_id, current_user.id)
    if plantao is None:
        raise HTTPException(status_code=403, detail="Sem permissão para deletar esta bonificação.")
    BonificacaoRepository.delete(db, bonificacao_id)
    FinanceiroRepository.sync_mes(db, plantao.local_id, plantao.data_inicio.month, plantao.data_inicio.year)
    return APIResponse(success=True, message="Bonificação deletada com sucesso.", data=None)


@router.patch(
    "/bonificacoes/{bonificacao_id}",
    response_model=APIResponse[BonificacaoResponse],
    summary="Atualizar bonificação",
    description="Atualiza valor e/ou descrição de uma bonificação do usuário autenticado.",
)
def atualizar_bonificacao(
    bonificacao_id: int,
    payload: BonificacaoUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    bonificacao = BonificacaoRepository.get_by_id(db, bonificacao_id)
    if bonificacao is None:
        raise HTTPException(status_code=404, detail="Bonificação não encontrada.")
    plantao = PlantaoRepository.get_by_id_and_user(db, bonificacao.plantao_id, current_user.id)
    if plantao is None:
        raise HTTPException(status_code=403, detail="Sem permissão para editar esta bonificação.")
    dados = payload.model_dump(exclude_unset=True)
    if not dados:
        raise HTTPException(status_code=400, detail="Nenhum campo enviado para atualização.")
    bonificacao = BonificacaoRepository.update(db, bonificacao_id, **dados)
    FinanceiroRepository.sync_mes(db, plantao.local_id, plantao.data_inicio.month, plantao.data_inicio.year)
    return APIResponse(success=True, message="Bonificação atualizada com sucesso.", data=BonificacaoResponse.model_validate(bonificacao))


@router.post(
    "/trocas",
    response_model=APIResponse[TrocaResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Registrar troca",
    description="Registra uma troca de plantão entre dois profissionais vinculada a um local.",
)
def criar_troca(
    payload: TrocaCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    if payload.local_id is not None:
        local = LocalRepository.get_by_id(db, payload.local_id)
        if local is None or local.usuario_id != current_user.id:
            raise HTTPException(status_code=404, detail="Local não encontrado.")
    troca = TrocaRepository.create(db, payload)
    return APIResponse(success=True, message="Troca registrada com sucesso.", data=TrocaResponse.model_validate(troca))


@router.get(
    "/trocas",
    response_model=APIResponse[list[TrocaResponse]],
    summary="Listar trocas",
    description="Retorna trocas por usuário (usuario_id) ou por local (local_id). Um dos dois deve ser informado.",
)
def listar_trocas(
    usuario_id: int | None = Query(default=None, description="ID do usuário — retorna todas as trocas dele."),
    local_id: int | None = Query(default=None, description="ID do local — retorna trocas daquele local."),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    if usuario_id is not None:
        if usuario_id != current_user.id:
            raise HTTPException(status_code=403, detail="Sem permissão para consultar trocas de outro usuário.")
        trocas = TrocaRepository.list_by_usuario(db, usuario_id)
        return APIResponse(success=True, message="Trocas listadas com sucesso.", data=[TrocaResponse.model_validate(t) for t in trocas])

    if local_id is not None:
        local = LocalRepository.get_by_id(db, local_id)
        if local is None or local.usuario_id != current_user.id:
            raise HTTPException(status_code=404, detail="Local não encontrado.")
        trocas = TrocaRepository.list_by_local(db, local_id)
        return APIResponse(success=True, message="Trocas listadas com sucesso.", data=[TrocaResponse.model_validate(t) for t in trocas])

    raise HTTPException(status_code=400, detail="Informe usuario_id ou local_id como parâmetro.")


@router.delete(
    "/trocas/{troca_id}",
    response_model=APIResponse[None],
    summary="Deletar troca",
    description="Remove uma troca registrada.",
)
def deletar_troca(
    troca_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    troca = TrocaRepository.get_by_id(db, troca_id)
    if troca is None:
        raise HTTPException(status_code=404, detail="Troca não encontrada.")
    local = LocalRepository.get_by_id(db, troca.local_id)
    if local is None or local.usuario_id != current_user.id:
        raise HTTPException(status_code=403, detail="Sem permissão para deletar esta troca.")
    TrocaRepository.delete(db, troca_id)
    return APIResponse(success=True, message="Troca deletada com sucesso.", data=None)


@router.post(
    "/feedback",
    response_model=APIResponse[None],
    summary="Enviar feedback",
    description="Envia um feedback do usuário autenticado por e-mail para a equipe de desenvolvimento.",
)
def enviar_feedback(payload: FeedbackCreate, current_user=Depends(get_current_user)):
    """Endpoint que envia feedback do usuário autenticado por email para o time."""
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
    summary="Upgrade para premium (webhook de pagamento)",
    description=(
        "Endpoint chamado pelo gateway de pagamento após confirmação. "
        "Requer o header X-Upgrade-Secret configurado em UPGRADE_WEBHOOK_SECRET no .env."
    ),
)
def upgrade_usuario(
    payload: UpgradeWebhookRequest,
    db: Session = Depends(get_db),
    x_upgrade_secret: str = Header(default="", alias="X-Upgrade-Secret"),
):
    if not settings.upgrade_webhook_secret:
        raise HTTPException(status_code=503, detail="Gateway de pagamento não configurado.")
    if x_upgrade_secret != settings.upgrade_webhook_secret:
        raise HTTPException(status_code=401, detail="Secret inválido.")

    user = UserRepository.get_by_id(db, payload.usuario_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")

    atualizado = PlanoService.upgrade_para_premium(user)
    db.add(user)
    db.commit()
    db.refresh(user)

    logger.info("Plano atualizado para premium: %s (id=%s)", user.email, user.id)
    return APIResponse(
        success=True,
        message="Usuário atualizado para premium" if atualizado else "Usuário já possui plano premium",
        data=UserResponse.model_validate(user),
    )


@router.post(
    "/locais",
    response_model=APIResponse[LocalResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Criar local",
    description="Cria um local para o usuário autenticado. O usuário não pode ser informado no corpo da requisição.",
)
def criar_local(payload: LocalCreate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    local_data = payload.model_dump()
    local_data["usuario_id"] = current_user.id
    local = LocalRepository.create(db, LocalCreateWithUsuario.model_validate(local_data))
    logger.info("Local criado para usuário %s: %s", current_user.email, local.nome_local)
    return APIResponse(success=True, message="Local criado com sucesso.", data=LocalResponse.model_validate(local))


@router.get(
    "/locais",
    response_model=APIResponse[list[LocalResponse]],
    summary="Listar locais",
    description="Retorna os locais cadastrados pelo usuário autenticado.",
)
def listar_locais(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    locais = LocalRepository.list_by_user(db, current_user.id)
    return APIResponse(success=True, message="Locais listados com sucesso.", data=[LocalResponse.model_validate(l) for l in locais])


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
    "/financeiro",
    response_model=APIResponse[FinanceiroRecordResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Criar ou atualizar registro financeiro",
    description="Cria ou atualiza o registro financeiro de um local para o mês/ano da data informada (upsert). Armazena valor total e data de pagamento.",
)
def criar_financeiro(
    payload: FinanceiroCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    local = LocalRepository.get_by_id(db, payload.local_id)
    if local is None or local.usuario_id != current_user.id:
        raise HTTPException(status_code=404, detail="Local não encontrado.")
    registro = FinanceiroRepository.upsert(db, payload.local_id, float(payload.valor), payload.data, payload.em_atraso)
    return APIResponse(success=True, message="Registro financeiro salvo.", data=FinanceiroRecordResponse.model_validate(registro))


@router.get(
    "/financeiro/local/{local_id}",
    response_model=APIResponse[list[FinanceiroRecordResponse]],
    summary="Listar registros financeiros de um local",
    description="Retorna todos os registros financeiros de um local, ordenados por data decrescente.",
)
def listar_financeiro_por_local(
    local_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    local = LocalRepository.get_by_id(db, local_id)
    if local is None or local.usuario_id != current_user.id:
        raise HTTPException(status_code=404, detail="Local não encontrado.")
    registros = FinanceiroRepository.list_by_local(db, local_id)
    return APIResponse(success=True, message="Registros financeiros listados.", data=[FinanceiroRecordResponse.model_validate(r) for r in registros])


@router.patch(
    "/financeiro/{financeiro_id}/em-atraso",
    response_model=APIResponse[FinanceiroRecordResponse],
    summary="Atualizar status de atraso",
    description="Marca ou desmarca um registro financeiro como em atraso.",
)
def atualizar_em_atraso(
    financeiro_id: int,
    payload: FinanceiroEmAtrasoUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    registro = FinanceiroRepository.get_by_id(db, financeiro_id)
    if registro is None:
        raise HTTPException(status_code=404, detail="Registro financeiro não encontrado.")
    local = LocalRepository.get_by_id(db, registro.local_id)
    if local is None or local.usuario_id != current_user.id:
        raise HTTPException(status_code=404, detail="Registro financeiro não encontrado.")
    registro = FinanceiroRepository.update_em_atraso(db, registro, payload.em_atraso)
    return APIResponse(success=True, message="Status de atraso atualizado.", data=FinanceiroRecordResponse.model_validate(registro))


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

    inicio_dt = payload.data_inicio
    fim_dt = ajustar_fim_intervalo(inicio_dt, payload.data_fim)

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
                horario_fim=payload.data_inicio.time()
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
                usuario_id=current_user.id,
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
            ),
        )
        FinanceiroRepository.sync_mes(db, plantao.local_id, plantao.data_inicio.month, plantao.data_inicio.year)
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
    response_model=APIResponse[list[PlantaoResponse]],
    summary="Listar plantões",
    description="Retorna os plantões do usuário autenticado.",
)
def listar_plantoes(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    plantoes = PlantaoRepository.list_by_user(db, current_user.id)
    return APIResponse(
        success=True,
        message="Plantões listados com sucesso.",
        data=[PlantaoResponse.model_validate(p) for p in plantoes],
    )


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

    if payload.pagamento_plantao is not None:
        nova_data_pagamento = payload.pagamento_plantao
    else:
        nova_data_pagamento = _calcular_data_pagamento(inicio_dt.date())
    dados_atualizacao["pagamento_plantao"] = nova_data_pagamento

    conflito_relevante = any(k in dados_atualizacao for k in ("data_inicio", "data_fim"))
    if conflito_relevante and aplicar_em == "somente_este":
        conflito = PlantaoRepository.get_conflito(
            db,
            usuario_id=current_user.id,
            inicio=inicio_dt,
            fim=fim_dt,
            excluir_id=plantao_id,
        )
        if conflito:
            raise HTTPException(
                status_code=409,
                detail="Conflito de horário detectado com outro plantão existente.",
            )

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
            plantao = PlantaoRepository.update_by_user(db, plantao_id, current_user.id, **_decompor_datetimes(dados_atualizacao))
            if plantao is None:
                raise HTTPException(status_code=404, detail="Plantão não encontrado")
        else:
            plantoes_futuros = PlantaoRepository.list_futuros_da_serie(db, plantao_atual)
            for _p_fut in plantoes_futuros:
                _meses_sync.add((_p_fut.local_id, _p_fut.data_inicio.month, _p_fut.data_inicio.year))
            if not plantoes_futuros:
                raise HTTPException(status_code=404, detail="Série de plantões não encontrada")

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
                PlanoService.verificar_limite_serie(db, current_user, novos_count)

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
                            usuario_id=current_user.id,
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
                PlanoService.verificar_limite_serie(db, current_user, novos_count2)

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
                        usuario_id=current_user.id,
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
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="Erro ao atualizar plantão") from exc

    logger.info("Plantão %s atualizado por %s", plantao_id, current_user.email)
    _meses_sync.add((plantao.local_id, plantao.data_inicio.month, plantao.data_inicio.year))
    for _p_novo in _novos_plantoes_criados:
        _meses_sync.add((_p_novo.local_id, _p_novo.data_inicio.month, _p_novo.data_inicio.year))
    for _local_s, _mes_s, _ano_s in _meses_sync:
        FinanceiroRepository.sync_mes(db, _local_s, _mes_s, _ano_s)
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
    plantao_del = PlantaoRepository.get_by_id_and_user(db, plantao_id, current_user.id)
    if plantao_del is None:
        raise HTTPException(status_code=404, detail="Plantão não encontrado")
    _sync_del = (plantao_del.local_id, plantao_del.data_inicio.month, plantao_del.data_inicio.year)
    PlantaoRepository.delete_by_user(db, plantao_id, current_user.id)
    FinanceiroRepository.sync_mes(db, *_sync_del)

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
    "/financeiro/grafico-anual",
    response_model=APIResponse[list[GraficoMensalItem]],
    summary="Gráfico financeiro anual (12 meses)",
    description="Retorna o total e quantidade de plantões mês a mês (jan-dez), incluindo meses futuros com agendamentos já criados.",
)
def obter_grafico_anual(
    ano: int = Query(..., ge=1, description="Ano do relatório."),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    PlanoService.exigir_premium(current_user)
    meses = FinanceiroService.calcular_grafico_anual(db, current_user.id, ano)
    return APIResponse(
        success=True,
        message="Gráfico anual gerado com sucesso.",
        data=[GraficoMensalItem.model_validate(m) for m in meses],
    )


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


@router.post(
    "/plantoes/{plantao_id}/transferir",
    response_model=APIResponse[TransferenciaResponse],
    status_code=status.HTTP_200_OK,
    summary="Transferir plantão",
    description=(
        "Transfere um plantão para outra pessoa informando nome e telefone. "
        "O plantão sai imediatamente da lista do remetente. "
        "Se o destinatário for um usuário cadastrado, a transferência fica pendente até ele aceitar ou recusar. "
        "Caso contrário, apenas um SMS é enviado e a transferência é concluída."
    ),
)
def transferir_plantao(
    plantao_id: int,
    payload: TransferenciaCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Endpoint para transferir um plantão para outro usuário ou telefone via SMS."""
    plantao = PlantaoRepository.get_by_id_and_user(db, plantao_id, current_user.id)
    if plantao is None:
        raise HTTPException(status_code=404, detail="Plantão não encontrado")

    destinatario_usuario = UserRepository.get_by_phone(db, payload.destinatario_telefone)
    destinatario_usuario_id = destinatario_usuario.id if destinatario_usuario else None

    if destinatario_usuario_id == current_user.id:
        raise HTTPException(status_code=400, detail="Não é possível transferir um plantão para si mesmo")

    local = LocalRepository.get_by_id(db, plantao.local_id)
    nome_local = local.nome_local if local else "local não informado"

    data_str = plantao.data_inicio.strftime('%d/%m/%Y')
    if plantao.data_fim != plantao.data_inicio:
        data_str += f" até {plantao.data_fim.strftime('%d/%m/%Y')}"
    mensagem = (
        f"Olá {payload.destinatario_nome}! {current_user.nome} transferiu um plantão para você.\n"
        f"Data: {data_str}\n"
        f"Local: {nome_local}\n"
        f"Horário: {plantao.horario_inicio.strftime('%H:%M')} às {plantao.horario_fim.strftime('%H:%M')}\n"
        f"Valor: R$ {float(plantao.valor):.2f}"
    )

    # Destinatário cadastrado → pendente (aguarda aceitar/recusar)
    # Destinatário externo → enviado (apenas SMS, sem confirmação)
    status_inicial = "pendente" if destinatario_usuario_id else "enviado"

    try:
        plantao.transferido = True
        db.add(plantao)
        transferencia = TransferenciaRepository.create(
            db,
            plantao_id=plantao.id,
            remetente_id=current_user.id,
            destinatario_usuario_id=destinatario_usuario_id,
            destinatario_nome=payload.destinatario_nome,
            destinatario_telefone=payload.destinatario_telefone,
            status=status_inicial,
            canal=payload.canal,
        )
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="Erro ao registrar transferência") from exc

    # Envio de mensagem não reverte a transferência em caso de falha
    sms_enviado = SmsService.enviar(payload.destinatario_telefone, mensagem, canal=payload.canal)
    if sms_enviado:
        TransferenciaRepository.marcar_sms_enviado(db, transferencia.id)
        transferencia.sms_enviado = True

    if destinatario_usuario_id:
        mensagem_retorno = "Plantão transferido. O destinatário precisa aceitar no aplicativo para confirmar."
    else:
        mensagem_retorno = "Plantão transferido com sucesso."
    if not sms_enviado:
        mensagem_retorno += " A notificação por mensagem não pôde ser enviada, mas a transferência foi registrada."

    logger.info(
        "Plantão %s transferido por %s para %s (%s). Status: %s. SMS enviado: %s",
        plantao_id,
        current_user.email,
        payload.destinatario_nome,
        payload.destinatario_telefone,
        status_inicial,
        sms_enviado,
    )

    return APIResponse(
        success=True,
        message=mensagem_retorno,
        data=TransferenciaResponse.model_validate(transferencia),
    )


@router.get(
    "/transferencias/recebidas",
    response_model=APIResponse[list[TransferenciaResponse]],
    status_code=status.HTTP_200_OK,
    summary="Listar transferências recebidas pendentes",
    description="Lista os plantões transferidos por outros usuários para o usuário autenticado que ainda aguardam aceitar ou recusar.",
)
def listar_transferencias_recebidas(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    transferencias = TransferenciaRepository.list_recebidas_por_usuario(db, current_user.id)
    return APIResponse(
        success=True,
        message="Transferências recebidas listadas com sucesso.",
        data=[TransferenciaResponse.model_validate(t) for t in transferencias],
    )


@router.post(
    "/transferencias/{transferencia_id}/aceitar",
    response_model=APIResponse[PlantaoResponse],
    status_code=status.HTTP_200_OK,
    summary="Aceitar transferência de plantão",
    description=(
        "O destinatário aceita o plantão transferido. "
        "Um novo plantão é criado na conta do destinatário e passa a somar no saldo mensal. "
        "É necessário informar um local_id da própria conta do destinatário."
    ),
)
def aceitar_transferencia(
    transferencia_id: int,
    payload: TransferenciaAceitar,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    transferencia = TransferenciaRepository.get_by_id_and_destinatario(db, transferencia_id, current_user.id)
    if transferencia is None:
        raise HTTPException(status_code=404, detail="Transferência não encontrada ou não está pendente")

    local = LocalRepository.get_by_id(db, payload.local_id)
    if local is None or local.usuario_id != current_user.id:
        raise HTTPException(status_code=404, detail="Local não encontrado ou não pertence ao usuário")

    plantao_original = transferencia.plantao
    if plantao_original is None:
        raise HTTPException(status_code=404, detail="Plantão original não encontrado")

    pagamento_plantao = None
    if local.dia_pagamento is not None:
        pagamento_plantao = PagamentoService.calcular_data_pagamento_plantao(
            data_plantao=plantao_original.data_inicio,
            indice_dia_pagamento=local.dia_pagamento,
            data_inicio_ciclo=local.data_inicio,
            data_final_ciclo=local.data_final,
            util_corrido=local.util_corrido,
        )

    try:
        novo_plantao = Plantao(
            usuario_id=current_user.id,
            local_id=payload.local_id,
            valor=plantao_original.valor,
            data_inicio=plantao_original.data_inicio,
            data_fim=plantao_original.data_fim,
            horario_inicio=plantao_original.horario_inicio,
            horario_fim=plantao_original.horario_fim,
            recorrencia=None,
            sobreaviso=plantao_original.sobreaviso,
            notificacao_ativa=True,
            notificacao_antecedencia_minutos=60,
            executado=False,
            pagamento_plantao=pagamento_plantao,
            transferido=False,
            recebido_via_transferencia=True,
        )
        db.add(novo_plantao)
        TransferenciaRepository.atualizar_status(db, transferencia_id, "aceito")
        db.commit()
        db.refresh(novo_plantao)
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="Erro ao aceitar transferência") from exc

    logger.info(
        "Transferência %s aceita por %s. Novo plantão %s criado.",
        transferencia_id,
        current_user.email,
        novo_plantao.id,
    )

    return APIResponse(
        success=True,
        message="Transferência aceita. O plantão foi adicionado à sua agenda.",
        data=PlantaoResponse.model_validate(novo_plantao),
    )


@router.post(
    "/transferencias/{transferencia_id}/recusar",
    response_model=APIResponse[None],
    status_code=status.HTTP_200_OK,
    summary="Recusar transferência de plantão",
    description=(
        "O destinatário recusa o plantão transferido. "
        "O plantão volta a aparecer na agenda do remetente."
    ),
)
def recusar_transferencia(
    transferencia_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    transferencia = TransferenciaRepository.get_by_id_and_destinatario(db, transferencia_id, current_user.id)
    if transferencia is None:
        raise HTTPException(status_code=404, detail="Transferência não encontrada ou não está pendente")

    plantao_original = transferencia.plantao

    try:
        if plantao_original is not None:
            plantao_original.transferido = False
            db.add(plantao_original)
        TransferenciaRepository.atualizar_status(db, transferencia_id, "recusado")
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="Erro ao recusar transferência") from exc

    logger.info(
        "Transferência %s recusada por %s. Plantão %s revertido.",
        transferencia_id,
        current_user.email,
        transferencia.plantao_id,
    )

    return APIResponse(success=True, message="Transferência recusada. O plantão foi devolvido ao remetente.", data=None)
