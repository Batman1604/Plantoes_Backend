import hmac
import logging
import secrets
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from app.core.mascaras import mascarar_email, mascarar_telefone
from app.core.rate_limiter import rate_limit, rate_limit_por_usuario
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from app.core.database import get_db
from app.models import AmigoConfiguracao, Plantao, SolicitacaoTroca, Troca, User, normalizar_email
from app.core.security import (
    IDADE_MAXIMA_DA_SESSAO,
    create_access_token,
    create_purpose_token,
    decode_access_token,
    decode_purpose_token,
    hash_password,
    verify_password,
)
from app.repositories.plantao_repository import (
    AmigoConfiguracaoRepository,
    AmizadeRepository,
    CadastroPendenteRepository,
    CodigoResetSenhaRepository,
    FinanceiroRepository,
    LocalRepository,
    PlantaoRepository,
    SolicitacaoTrocaRepository,
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
    AmigoConfiguracaoResponse,
    AmigoConfiguracaoUpdate,
    AmigoPlantaoItem,
    AmizadeAmigoResumo,
    AmizadeCreate,
    AmizadeListItem,
    AmizadeResponse,
    AppleAuthRequest,
    ConfirmarCadastroRequest,
    FinanceiroCreate,
    FinanceiroEmAtrasoUpdate,
    FinanceiroRecordResponse,
    PerfilUpdate,
    AuthResponse,
    AuthStatusResponse,
    ExcluirContaRequest,
    FeedbackCreate,
    FinanceiroData,
    GoogleAuthRequest,
    GraficoMensalItem,
    UpgradeWebhookRequest,
    IniciarCadastroRequest,
    LocalCreate,
    LocalResponse,
    LocalUpdate,
    PlantaoCreate,
    PlantaoExecutadoUpdate,
    PlantaoResponse,
    PlantaoUpdate,
    ReenviarCadastroRequest,
    ResetPasswordRequest,
    SenhaResetRequest,
    VerificarCodigoSenhaRequest,
    SolicitacaoTrocaCreate,
    SolicitacaoTrocaListItem,
    SolicitacaoTrocaResponse,
    PessoaTrocaResumo,
    TransferenciaAceitar,
    TransferenciaCreate,
    TransferenciaResponse,
    BonificacaoCreate,
    BonificacaoResponse,
    BonificacaoUpdate,
    TrocaCreate,
    TrocaOperacaoCreate,
    TrocaOperacaoData,
    TrocaResponse,
    UsuarioResumo,
    UserLogin,
    UserResponse,
    VerificarTelefoneRequest,
)
from app.services.email_service import EmailService
from app.services.feedback_service import FeedbackService
from app.services.financeiro_service import FinanceiroService
from app.core.config import settings
from app.services.oauth_service import OAuthService, OAuthTokenError, parse_bool_claim
from app.services.pagamento_service import PagamentoService
from app.services.plano_service import PlanoService
from app.services.bonificacao_service import BonificacaoService
from app.services.erros import ErroDeDominio
from app.services.troca_service import TrocaService
from app.services.local_service import LocalService
from app.services.plantao_service import PlantaoService
# A-13 (4/4): a serialização morava aqui até o SyncService precisar dela; agora
# rota e sync importam do mesmo módulo de serviço.
from app.services.serializacao import (
    _alvos_com_valor_oculto,
    _plantao_com_troca,
    _plantao_recebido_troca_resumo,
    _plantao_troca_resumo,
    _plantoes_com_troca,
    _serializar_amigo_configuracao,
)
from app.services.sms_service import SmsService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["plantoes"])
security = HTTPBearer(auto_error=False)


def http_de_erro_de_dominio(erro: ErroDeDominio) -> HTTPException:
    """Traduz uma recusa da regra de negócio para HTTP.

    É o único ponto onde erro de domínio vira status HTTP. Os serviços não
    conhecem FastAPI; as rotas não conhecem regra de negócio.
    """
    return HTTPException(status_code=erro.status_http, detail=erro.mensagem)


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

    if _sessao_invalidada(user, token_payload["iat"]):
        raise unauthorized_exception()

    return user


def _sessao_invalidada(user: User, emitido_em: float | None) -> bool:
    """AUD-031: token emitido antes da última troca/redefinição de senha.
    Token sem `iat` (anterior a esta versão) também cai, se houve troca."""
    limite = user.sessoes_invalidas_antes_de
    if limite is None:
        return False
    if limite.tzinfo is None:
        # SQLite (testes) não preserva o fuso ao ler a coluna de volta.
        limite = limite.replace(tzinfo=UTC)
    return emitido_em is None or emitido_em < limite.timestamp()



# AUD-011: POST /api/usuarios (cadastro legado) foi removido por decisão do
# usuário. Ele criava a conta na hora, sem verificar telefone nem e-mail: dava
# para "ocupar" o número ou o e-mail de outro médico e receber as
# transferências feitas para ele. O único cadastro é /auth/cadastro +
# /auth/confirmar-cadastro.


_FALHA_NO_ENVIO = "Não foi possível enviar o código de verificação. Tente novamente."


def _disparar_verificacao_telefone(db: Session, usuario_id: int, telefone: str) -> bool:
    """Devolve se o código foi enviado (C-10). Antes o resultado do envio era
    ignorado e a exceção engolida: a rota dizia "código reenviado" sem que
    nada tivesse saído."""
    codigo = f"{secrets.randbelow(1_000_000):06d}"
    expira_em = datetime.now(UTC) + timedelta(minutes=1)
    VerificacaoTelefoneRepository.upsert(db, usuario_id, codigo, expira_em)
    try:
        enviado = SmsService.enviar_codigo_verificacao(telefone, codigo)
    except Exception:
        logger.exception("Falha ao enviar código de verificação para %s", mascarar_telefone(telefone))
        return False
    if enviado:
        logger.info("Código de verificação enviado para %s", mascarar_telefone(telefone))
    return enviado


def _conflitos_cadastro(db: Session, email: str, telefone: str | None) -> list[str]:
    conflitos = []
    if UserRepository.get_by_email(db, email):
        conflitos.append("Email")
    if telefone and UserRepository.get_by_phone(db, telefone):
        conflitos.append("Telefone")
    return conflitos


def _detalhe_conflito(conflitos: list[str]) -> str:
    return " e ".join(conflitos) + (" já cadastrado" if len(conflitos) == 1 else " já cadastrados")


@router.post(
    "/auth/cadastro",
    response_model=APIResponse[None],
    summary="Iniciar cadastro",
    description=(
        "Primeiro passo do cadastro: valida os dados, envia um código de verificação por "
        "WhatsApp/SMS e NÃO cria a conta ainda. A conta só é criada em /auth/confirmar-cadastro, "
        "depois que o código é confirmado no app."
    ),
)
def iniciar_cadastro(payload: IniciarCadastroRequest, db: Session = Depends(get_db), _=Depends(rate_limit(5, 60))):
    conflitos = _conflitos_cadastro(db, payload.email, payload.telefone)
    if conflitos:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_detalhe_conflito(conflitos))

    codigo = f"{secrets.randbelow(1_000_000):06d}"
    expira_em = datetime.now(UTC) + timedelta(minutes=1)
    try:
        CadastroPendenteRepository.upsert(
            db,
            nome=payload.nome,
            email=payload.email,
            telefone=payload.telefone,
            senha_hash=hash_password(payload.senha),
            codigo=codigo,
            expira_em=expira_em,
        )
        enviado = SmsService.enviar_codigo_verificacao(payload.telefone, codigo)
    except Exception as exc:
        db.rollback()
        # AUD-033: o tipo, não a mensagem — a de um erro de banco traz os
        # parâmetros do INSERT (e-mail, telefone, hash da senha).
        logger.warning("Falha ao iniciar cadastro para %s: %s", mascarar_email(payload.email), type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Não foi possível enviar o código de verificação. Tente novamente.",
        ) from exc

    if not enviado:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Não foi possível enviar o código de verificação. Tente novamente.",
        )

    logger.info("Cadastro iniciado, código de verificação enviado para %s", mascarar_telefone(payload.telefone))
    return APIResponse(success=True, message="Código enviado para o seu WhatsApp.", data=None)


@router.post(
    "/auth/confirmar-cadastro",
    response_model=APIResponse[AuthResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Confirmar cadastro",
    description="Confirma o código de 6 dígitos e cria a conta, retornando o token de acesso para uso imediato.",
)
def confirmar_cadastro(
    payload: ConfirmarCadastroRequest, db: Session = Depends(get_db), _=Depends(rate_limit(5, 120))
):
    # Contador de tentativas (padrão do AUD-032): depois de 5 erros, nem o
    # código certo vale; o limite por IP sozinho deixava adivinhar os 6 dígitos.
    pendente = CadastroPendenteRepository.conferir(db, payload.email, payload.codigo)
    if pendente is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Código inválido ou expirado.")

    conflitos = _conflitos_cadastro(db, pendente.email, pendente.telefone)
    if conflitos:
        CadastroPendenteRepository.delete_by_email(db, pendente.email)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_detalhe_conflito(conflitos))

    try:
        user = UserRepository.create_a_partir_de_pendente(db, pendente)
    except Exception as exc:
        db.rollback()
        # AUD-033: o tipo, não a mensagem (ver iniciar_cadastro).
        logger.warning("Falha ao concluir cadastro de %s: %s", mascarar_email(pendente.email), type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Não foi possível concluir o cadastro. Tente novamente.",
        ) from exc

    CadastroPendenteRepository.delete_by_email(db, pendente.email)
    logger.info("Cadastro confirmado, usuário criado: %s", mascarar_email(user.email))

    try:
        token_verificacao = create_purpose_token(user.email, "email_verification", expire_minutes=24 * 60)
        EmailService.enviar_verificacao(user.email, token_verificacao)
    except Exception:
        logger.warning("Falha ao enviar email de verificação para %s", mascarar_email(user.email))

    access_token = create_access_token(user.id)
    return APIResponse(
        success=True,
        message="Cadastro confirmado com sucesso.",
        data=AuthResponse(autenticado=True, access_token=access_token, usuario=UserResponse.model_validate(user)),
    )


@router.post(
    "/auth/reenviar-cadastro",
    response_model=APIResponse[None],
    summary="Reenviar código de cadastro",
    description="Gera e envia um novo código de 6 dígitos via WhatsApp para um cadastro ainda não confirmado.",
)
def reenviar_cadastro(
    payload: ReenviarCadastroRequest, db: Session = Depends(get_db), _=Depends(rate_limit(3, 60))
):
    pendente = CadastroPendenteRepository.get_by_email(db, payload.email)
    if pendente is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cadastro não encontrado ou já confirmado.")

    codigo = f"{secrets.randbelow(1_000_000):06d}"
    expira_em = datetime.now(UTC) + timedelta(minutes=1)
    try:
        enviado = SmsService.enviar_codigo_verificacao(pendente.telefone, codigo)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Não foi possível enviar o código de verificação. Tente novamente.",
        ) from exc

    if not enviado:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Não foi possível enviar o código de verificação. Tente novamente.",
        )

    CadastroPendenteRepository.upsert(
        db,
        nome=pendente.nome,
        email=pendente.email,
        telefone=pendente.telefone,
        senha_hash=pendente.senha_hash,
        codigo=codigo,
        expira_em=expira_em,
    )
    return APIResponse(success=True, message="Código reenviado para o seu WhatsApp.", data=None)


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
    logger.info("Login realizado: %s", mascarar_email(user.email))
    return APIResponse(
        success=True,
        message="Login realizado com sucesso.",
        data=AuthResponse(autenticado=True, access_token=access_token, usuario=UserResponse.model_validate(user)),
    )


@router.post(
    "/auth/google",
    response_model=APIResponse[AuthResponse],
    summary="Autenticar com Google",
    description=(
        "Valida o ID Token emitido pelo Google Sign-In, localiza a conta pelo google_id, "
        "vincula a uma conta existente com o mesmo email (só se o Google confirma que o "
        "email é verificado) ou cria uma conta nova sem senha. Retorna o mesmo tipo de "
        "token de acesso usado pelo login por email/senha — não existe uma sessão separada."
    ),
)
def autenticar_google(payload: GoogleAuthRequest, db: Session = Depends(get_db), _=Depends(rate_limit(10, 60))):
    try:
        claims = OAuthService.verificar_token_google(payload.id_token)
    except OAuthTokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    email = claims.get("email")
    if not email:
        raise HTTPException(status_code=422, detail="O token do Google não contém um email.")

    try:
        user, criado = UserRepository.find_or_create_google(
            db,
            google_id=claims["sub"],
            email=email,
            nome=claims.get("name") or email.split("@")[0],
            avatar=claims.get("picture"),
            email_verificado=parse_bool_claim(claims.get("email_verified", False)),
        )
    except ValueError:
        raise HTTPException(
            status_code=409,
            detail=(
                "Já existe uma conta com este email, mas o Google não confirmou que você é "
                "o dono dela. Entre com email e senha; a vinculação com o Google poderá ser "
                "feita depois, autenticado, pelo app."
            ),
        )

    logger.info("%s via Google: %s", "Usuário criado" if criado else "Login", mascarar_email(user.email))
    access_token = create_access_token(user.id)
    return APIResponse(
        success=True,
        message="Autenticado com Google.",
        data=AuthResponse(autenticado=True, access_token=access_token, usuario=UserResponse.model_validate(user)),
    )


@router.post(
    "/auth/apple",
    response_model=APIResponse[AuthResponse],
    summary="Autenticar com Apple",
    description=(
        "Valida o Identity Token emitido pelo Sign in with Apple, localiza a conta pelo "
        "apple_id, vincula a uma conta existente com o mesmo email (só se a Apple confirma "
        "que o email é verificado) ou cria uma conta nova sem senha. A Apple só devolve o "
        "email dentro do token; se ele não vier (comum em logins após o primeiro) e não "
        "houver conta vinculada ainda, retorna 422 — o app precisa reenviar o email "
        "capturado na primeira autorização."
    ),
)
def autenticar_apple(payload: AppleAuthRequest, db: Session = Depends(get_db), _=Depends(rate_limit(10, 60))):
    try:
        claims = OAuthService.verificar_token_apple(payload.identity_token)
    except OAuthTokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    apple_id = claims["sub"]
    email = claims.get("email")

    if not email:
        usuario_existente = db.scalar(select(User).where(User.apple_id == apple_id))
        if usuario_existente is None:
            raise HTTPException(
                status_code=422,
                detail="A Apple não enviou o email neste login. Reenvie o email capturado na primeira autorização.",
            )

    try:
        user, criado = UserRepository.find_or_create_apple(
            db,
            apple_id=apple_id,
            email=email or f"{apple_id}@privaterelay.apple.local",
            nome=payload.nome or (email.split("@")[0] if email else "Usuário Apple"),
            email_verificado=parse_bool_claim(claims.get("email_verified", False)),
        )
    except ValueError:
        raise HTTPException(
            status_code=409,
            detail=(
                "Já existe uma conta com este email, mas a Apple não confirmou que você é o "
                "dono dela. Entre com email e senha; a vinculação com a Apple poderá ser "
                "feita depois, autenticado, pelo app."
            ),
        )

    logger.info("%s via Apple: %s", "Usuário criado" if criado else "Login", mascarar_email(user.email))
    access_token = create_access_token(user.id)
    return APIResponse(
        success=True,
        message="Autenticado com Apple.",
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
    # AUD-031: a cadeia de renovação tem idade máxima desde o login original.
    # Token anterior a esta versão (sem auth_time) começa a contar agora.
    agora = datetime.now(UTC).timestamp()
    autenticado_em = token_payload["auth_time"] if token_payload["auth_time"] is not None else agora
    if agora - autenticado_em > IDADE_MAXIMA_DA_SESSAO.total_seconds():
        raise unauthorized_exception()

    try:
        TokenRevogadoRepository.revoke(
            db,
            jti=str(token_payload["jti"]),
            usuario_id=int(token_payload["user_id"]),
            expira_em=token_payload["exp"],
        )
    except IntegrityError:
        db.rollback()

    new_token = create_access_token(current_user.id, autenticado_em=autenticado_em)
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
    token = create_purpose_token(current_user.email, "email_verification", expire_minutes=24 * 60)
    try:
        enviado = EmailService.enviar_verificacao(current_user.email, token)
    except Exception:
        logger.exception("Falha ao reenviar verificação para %s", mascarar_email(current_user.email))
        enviado = False
    if not enviado:
        # C-10: mesmo tratamento do cadastro — sem envio, não há "enviado".
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Não foi possível enviar o email de verificação. Tente novamente.",
        )
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

    verificacao = VerificacaoTelefoneRepository.conferir(db, current_user.id, payload.codigo)
    if verificacao is None:
        raise HTTPException(status_code=400, detail="Código inválido ou expirado.")

    current_user.telefone_verificado = True
    db.add(current_user)
    VerificacaoTelefoneRepository.delete_by_user(db, current_user.id)
    db.commit()

    logger.info("Telefone verificado para usuário %s", mascarar_email(current_user.email))
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

    if not _disparar_verificacao_telefone(db, current_user.id, current_user.telefone):
        # C-10: mesmo tratamento do cadastro.
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=_FALHA_NO_ENVIO)
    return APIResponse(success=True, message="Código reenviado para o seu WhatsApp.", data=None)


def _disparar_codigo_reset_senha(db: Session, user: User) -> bool:
    """Devolve se o código chegou a algum canal (C-10). Fora de produção, sem
    SMTP, o EmailService registra o código no log (modo de desenvolvimento) e
    isso conta como entregue — é como se testa o fluxo no aparelho."""
    codigo = f"{secrets.randbelow(1_000_000):06d}"
    expira_em = datetime.now(UTC) + timedelta(minutes=1)
    CodigoResetSenhaRepository.upsert(db, user.id, codigo, expira_em)
    try:
        return EmailService.enviar_reset_senha(user.email, codigo)
    except Exception:
        logger.exception("Falha ao enviar código de reset de senha para %s", mascarar_email(user.email))
        return False


_EMAIL_NAO_CADASTRADO = "Não encontramos cadastro com este email. Verifique se digitou certo."


# AUD-032: as rotas duplicadas em português (/auth/esqueci-senha e
# /auth/redefinir-senha) foram removidas. Cada rota que testava o mesmo código
# tinha o próprio orçamento no limitador; o app usa forgot-password,
# verificar-codigo-senha e reset-password, e o site usa reset-password.


@router.post(
    "/auth/forgot-password",
    response_model=APIResponse[None],
    summary="Solicitar redefinição de senha (EN)",
    description="Envia um email com código de 6 dígitos para redefinição de senha. Retorna 404 se o email não estiver cadastrado.",
)
def forgot_password(payload: SenhaResetRequest, db: Session = Depends(get_db), _=Depends(rate_limit(3, 60))):
    user = UserRepository.get_by_email(db, payload.email)
    if not user:
        raise HTTPException(status_code=404, detail=_EMAIL_NAO_CADASTRADO)
    if not _disparar_codigo_reset_senha(db, user):
        # C-10: mesmo tratamento do cadastro.
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=_FALHA_NO_ENVIO)
    return APIResponse(success=True, message="Código enviado para o seu email.", data=None)


@router.post(
    "/auth/verificar-codigo-senha",
    response_model=APIResponse[None],
    summary="Verificar código de redefinição de senha",
    description="Confirma se o código de 6 dígitos enviado por email é válido, sem consumi-lo ainda.",
)
def verificar_codigo_senha(
    payload: VerificarCodigoSenhaRequest, db: Session = Depends(get_db), _=Depends(rate_limit(5, 120))
):
    user = UserRepository.get_by_email(db, payload.email)
    if not user or CodigoResetSenhaRepository.conferir(db, user.id, payload.code) is None:
        raise HTTPException(status_code=400, detail="Código inválido ou expirado.")
    return APIResponse(success=True, message="Código válido.", data=None)


@router.post(
    "/auth/reset-password",
    response_model=APIResponse[None],
    summary="Redefinir senha com código (EN)",
)
def reset_password(payload: ResetPasswordRequest, db: Session = Depends(get_db), _=Depends(rate_limit(5, 60))):
    user = UserRepository.get_by_email(db, payload.email)
    if not user or CodigoResetSenhaRepository.conferir(db, user.id, payload.code) is None:
        raise HTTPException(status_code=400, detail="Código inválido ou expirado.")
    UserRepository.update_senha(db, user.id, payload.newPassword)
    CodigoResetSenhaRepository.delete_by_user(db, user.id)
    logger.info("Senha redefinida via código para %s", mascarar_email(user.email))
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
    logger.info("Senha alterada para %s", mascarar_email(current_user.email))
    return APIResponse(success=True, message="Senha alterada com sucesso.", data=None)


@router.delete(
    "/usuarios",
    response_model=APIResponse[None],
    summary="Excluir conta",
    description="Exclui permanentemente a conta do usuário autenticado e todos os seus dados.",
)
def deletar_conta(
    payload: ExcluirContaRequest = ExcluirContaRequest(),
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    # Contas com senha (email/senha, ou Google/Apple que também definiram senha) exigem
    # confirmação da senha atual antes de excluir — um token JWT sozinho (ex.: roubado de um
    # dispositivo desbloqueado) não deveria bastar para apagar a conta permanentemente.
    # Contas 100% OAuth (senha_hash nulo) não têm senha para confirmar; o próprio token já é
    # a única credencial que existe para elas.
    if current_user.senha_hash:
        if not payload.senha_atual or not verify_password(payload.senha_atual, current_user.senha_hash):
            raise HTTPException(status_code=400, detail="Senha atual incorreta.")

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
    logger.info("Conta excluída: %s", mascarar_email(current_user.email))
    return APIResponse(success=True, message="Conta excluída com sucesso.", data=None)


@router.patch(
    "/usuarios/perfil",
    response_model=APIResponse[UserResponse],
    summary="Atualizar perfil do usuário",
    description="Atualiza avatar, profissão e/ou tema (theme_mode) do usuário autenticado.",
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


# A-18: POST/DELETE /api/usuarios/avatar foram removidos por decisão do
# usuário. A foto de perfil fica só no aparelho (o app nunca chamou o upload);
# o servidor guarda apenas o caminho local que o app manda em PerfilUpdate.avatar.
# Antes, os arquivos ficavam servidos em /uploads sem autenticação, com nome
# sequencial (o id do usuário).


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
    try:
        bonificacao = BonificacaoService.criar(db, current_user, payload)
    except ErroDeDominio as erro:
        raise http_de_erro_de_dominio(erro) from erro
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
    try:
        BonificacaoService.deletar(db, current_user, bonificacao_id)
    except ErroDeDominio as erro:
        raise http_de_erro_de_dominio(erro) from erro
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
    try:
        bonificacao = BonificacaoService.atualizar(db, current_user, bonificacao_id, payload)
    except ErroDeDominio as erro:
        raise http_de_erro_de_dominio(erro) from erro
    return APIResponse(success=True, message="Bonificação atualizada com sucesso.", data=BonificacaoResponse.model_validate(bonificacao))


@router.post(
    "/trocas",
    response_model=APIResponse[TrocaOperacaoData],
    status_code=status.HTTP_201_CREATED,
    summary="Registrar troca",
    description="Transfere um plantão do usuário autenticado e, opcionalmente, cria o plantão recebido na mesma transação.",
)
def criar_troca(
    payload: TrocaOperacaoCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    try:
        resultado = TrocaService.registrar(db, current_user, payload)
    except ErroDeDominio as erro:
        raise http_de_erro_de_dominio(erro) from erro
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=500,
            detail="Não foi possível registrar a troca; nenhuma alteração foi salva.",
        ) from exc

    return APIResponse(
        success=True,
        message="Troca registrada com sucesso.",
        data=TrocaOperacaoData(
            troca_id=resultado.troca.id,
            plantao_transferido=_plantao_com_troca(db, resultado.plantao_transferido),
            plantao_recebido=(
                _plantao_com_troca(db, resultado.plantao_recebido)
                if resultado.plantao_recebido is not None
                else None
            ),
        ),
    )


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
        trocas = TrocaRepository.list_by_local(db, local_id, current_user.id)
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
    try:
        TrocaService.deletar(db, current_user, troca_id)
    except ErroDeDominio as erro:
        raise http_de_erro_de_dominio(erro) from erro

    return APIResponse(success=True, message="Troca deletada com sucesso.", data=None)


@router.post(
    "/feedback",
    response_model=APIResponse[None],
    summary="Enviar feedback",
    description="Envia um feedback do usuário autenticado por e-mail para a equipe de desenvolvimento.",
)
def enviar_feedback(
    payload: FeedbackCreate,
    current_user=Depends(get_current_user),
    # A-12: cada feedback vira um e-mail para a equipe.
    _ip=Depends(rate_limit(20, 600)),
    _usuario=Depends(rate_limit_por_usuario(get_current_user, 5, 600)),
):
    """Endpoint que envia feedback do usuário autenticado por email para o time."""
    try:
        FeedbackService.enviar_feedback(current_user, payload.mensagem)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Falha ao enviar feedback do usuário %s", mascarar_email(current_user.email))
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
    # AUD-089: comparação em tempo constante (o `!=` deixava medir quantos
    # caracteres do segredo batiam pelo tempo de resposta).
    if not hmac.compare_digest(x_upgrade_secret.encode("utf-8"), settings.upgrade_webhook_secret.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Secret inválido.")

    user = UserRepository.get_by_id(db, payload.usuario_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")

    atualizado = PlanoService.upgrade_para_premium(user)
    db.add(user)
    db.commit()
    db.refresh(user)

    logger.info("Plano atualizado para premium: %s (id=%s)", mascarar_email(user.email), user.id)
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
    local = LocalService.criar(db, current_user, payload)
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
    try:
        local = LocalService.atualizar(db, current_user, local_id, payload)
    except ErroDeDominio as erro:
        raise http_de_erro_de_dominio(erro) from erro
    except Exception as exc:
        logger.exception("Falha ao atualizar local %s", local_id)
        raise HTTPException(status_code=500, detail="Erro ao atualizar local") from exc

    return APIResponse(
        success=True,
        message="Local atualizado com sucesso.",
        data=LocalResponse.model_validate(local),
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
    try:
        LocalService.deletar(db, current_user, local_id)
    except ErroDeDominio as erro:
        raise http_de_erro_de_dominio(erro) from erro

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
    try:
        plantao = PlantaoService.criar(db, current_user, payload)
    except ErroDeDominio as erro:
        raise http_de_erro_de_dominio(erro) from erro
    except HTTPException:
        raise
    except Exception as exc:
        # AUD-056: sem isto o 500 saía sem causa no log (o handler de
        # HTTPException registra só o `detail`).
        logger.exception("Falha ao criar plantão do usuário %s", current_user.id)
        raise HTTPException(status_code=500, detail="Erro ao criar plantão") from exc
    return APIResponse(
        success=True,
        message="Plantão criado com sucesso.",
        data=PlantaoResponse.model_validate(plantao),
    )


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
        data=_plantoes_com_troca(db, plantoes),
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
    try:
        plantao = PlantaoService.atualizar(db, current_user, plantao_id, payload, aplicar_em=payload.aplicar_em)
    except ErroDeDominio as erro:
        raise http_de_erro_de_dominio(erro) from erro
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Falha ao atualizar plantão %s", plantao_id)  # AUD-056
        raise HTTPException(status_code=500, detail="Erro ao atualizar plantão") from exc
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
        mascarar_email(current_user.email),
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
    description=(
        "Remove um plantão pertencente ao usuário autenticado. Com "
        "aplicar_em=este_e_futuros, remove também os plantões futuros da mesma "
        "série recorrente (mesmo critério de série usado em PATCH /plantoes/:id)."
    ),
)
def deletar_plantao(
    plantao_id: int,
    aplicar_em: Literal["somente_este", "este_e_futuros"] = Query("somente_este"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    try:
        PlantaoService.deletar(db, current_user, plantao_id, aplicar_em=aplicar_em)
    except ErroDeDominio as erro:
        raise http_de_erro_de_dominio(erro) from erro
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
        logger.exception("Falha ao registrar transferência do plantão %s", plantao_id)  # AUD-056
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
        # AUD-033: o destinatário pode nem ter conta; nome dele não vai ao log
        # e o telefone vai mascarado.
        "Plantão %s transferido por %s para %s. Status: %s. SMS enviado: %s",
        plantao_id,
        mascarar_email(current_user.email),
        mascarar_telefone(payload.destinatario_telefone),
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

    # Mesmo limite aplicado em POST /api/plantoes — sem isso, um usuário do plano free
    # conseguia ultrapassar o limite recebendo plantões por transferência em vez de criá-los.
    PlanoService.verificar_limite_serie(db, current_user, 1)

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
        logger.exception("Falha ao aceitar transferência %s", transferencia_id)  # AUD-056
        raise HTTPException(status_code=500, detail="Erro ao aceitar transferência") from exc

    logger.info(
        "Transferência %s aceita por %s. Novo plantão %s criado.",
        transferencia_id,
        mascarar_email(current_user.email),
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
        logger.exception("Falha ao recusar transferência %s", transferencia_id)  # AUD-056
        raise HTTPException(status_code=500, detail="Erro ao recusar transferência") from exc

    logger.info(
        "Transferência %s recusada por %s. Plantão %s revertido.",
        transferencia_id,
        mascarar_email(current_user.email),
        transferencia.plantao_id,
    )

    return APIResponse(success=True, message="Transferência recusada. O plantão foi devolvido ao remetente.", data=None)




@router.get(
    "/usuarios/busca",
    response_model=APIResponse[list[UsuarioResumo]],
    summary="Buscar usuários por email ou telefone",
    description="Busca usuários por prefixo de email OU por telefone normalizado (máx. 5 resultados), "
    "excluindo o próprio usuário e quem já tem amizade pendente ou aceita com ele. Informe "
    "exatamente um dos dois parâmetros.",
)
def buscar_usuarios(
    # min_length=3 no email: com 1 caractere, percorrer os prefixos devolvia a
    # tabela de usuários de 5 em 5 resultados. Três caracteres ainda servem a
    # uma busca real (quem procura um contato sabe o começo do email dele) e
    # tornam a varredura muito menos prática (ver auditoria C-09).
    email: str | None = Query(default=None, min_length=3, max_length=255),
    telefone: str | None = Query(default=None, min_length=1, max_length=30),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
    _=Depends(rate_limit(30, 60)),
):
    if (email is None) == (telefone is None):
        raise HTTPException(status_code=400, detail="Informe email ou telefone, nunca os dois nem nenhum.")

    if email is not None:
        usuarios = UserRepository.buscar_por_email_prefixo(db, email, excluir_usuario_id=current_user.id)
    else:
        usuarios = UserRepository.buscar_por_telefone(db, telefone, excluir_usuario_id=current_user.id)

    return APIResponse(
        success=True,
        message="Usuários encontrados.",
        # K9 (AUD-064): a busca por prefixo revelava o e-mail completo de quem
        # aparecia (bastava "abc" para colher endereços). Só devolve o e-mail
        # quando quem buscou digitou exatamente o dele.
        data=[
            UsuarioResumo(
                id=u.id,
                nome=u.nome,
                email=u.email if email is not None and normalizar_email(email) == u.email else None,
                avatar=u.avatar,
            )
            for u in usuarios
        ],
    )


@router.post(
    "/amizades",
    response_model=APIResponse[AmizadeResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Enviar solicitação de amizade",
)
def criar_amizade(
    payload: AmizadeCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
    # Segunda camada contra a varredura da base: o endpoint aceita qualquer
    # destinatario_usuario_id, então sem limite bastava percorrer os ids em
    # sequência para materializar uma amizade com todo mundo e depois ler a
    # listagem (ver auditoria C-09). Esconder o telefone de pendentes reduz o
    # que se colhe; o limite reduz a escala.
    _=Depends(rate_limit(20, 60)),
    # A-12: o mesmo teto por conta, que não se contorna trocando de IP.
    _usuario=Depends(rate_limit_por_usuario(get_current_user, 20, 60)),
):
    if payload.destinatario_usuario_id == current_user.id:
        raise HTTPException(status_code=400, detail="Não é possível adicionar a si mesmo.")

    destinatario = UserRepository.get_by_id(db, payload.destinatario_usuario_id)
    if destinatario is None:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")

    existente = AmizadeRepository.get_relacao_existente(db, current_user.id, payload.destinatario_usuario_id)
    amizade = None
    if existente is None:
        amizade = AmizadeRepository.create(db, current_user.id, payload.destinatario_usuario_id)
    if amizade is None:
        # Também quando o par foi gravado entre a checagem e o INSERT (pedido
        # simultâneo na direção oposta): o índice único recusa, é conflito.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Já existe uma solicitação pendente ou amizade com este usuário.",
        )

    logger.info("Solicitação de amizade criada: %s -> %s", mascarar_email(current_user.email), mascarar_email(destinatario.email))
    return APIResponse(
        success=True,
        message="Solicitação de amizade enviada.",
        data=AmizadeResponse.model_validate(amizade),
    )


@router.get(
    "/amizades",
    response_model=APIResponse[list[AmizadeListItem]],
    summary="Listar amizades e solicitações",
    description="Lista as amizades e solicitações do usuário autenticado, embutindo o outro usuário e, "
    "quando aceita, a configuração de visibilidade/cor do próprio usuário para aquele amigo.",
)
def listar_amizades(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    amizades = AmizadeRepository.list_by_usuario(db, current_user.id)
    configs_por_amigo = {
        config.amigo_usuario_id: config
        for config in AmigoConfiguracaoRepository.list_by_usuario(db, current_user.id)
    }

    itens = []
    for amizade in amizades:
        if amizade.solicitante_usuario_id == current_user.id:
            papel = "solicitante"
            outro = amizade.destinatario
        else:
            papel = "destinatario"
            outro = amizade.solicitante

        config = configs_por_amigo.get(outro.id)
        # Telefone é dado de contato, não de exibição: a lista mostra nome e
        # avatar. Só acompanha a resposta depois que a outra parte ACEITOU —
        # numa solicitação pendente, quem recebe nunca consentiu com nada (ver
        # auditoria C-09).
        amigo = AmizadeAmigoResumo.da_amizade(outro, amizade.status)
        itens.append(
            AmizadeListItem(
                id=amizade.id,
                status=amizade.status,
                papel=papel,
                amigo=amigo,
                configuracao_id=config.id if config else None,
                modo_visibilidade=config.modo_visibilidade if config else None,
                mostrar_valores=config.mostrar_valores if config else None,
                cor=config.cor if config else None,
                locais_permitidos=[item.local_id for item in config.locais_permitidos] if config else [],
                criado_em=amizade.criado_em,
            )
        )

    return APIResponse(success=True, message="Amizades listadas com sucesso.", data=itens)


@router.patch(
    "/amizades/{amizade_id}/aceitar",
    response_model=APIResponse[AmizadeResponse],
    summary="Aceitar solicitação de amizade",
    description="Somente o destinatário pode aceitar. Cria automaticamente as duas linhas de "
    "configuração (uma para cada lado) com os valores padrão.",
)
def aceitar_amizade(
    amizade_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    amizade = AmizadeRepository.get_by_id(db, amizade_id)
    if amizade is None or current_user.id not in (amizade.solicitante_usuario_id, amizade.destinatario_usuario_id):
        raise HTTPException(status_code=404, detail="Solicitação de amizade não encontrada.")
    if amizade.destinatario_usuario_id != current_user.id:
        raise HTTPException(status_code=403, detail="Apenas o destinatário pode aceitar esta solicitação.")
    if amizade.status != "pendente":
        raise HTTPException(status_code=404, detail="Solicitação de amizade não está pendente.")

    aceita = AmizadeRepository.aceitar(db, amizade)
    if aceita is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Esta solicitação de amizade já foi respondida.")
    amizade, _, _ = aceita
    logger.info("Amizade %s aceita por %s", amizade_id, mascarar_email(current_user.email))
    return APIResponse(success=True, message="Amizade aceita.", data=AmizadeResponse.model_validate(amizade))


@router.patch(
    "/amizades/{amizade_id}/rejeitar",
    response_model=APIResponse[None],
    summary="Rejeitar solicitação de amizade",
    description="Somente o destinatário pode rejeitar. A linha é apagada, permitindo um novo convite futuro.",
)
def rejeitar_amizade(
    amizade_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    amizade = AmizadeRepository.get_by_id(db, amizade_id)
    if amizade is None or current_user.id not in (amizade.solicitante_usuario_id, amizade.destinatario_usuario_id):
        raise HTTPException(status_code=404, detail="Solicitação de amizade não encontrada.")
    if amizade.destinatario_usuario_id != current_user.id:
        raise HTTPException(status_code=403, detail="Apenas o destinatário pode rejeitar esta solicitação.")
    if amizade.status != "pendente":
        raise HTTPException(status_code=404, detail="Solicitação de amizade não está pendente.")

    if not AmizadeRepository.rejeitar(db, amizade):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Esta solicitação de amizade já foi respondida.")
    logger.info("Amizade %s rejeitada por %s", amizade_id, mascarar_email(current_user.email))
    return APIResponse(success=True, message="Solicitação de amizade rejeitada.", data=None)


@router.delete(
    "/amizades/{amizade_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remover amizade ou cancelar solicitação pendente",
    description="Quando a amizade está aceita, remove a amizade e as duas linhas de configuração "
    "associadas; qualquer um dos dois lados pode chamar. Quando está pendente, cancela a "
    "solicitação; só quem enviou (solicitante) pode chamar — o destinatário deve usar /rejeitar.",
)
def remover_amizade(
    amizade_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    amizade = AmizadeRepository.get_by_id(db, amizade_id)
    if amizade is None or current_user.id not in (amizade.solicitante_usuario_id, amizade.destinatario_usuario_id):
        raise HTTPException(status_code=404, detail="Amizade não encontrada.")

    if amizade.status == "pendente":
        if amizade.solicitante_usuario_id != current_user.id:
            raise HTTPException(
                status_code=403,
                detail="Apenas quem enviou a solicitação pode cancelá-la. Use /rejeitar para recusar.",
            )
        AmizadeRepository.cancelar_pendente(db, amizade)
        logger.info("Solicitação de amizade %s cancelada por %s", amizade_id, mascarar_email(current_user.email))
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    AmizadeRepository.deletar_aceita(db, amizade)
    logger.info("Amizade %s removida por %s", amizade_id, mascarar_email(current_user.email))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch(
    "/amigo-configuracoes/{config_id}",
    response_model=APIResponse[AmigoConfiguracaoResponse],
    summary="Atualizar configuração de visibilidade/cor de um amigo",
    description="Atualiza apenas a linha de configuração cujo usuario_id é o do usuário autenticado. "
    "Cada lado da amizade só pode editar a própria configuração.",
)
def atualizar_amigo_configuracao(
    config_id: int,
    payload: AmigoConfiguracaoUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    config = AmigoConfiguracaoRepository.get_by_id(db, config_id)
    if config is None or config.usuario_id != current_user.id:
        raise HTTPException(status_code=404, detail="Configuração não encontrada.")

    dados = payload.model_dump(exclude_unset=True)
    locais_permitidos_ids = dados.pop("locais_permitidos_ids", None)

    if locais_permitidos_ids is not None:
        locais_do_usuario = {local.id for local in LocalRepository.list_by_user(db, current_user.id)}
        invalidos = set(locais_permitidos_ids) - locais_do_usuario
        if invalidos:
            raise HTTPException(
                status_code=400,
                detail=f"Locais inválidos ou não pertencentes ao usuário: {sorted(invalidos)}",
            )

    config = AmigoConfiguracaoRepository.update(
        db, config, locais_permitidos_ids=locais_permitidos_ids, **dados
    )
    return APIResponse(
        success=True,
        message="Configuração atualizada com sucesso.",
        data=_serializar_amigo_configuracao(config),
    )


@router.get(
    "/amigos/plantoes",
    response_model=APIResponse[list[AmigoPlantaoItem]],
    summary="Listar plantões visíveis dos amigos",
    description=(
        "Retorna os plantões dos amigos aceitos do usuário autenticado, filtrados pela "
        "visibilidade (modo_visibilidade/locais_permitidos) e mostrar_valores que CADA AMIGO "
        "definiu para este usuário. A cor de cada item é a que o usuário autenticado escolheu "
        "para pintar aquele amigo em seu próprio calendário."
    ),
)
def listar_plantoes_de_amigos(
    amigo_usuario_id: int | None = Query(default=None, description="Filtra o resultado para um único amigo."),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    amizades_aceitas = [
        a for a in AmizadeRepository.list_by_usuario(db, current_user.id) if a.status == "aceito"
    ]
    amigos_ids = [
        (a.destinatario_usuario_id if a.solicitante_usuario_id == current_user.id else a.solicitante_usuario_id)
        for a in amizades_aceitas
    ]

    if amigo_usuario_id is not None:
        if amigo_usuario_id not in amigos_ids:
            raise HTTPException(status_code=404, detail="Amigo não encontrado ou amizade não aceita.")
        amigos_ids = [amigo_usuario_id]

    if not amigos_ids:
        return APIResponse(success=True, message="Nenhum amigo com plantões visíveis.", data=[])

    minhas_cores = {
        config.amigo_usuario_id: config.cor
        for config in AmigoConfiguracaoRepository.list_by_usuario(db, current_user.id)
    }
    visibilidades = {
        config.usuario_id: config
        for config in AmigoConfiguracaoRepository.list_visibilidade_para_usuario(db, current_user.id, amigos_ids)
    }

    itens: list[AmigoPlantaoItem] = []
    for amigo_id in amigos_ids:
        visibilidade = visibilidades.get(amigo_id)
        if visibilidade is None:
            continue

        locais_do_amigo = {local.id: local for local in LocalRepository.list_by_user(db, amigo_id)}
        if visibilidade.modo_visibilidade == "personalizado":
            local_ids_permitidos = [item.local_id for item in visibilidade.locais_permitidos]
        else:
            local_ids_permitidos = list(locais_do_amigo.keys())

        if not local_ids_permitidos:
            continue

        amigo_user = UserRepository.get_by_id(db, amigo_id)
        if amigo_user is None:
            continue

        plantoes = PlantaoRepository.list_by_user_e_locais(db, amigo_id, local_ids_permitidos)
        for plantao in plantoes:
            plantao_data = PlantaoResponse.model_validate(plantao)
            if not visibilidade.mostrar_valores:
                plantao_data.valor = Decimal("0")
                # AUD-062: a data de pagamento e a descrição da bonificação
                # ("plantão extra R$ 500") revelavam o que o valor zerado escondia.
                plantao_data.pagamento_plantao = None
                for bonificacao in plantao_data.bonificacoes:
                    bonificacao.valor = Decimal("0")
                    bonificacao.descricao = None

            local = locais_do_amigo.get(plantao.local_id)
            itens.append(
                AmigoPlantaoItem(
                    amigo_usuario_id=amigo_id,
                    amigo_nome=amigo_user.nome,
                    amigo_avatar=amigo_user.avatar,
                    cor=minhas_cores.get(amigo_id, "#0D9488"),
                    local_id=plantao.local_id,
                    local_nome=local.nome_local if local else "",
                    local_endereco=local.endereco_local if local else None,
                    local_dia_pagamento=local.dia_pagamento if local else None,
                    plantao=plantao_data,
                )
            )

    return APIResponse(success=True, message="Plantões de amigos listados com sucesso.", data=itens)






def _plantao_visivel_para_amigo(db: Session, plantao: Plantao, observador_id: int) -> bool:
    """AUD-029: o plantão aparece na agenda que o dono mostra a `observador_id`?

    A mesma regra de /amigos/plantoes: precisa haver a configuração do dono
    para o observador; no modo "personalizado", o local do plantão tem de
    estar entre os permitidos; no modo "tudo", tem de ser um local do próprio
    dono (plantão sem local ou com local de terceiro não aparece na agenda).
    """
    config = db.scalar(
        select(AmigoConfiguracao)
        .options(selectinload(AmigoConfiguracao.locais_permitidos))
        .where(
            AmigoConfiguracao.usuario_id == plantao.usuario_id,
            AmigoConfiguracao.amigo_usuario_id == observador_id,
        )
    )
    if config is None or plantao.local_id is None:
        return False
    if config.modo_visibilidade == "personalizado":
        return plantao.local_id in {item.local_id for item in config.locais_permitidos}
    local = LocalRepository.get_by_id(db, plantao.local_id)
    return local is not None and local.usuario_id == plantao.usuario_id


@router.post(
    "/solicitacoes-troca",
    response_model=APIResponse[SolicitacaoTrocaResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Solicitar troca de plantão com um amigo",
    description=(
        "Cria um pedido pendente de troca de plantão com um amigo (amizade precisa estar aceita). "
        "Só vira uma troca de fato (POST /trocas) quando o destinatário aceitar."
    ),
)
def criar_solicitacao_troca(
    payload: SolicitacaoTrocaCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
    # A-12: limite por IP (generoso: vários médicos atrás do NAT do hospital)
    # e por conta. Só a criação; o fluxo de aceite não muda.
    _ip=Depends(rate_limit(60, 60)),
    _usuario=Depends(rate_limit_por_usuario(get_current_user, 20, 60)),
):
    if payload.destinatario_usuario_id == current_user.id:
        raise HTTPException(status_code=400, detail="Não é possível solicitar troca consigo mesmo.")

    destinatario = UserRepository.get_by_id(db, payload.destinatario_usuario_id)
    if destinatario is None:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")

    amizade = AmizadeRepository.get_aceita_entre(db, current_user.id, payload.destinatario_usuario_id)
    if amizade is None:
        raise HTTPException(status_code=403, detail="Só é possível solicitar troca com um amigo com amizade aceita.")

    if payload.plantao_transferido_id is not None:
        plantao = db.scalar(select(Plantao).where(Plantao.id == payload.plantao_transferido_id))
        if plantao is None or plantao.usuario_id != current_user.id:
            raise HTTPException(status_code=404, detail="Plantão transferido não encontrado.")
        if plantao.transferido:
            raise HTTPException(status_code=400, detail="Este plantão já foi transferido e não pode ser transferido novamente.")

    if payload.plantao_recebido is not None:
        local = LocalRepository.get_by_id(db, payload.plantao_recebido.local_id)
        if local is None or local.usuario_id != current_user.id:
            raise HTTPException(status_code=404, detail="Local do plantão recebido não encontrado.")

    if payload.plantao_alvo_id is not None:
        plantao_alvo = db.scalar(select(Plantao).where(Plantao.id == payload.plantao_alvo_id))
        if (
            plantao_alvo is None
            or plantao_alvo.usuario_id != payload.destinatario_usuario_id
            # AUD-029: só um plantão que o destinatário MOSTRA para quem pede
            # (mesma regra de /amigos/plantoes). Antes, qualquer id de plantão
            # do amigo servia, e o resumo da solicitação devolvia local e
            # valor que ele tinha escondido. Mesma resposta de "não existe".
            or not _plantao_visivel_para_amigo(db, plantao_alvo, current_user.id)
        ):
            raise HTTPException(status_code=404, detail="Plantão alvo não encontrado ou não pertence ao destinatário.")
        if plantao_alvo.transferido:
            raise HTTPException(status_code=400, detail="Este plantão já foi transferido e não pode ser negociado novamente.")

    recebido = payload.plantao_recebido
    solicitacao = SolicitacaoTrocaRepository.create(
        db,
        remetente_usuario_id=current_user.id,
        destinatario_usuario_id=payload.destinatario_usuario_id,
        tipo=payload.tipo,
        plantao_transferido_id=payload.plantao_transferido_id,
        plantao_alvo_id=payload.plantao_alvo_id,
        plantao_recebido_local_id=recebido.local_id if recebido else None,
        plantao_recebido_data_inicio=recebido.data_inicio if recebido else None,
        plantao_recebido_horario_inicio=recebido.horario_inicio if recebido else None,
        plantao_recebido_data_fim=recebido.data_fim if recebido else None,
        plantao_recebido_horario_fim=recebido.horario_fim if recebido else None,
        plantao_recebido_valor=recebido.valor if recebido else None,
        plantao_recebido_sobreaviso=recebido.sobreaviso if recebido else False,
    )
    logger.info(
        "Solicitação de troca criada: %s -> %s (%s)", mascarar_email(current_user.email), mascarar_email(destinatario.email), payload.tipo
    )
    return APIResponse(
        success=True,
        message="Solicitação de troca enviada.",
        data=SolicitacaoTrocaResponse.model_validate(solicitacao),
    )


@router.get(
    "/solicitacoes-troca",
    response_model=APIResponse[list[SolicitacaoTrocaListItem]],
    summary="Listar solicitações de troca",
    description="Lista as solicitações de troca em que o usuário autenticado é remetente ou destinatário.",
)
def listar_solicitacoes_troca(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    solicitacoes = SolicitacaoTrocaRepository.list_by_usuario(db, current_user.id)
    ocultos = _alvos_com_valor_oculto(db, current_user.id, solicitacoes)
    itens = [
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
        for s in solicitacoes
    ]
    return APIResponse(success=True, message="Solicitações de troca listadas com sucesso.", data=itens)


@router.patch(
    "/solicitacoes-troca/{solicitacao_id}/aceitar",
    response_model=APIResponse[None],
    summary="Aceitar solicitação de troca",
    description=(
        "Somente o destinatário pode aceitar. Marca o plantão transferido, cria/transfere o "
        "plantão recebido quando houver, e grava o registro em trocas usando os dados reais das "
        "contas de remetente e destinatário em vez de texto livre. Para tipo='transferir' puro "
        "(sem plantao_recebido nem plantao_alvo_id), clona o plantão transferido para a conta do "
        "destinatário, que passa a ter o próprio plantão marcado recebido_via_transferencia=true."
    ),
)
def aceitar_solicitacao_troca(
    solicitacao_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    solicitacao = SolicitacaoTrocaRepository.get_by_id(db, solicitacao_id)
    if (
        solicitacao is None
        or solicitacao.destinatario_usuario_id != current_user.id
        or solicitacao.status != "pendente"
    ):
        raise HTTPException(status_code=404, detail="Solicitação de troca não encontrada ou não está pendente.")

    remetente = solicitacao.remetente
    destinatario = solicitacao.destinatario

    plantao_transferido = None
    if solicitacao.plantao_transferido_id is not None:
        plantao_transferido = db.scalar(
            select(Plantao).where(Plantao.id == solicitacao.plantao_transferido_id)
        )
        if plantao_transferido is None or plantao_transferido.usuario_id != solicitacao.remetente_usuario_id:
            raise HTTPException(status_code=404, detail="Plantão transferido não encontrado.")
        if plantao_transferido.transferido:
            raise HTTPException(status_code=400, detail="Este plantão já foi transferido e não pode ser transferido novamente.")

    plantao_alvo = None
    if solicitacao.plantao_alvo_id is not None:
        plantao_alvo = db.scalar(select(Plantao).where(Plantao.id == solicitacao.plantao_alvo_id))
        if plantao_alvo is None or plantao_alvo.usuario_id != solicitacao.destinatario_usuario_id:
            raise HTTPException(status_code=404, detail="Plantão alvo não encontrado ou não pertence mais ao destinatário.")
        if plantao_alvo.transferido:
            raise HTTPException(status_code=400, detail="Este plantão já foi transferido e não pode ser negociado novamente.")

    if solicitacao.plantao_recebido_local_id is not None:
        local = LocalRepository.get_by_id(db, solicitacao.plantao_recebido_local_id)
        if local is None or local.usuario_id != solicitacao.remetente_usuario_id:
            raise HTTPException(status_code=404, detail="Local do plantão recebido não encontrado.")

    # Mesmo limite aplicado em POST /api/plantoes, calculado pelo ganho líquido de cada
    # lado: numa troca de verdade (tipo='trocar') cada parte cede um plantão e recebe outro,
    # então o saldo é zero e ninguém deveria ser bloqueado; só bloqueia quem efetivamente
    # fica com mais plantões ativos ao final da operação (ex.: tipo='receber').
    ganho_remetente = 1 if (plantao_alvo is not None or solicitacao.plantao_recebido_local_id is not None) else 0
    perda_remetente = 1 if plantao_transferido is not None else 0
    if ganho_remetente > perda_remetente:
        PlanoService.verificar_limite_serie(db, remetente, ganho_remetente - perda_remetente)

    ganho_destinatario = 1 if plantao_transferido is not None else 0
    perda_destinatario = 1 if plantao_alvo is not None else 0
    if ganho_destinatario > perda_destinatario:
        PlanoService.verificar_limite_serie(db, destinatario, ganho_destinatario - perda_destinatario)

    # A-08: a resposta é reservada ANTES de qualquer escrita, com UPDATE
    # condicional ao status 'pendente'. Sem isto, dois aceites simultâneos
    # passavam pela checagem acima e os dois criavam clone e Troca.
    if not SolicitacaoTrocaRepository.responder_se_pendente(db, solicitacao.id, "aceita"):
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Esta solicitação de troca já foi respondida.")

    try:
        if plantao_transferido is not None:
            plantao_transferido.transferido = True

        # Lado do remetente: o que ele ganha nesta solicitação, quando o destinatário está
        # cedendo um plantão (tipos 'receber' e 'trocar'). Independente do lado do
        # destinatário abaixo — numa troca real (tipo='trocar') os dois acontecem juntos.
        recebido_remetente = None
        if plantao_alvo is not None:
            # Mesmo tratamento do plantao_transferido abaixo: o plantão original
            # permanece com quem cedeu (destinatário), só marcado transferido=True, e um
            # clone novo é criado para quem recebe. Reatribuir usuario_id diretamente
            # (comportamento antigo) fazia o plantão sumir da agenda de quem cedeu, sem
            # deixar rastro de que ele foi transferido.
            plantao_alvo.transferido = True
            recebido_remetente = Plantao(
                usuario_id=solicitacao.remetente_usuario_id,
                local_id=plantao_alvo.local_id,
                valor=plantao_alvo.valor,
                data_inicio=plantao_alvo.data_inicio,
                horario_inicio=plantao_alvo.horario_inicio,
                data_fim=plantao_alvo.data_fim,
                horario_fim=plantao_alvo.horario_fim,
                sobreaviso=plantao_alvo.sobreaviso,
                descricao=plantao_alvo.descricao,
                recorrencia=None,
                notificacao_ativa=True,
                notificacao_antecedencia_minutos=60,
                notificado=False,
                executado=False,
                transferido=False,
                recebido_via_transferencia=True,
            )
            db.add(recebido_remetente)
            db.flush()
        elif solicitacao.plantao_recebido_local_id is not None:
            recebido_remetente = Plantao(
                usuario_id=solicitacao.remetente_usuario_id,
                local_id=solicitacao.plantao_recebido_local_id,
                valor=solicitacao.plantao_recebido_valor,
                data_inicio=solicitacao.plantao_recebido_data_inicio,
                horario_inicio=solicitacao.plantao_recebido_horario_inicio,
                data_fim=solicitacao.plantao_recebido_data_fim,
                horario_fim=solicitacao.plantao_recebido_horario_fim,
                sobreaviso=solicitacao.plantao_recebido_sobreaviso,
                recorrencia=None,
                notificacao_ativa=True,
                notificacao_antecedencia_minutos=60,
                notificado=False,
                executado=False,
                transferido=False,
                recebido_via_transferencia=True,
            )
            db.add(recebido_remetente)
            db.flush()

        # Lado do destinatário: o que ele ganha nesta solicitação, sempre que o remetente
        # está cedendo plantao_transferido (tipos 'transferir' e 'trocar'). Isso NÃO pode
        # ser um elif do bloco acima — numa troca real (tipo='trocar') ambos os lados
        # precisam ser processados, senão quem aceita nunca recebe o plantão do remetente
        # (bug: só o remetente ficava com os dois plantões após aceitar uma troca).
        recebido_destinatario = None
        if plantao_transferido is not None:
            # Devolução: plantao_transferido é ele mesmo o lado "recebido" de uma troca
            # anterior, e o plantão original daquela troca pertence a quem está aceitando
            # agora — ou seja, o plantão está voltando pro dono original. Reaproveita essa
            # linha original (só desmarcando transferido) em vez de criar mais um clone;
            # senão o dono original ficava com dois cards pro mesmo plantão — o clone novo
            # "recebido" e o original antigo, preso para sempre com transferido=True — e
            # perdia recorrência/notificação/bonificações da linha original, que um clone
            # em branco não carrega.
            plantao_original = None
            troca_origem = db.scalar(
                select(Troca)
                .where(Troca.plantao_id_recebido == plantao_transferido.id)
                .order_by(Troca.id.desc())
            )
            if troca_origem is not None and troca_origem.plantao_id_transferido is not None:
                candidato = db.scalar(select(Plantao).where(Plantao.id == troca_origem.plantao_id_transferido))
                if (
                    candidato is not None
                    and candidato.usuario_id == solicitacao.destinatario_usuario_id
                    and candidato.transferido
                ):
                    plantao_original = candidato

            if plantao_original is not None:
                plantao_original.transferido = False
                recebido_destinatario = plantao_original
            else:
                # É sempre um clone novo — o plantão original permanece com o remetente, só
                # marcado transferido=True para histórico. Não fica vinculado a nenhum local
                # próprio do destinatário; existe só como card do recebimento.
                recebido_destinatario = Plantao(
                    usuario_id=solicitacao.destinatario_usuario_id,
                    local_id=plantao_transferido.local_id,
                    valor=plantao_transferido.valor,
                    data_inicio=plantao_transferido.data_inicio,
                    horario_inicio=plantao_transferido.horario_inicio,
                    data_fim=plantao_transferido.data_fim,
                    horario_fim=plantao_transferido.horario_fim,
                    sobreaviso=plantao_transferido.sobreaviso,
                    descricao=plantao_transferido.descricao,
                    recorrencia=None,
                    notificacao_ativa=True,
                    notificacao_antecedencia_minutos=60,
                    notificado=False,
                    executado=False,
                    transferido=False,
                    recebido_via_transferencia=True,
                )
                db.add(recebido_destinatario)
            db.flush()

        # Cada lado vira seu próprio registro Troca, para que cada plantão envolvido
        # (transferido ou recebido) resolva a contraparte correta em _plantao_com_troca.
        # Uma troca real (tipo='trocar') gera as duas: uma para cada sentido.
        if plantao_transferido is not None:
            db.add(Troca(
                usuario_id=solicitacao.remetente_usuario_id,
                local_id=plantao_transferido.local_id,
                # AUD-006 (resíduo): plantão já desvinculado (local excluído
                # pelo dono) só tem o nome do hospital na troca de origem; sem
                # repassá-lo, o novo clone nascia sem local_nome.
                local=(
                    troca_origem.local
                    if plantao_transferido.local_id is None and troca_origem is not None
                    else None
                ),
                plantao_id_transferido=plantao_transferido.id,
                plantao_id_recebido=recebido_destinatario.id,
                nome_transferir=destinatario.nome,
                telefone_transferir=destinatario.telefone,
                contraparte_usuario_id=destinatario.id,
                nome_receber=remetente.nome,
                telefone_receber=remetente.telefone,
                contraparte_usuario_id_receber=remetente.id,
            ))

        if recebido_remetente is not None:
            db.add(Troca(
                usuario_id=solicitacao.destinatario_usuario_id,
                local_id=recebido_remetente.local_id,
                plantao_id_transferido=plantao_alvo.id if plantao_alvo is not None else None,
                plantao_id_recebido=recebido_remetente.id,
                nome_transferir=remetente.nome if plantao_alvo is not None else None,
                telefone_transferir=remetente.telefone if plantao_alvo is not None else None,
                contraparte_usuario_id=remetente.id if plantao_alvo is not None else None,
                nome_receber=destinatario.nome,
                telefone_receber=destinatario.telefone,
                contraparte_usuario_id_receber=destinatario.id,
            ))
        db.flush()

        solicitacao.status = "aceita"
        solicitacao.respondido_em = datetime.now(UTC)
        db.add(solicitacao)

        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("Falha transacional ao aceitar solicitação de troca")
        raise HTTPException(status_code=500, detail="Não foi possível concluir a troca; nenhuma alteração foi salva.") from exc

    logger.info("Solicitação de troca %s aceita por %s", solicitacao_id, mascarar_email(current_user.email))
    return APIResponse(success=True, message="Troca concluída com sucesso.", data=None)


@router.patch(
    "/solicitacoes-troca/{solicitacao_id}/rejeitar",
    response_model=APIResponse[None],
    summary="Rejeitar solicitação de troca",
    description="Somente o destinatário pode rejeitar. Marca status='rejeitada', sem outro efeito.",
)
def rejeitar_solicitacao_troca(
    solicitacao_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    solicitacao = SolicitacaoTrocaRepository.get_by_id(db, solicitacao_id)
    if (
        solicitacao is None
        or solicitacao.destinatario_usuario_id != current_user.id
        or solicitacao.status != "pendente"
    ):
        raise HTTPException(status_code=404, detail="Solicitação de troca não encontrada ou não está pendente.")

    if SolicitacaoTrocaRepository.marcar_respondida(db, solicitacao, "rejeitada") is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Esta solicitação de troca já foi respondida.")
    logger.info("Solicitação de troca %s rejeitada por %s", solicitacao_id, mascarar_email(current_user.email))
    return APIResponse(success=True, message="Solicitação de troca rejeitada.", data=None)


@router.delete(
    "/solicitacoes-troca/{solicitacao_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Cancelar solicitação de troca pendente",
    description=(
        "Cancela uma solicitação de troca/transferência/recebimento ainda pendente. Só quem "
        "enviou (remetente) pode cancelar — o destinatário deve usar /rejeitar. Como uma "
        "solicitação tipo='trocar' guarda os dois lados (plantão transferido e recebido) na "
        "mesma linha, cancelar cancela a troca inteira de uma vez, não é preciso duas chamadas."
    ),
)
def cancelar_solicitacao_troca(
    solicitacao_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    solicitacao = SolicitacaoTrocaRepository.get_by_id(db, solicitacao_id)
    if solicitacao is None or current_user.id not in (
        solicitacao.remetente_usuario_id,
        solicitacao.destinatario_usuario_id,
    ):
        raise HTTPException(status_code=404, detail="Solicitação de troca não encontrada.")
    if solicitacao.status != "pendente":
        raise HTTPException(status_code=404, detail="Solicitação de troca não está pendente.")
    if solicitacao.remetente_usuario_id != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="Apenas quem enviou a solicitação pode cancelá-la. Use /rejeitar para recusar.",
        )

    SolicitacaoTrocaRepository.cancelar_pendente(db, solicitacao)
    logger.info("Solicitação de troca %s cancelada por %s", solicitacao_id, mascarar_email(current_user.email))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
