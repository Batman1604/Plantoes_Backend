import logging
import random

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.repositories.plantao_repository import UserRepository
from app.repositories.whatsapp_repository import WhatsappRepository
from app.services.claude_service import processar_mensagem
from app.services.email_service import EmailService
from app.services.sms_service import SmsService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["whatsapp"])

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
    return str(random.randint(100000, 999999))


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
    try:
        SmsService.enviar(telefone, mensagem, canal="whatsapp")
    except Exception:
        logger.exception("Falha ao enviar resposta WhatsApp para %s", telefone)


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
    # Meta exige 200 imediato — erros internos são logados, nunca devolvem 4xx/5xx
    try:
        payload = await request.json()
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
        if texto.strip() == sessao.codigo_verificacao:
            WhatsappRepository.vincular_usuario(db, sessao, sessao.usuario_id)
            _responder(telefone, MSG_AUTENTICADO)
        else:
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
            logger.exception("Erro ao processar mensagem Claude para %s", telefone)
            _responder(telefone, MSG_ERRO_GENERICO)

        return {"status": "ok"}

    # Estado desconhecido — reseta
    WhatsappRepository.resetar_sessao(db, sessao)
    _responder(telefone, MSG_BOAS_VINDAS)
    return {"status": "ok"}
