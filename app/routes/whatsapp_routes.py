import hashlib
import hmac
import json
import logging
import secrets

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.mascaras import mascarar_telefone
from app.core.database import get_db
from app.repositories.plantao_repository import UserRepository
from app.repositories.whatsapp_repository import WhatsappRepository
from app.services.claude_service import processar_mensagem
from app.services.email_service import EmailService
from app.services.sms_service import SmsService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["whatsapp"])

# Mensagens de sistema usadas na conversa do WhatsApp para guiar a autenticação e interação.
MSG_BOAS_VINDAS = (
    "Olá! Bem-vindo ao assistente do Plantões App. 👋\n\n"
    "Para começar, envie o *e-mail* da sua conta cadastrada no aplicativo."
)

MSG_CODIGO_ENVIADO = (
    "Encontrei sua conta! Enviamos um código de 6 dígitos para o seu e-mail.\n"
    "Por favor, envie o código aqui para confirmar sua identidade."
)

MSG_AUTENTICADO = (
    "Identidade confirmada! ✅\n\n"
    "Agora você pode gerenciar seus plantões por aqui. Como posso ajudar?\n\n"
    "Exemplos:\n"
    "• \"Liste meus plantões\"\n"
    "• \"Criar plantão sexta 08h-16h valor 500\"\n"
    "• \"Remover plantão ID 12\"\n"
    "• \"Listar meus locais\""
)

MSG_CODIGO_INVALIDO = (
    "Código inválido. Tente novamente ou envie *reiniciar* para recomeçar."
)

MSG_CODIGO_EXPIRADO_OU_ESGOTADO = (
    "Esse código expirou ou você errou demais vezes. Envie seu e-mail novamente para receber um novo código."
)

MSG_EMAIL_NAO_ENCONTRADO = (
    "Não encontrei nenhuma conta com esse e-mail. "
    "Verifique o e-mail e tente novamente, ou acesse o aplicativo para criar uma conta."
)

MSG_ERRO_GENERICO = (
    "Ocorreu um erro inesperado. Tente novamente em instantes."
)

MSG_APENAS_TEXTO = (
    "Só consigo processar mensagens de texto por enquanto. "
    "Por favor, envie sua mensagem em formato de texto."
)


def _gerar_codigo() -> str:
    """Gera um código de 6 dígitos para autenticação via WhatsApp."""
    return f"{secrets.randbelow(1_000_000):06d}"


def _extrair_mensagem(payload: dict) -> tuple[str, str] | None:
    """Extrai (telefone, texto) do payload da Meta. Retorna None se não for mensagem de texto."""
    try:
        entry = payload.get("entry", [])
        if not entry:
            return None
        changes = entry[0].get("changes", [])
        if not changes:
            return None
        value = changes[0].get("value", {})
        messages = value.get("messages", [])
        if not messages:
            return None
        msg = messages[0]
        if msg.get("type") != "text":
            return None
        telefone = msg["from"]
        texto = msg["text"]["body"].strip()
        return telefone, texto
    except (KeyError, IndexError):
        return None


def _responder(telefone: str, mensagem: str) -> None:
    """Envio seguro de resposta para o usuário via WhatsApp. Falhas são apenas logadas."""
    try:
        SmsService.enviar(telefone, mensagem, canal="whatsapp")
    except Exception:
        logger.exception("Falha ao enviar resposta WhatsApp para %s", mascarar_telefone(telefone))


def _assinatura_valida(corpo: bytes, assinatura: str | None) -> bool:
    """Confere a assinatura HMAC-SHA256 (header X-Hub-Signature-256) que a Meta envia em
    todo webhook, usando o app secret configurado. Sem isso, qualquer requisição POST para
    este endpoint público seria processada como se viesse do WhatsApp de qualquer número,
    permitindo personificar a sessão autenticada de outro usuário.

    Se WHATSAPP_APP_SECRET não estiver configurado, só deixa passar fora de produção (para
    permitir testar localmente com curl) — em produção, um webhook mal configurado deve
    falhar de forma visível, não aceitar tráfego não verificado silenciosamente."""
    if not settings.whatsapp_app_secret:
        return settings.environment != "production"
    if not assinatura or not assinatura.startswith("sha256="):
        return False
    esperado = hmac.new(settings.whatsapp_app_secret.encode("utf-8"), corpo, hashlib.sha256).hexdigest()
    recebido = assinatura.removeprefix("sha256=")
    return hmac.compare_digest(esperado, recebido)


@router.get("/webhook/whatsapp", response_class=PlainTextResponse)
def verificar_webhook(
    hub_mode: str = Query(alias="hub.mode", default=""),
    hub_verify_token: str = Query(alias="hub.verify_token", default=""),
    hub_challenge: str = Query(alias="hub.challenge", default=""),
):
    if hub_mode == "subscribe" and hub_verify_token == settings.whatsapp_verify_token:
        logger.info("Webhook WhatsApp verificado com sucesso.")
        return PlainTextResponse(content=hub_challenge)
    logger.warning("Tentativa de verificação de webhook inválida.")
    return PlainTextResponse(content="Forbidden", status_code=403)


@router.post("/webhook/whatsapp")
async def receber_mensagem(request: Request, db: Session = Depends(get_db)):
    corpo = await request.body()
    if not _assinatura_valida(corpo, request.headers.get("X-Hub-Signature-256")):
        logger.warning("Webhook WhatsApp: assinatura ausente ou inválida — requisição descartada.")
        # 200 propositalmente: um 401/403 aqui só ajudaria alguém a testar assinaturas por
        # tentativa e erro. Meta nunca vê isso porque as requisições dela sempre são assinadas.
        return {"status": "ok"}

    # Meta exige 200 imediato — erros internos são logados, nunca devolvem 4xx/5xx
    try:
        payload = json.loads(corpo)
    except Exception:
        return {"status": "ok"}

    resultado = _extrair_mensagem(payload)
    if resultado is None:
        return {"status": "ok"}

    telefone, texto = resultado
    texto_lower = texto.lower()

    # Busca ou cria sessão para esse número
    sessao = WhatsappRepository.get_by_telefone(db, telefone)
    if sessao is None:
        sessao = WhatsappRepository.criar(db, telefone)

    # Comando global de reinício
    if texto_lower in ("reiniciar", "sair", "logout", "reset"):
        WhatsappRepository.resetar_sessao(db, sessao)
        _responder(telefone, MSG_BOAS_VINDAS)
        return {"status": "ok"}

    # --- Máquina de estados de autenticação ---

    if sessao.estado == "aguardando_email":
        _responder(telefone, MSG_BOAS_VINDAS)
        WhatsappRepository.atualizar_estado(db, sessao, "aguardando_email_input")
        return {"status": "ok"}

    if sessao.estado == "aguardando_email_input":
        usuario = UserRepository.get_by_email(db, texto)
        if usuario is None:
            _responder(telefone, MSG_EMAIL_NAO_ENCONTRADO)
            return {"status": "ok"}

        codigo = _gerar_codigo()
        WhatsappRepository.salvar_codigo(db, sessao, codigo)
        # Armazena usuario_id temporariamente no campo para usar na verificação
        sessao.usuario_id = usuario.id
        db.commit()
        WhatsappRepository.atualizar_estado(db, sessao, "aguardando_codigo")

        try:
            EmailService.enviar_codigo_whatsapp(usuario.email, codigo)
        except Exception:
            logger.exception("Falha ao enviar e-mail de código para %s", usuario.email)

        _responder(telefone, MSG_CODIGO_ENVIADO)
        return {"status": "ok"}

    if sessao.estado == "aguardando_codigo":
        if not WhatsappRepository.codigo_valido(sessao):
            WhatsappRepository.resetar_sessao(db, sessao)
            _responder(telefone, MSG_CODIGO_EXPIRADO_OU_ESGOTADO)
            return {"status": "ok"}

        if texto.strip() == sessao.codigo_verificacao:
            WhatsappRepository.vincular_usuario(db, sessao, sessao.usuario_id)
            _responder(telefone, MSG_AUTENTICADO)
        else:
            WhatsappRepository.registrar_tentativa_invalida(db, sessao)
            _responder(telefone, MSG_CODIGO_INVALIDO)
        return {"status": "ok"}

    if sessao.estado == "autenticado":
        if sessao.usuario_id is None:
            WhatsappRepository.resetar_sessao(db, sessao)
            _responder(telefone, MSG_BOAS_VINDAS)
            return {"status": "ok"}

        try:
            resposta, historico_atualizado = processar_mensagem(
                db=db,
                usuario_id=sessao.usuario_id,
                historico_json=sessao.historico,
                mensagem_nova=texto,
            )
            WhatsappRepository.atualizar_historico(db, sessao, historico_atualizado)
            _responder(telefone, resposta)
        except Exception:
            logger.exception("Erro ao processar mensagem Claude para %s", mascarar_telefone(telefone))
            _responder(telefone, MSG_ERRO_GENERICO)

        return {"status": "ok"}

    # Estado desconhecido — reseta
    WhatsappRepository.resetar_sessao(db, sessao)
    _responder(telefone, MSG_BOAS_VINDAS)
    return {"status": "ok"}
