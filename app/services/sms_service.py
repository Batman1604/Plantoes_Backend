import logging

from app.core.mascaras import mascarar_telefone as _mascarar_telefone

logger = logging.getLogger(__name__)


class SmsService:
    """
    Serviço de envio de mensagens de texto.

    O canal de SMS ainda não está implementado: não há provedor configurado.
    Quando isso for verdade, o método correspondente devolve False — nunca
    True. Um "sucesso" falso aqui não fica contido no serviço: as rotas de
    cadastro decidem por ele (ver iniciar_cadastro e reenviar_cadastro, que
    respondem 502 quando o envio falha), então retornar True fazia o servidor
    prometer ao usuário um código que jamais seria entregue, e nenhuma conta
    nova conseguia ser criada (ver auditoria C-10).

    Para ativar o envio real via Twilio, implemente _enviar_sms de verdade.
    O canal de WhatsApp (_enviar_whatsapp) já é real e depende apenas das
    credenciais em WHATSAPP_TOKEN/WHATSAPP_PHONE_NUMBER_ID.
    """

    @staticmethod
    def enviar(telefone: str, mensagem: str, canal: str = "sms") -> bool:
        """
        Envia mensagem para o número informado.

        Retorna True se o envio foi bem-sucedido, False caso contrário.
        """
        if canal == "sms":
            return SmsService._enviar_sms(telefone, mensagem)
        if canal == "whatsapp":
            return SmsService._enviar_whatsapp(telefone, mensagem)
        logger.warning("Canal de mensagem desconhecido: %s", canal)
        return False

    @staticmethod
    def _enviar_sms(telefone: str, mensagem: str) -> bool:
        """Canal de SMS não implementado — nenhum provedor configurado.

        Devolve False porque o envio de fato não aconteceu. A mensagem NÃO é
        registrada: ela carrega o código de verificação de seis dígitos, e a
        versão anterior o escrevia em texto puro, em nível INFO, no log de
        produção.
        """
        logger.warning(
            "Envio de SMS solicitado, mas nenhum provedor está configurado (destino %s).",
            _mascarar_telefone(telefone),
        )
        return False

    @staticmethod
    def enviar_codigo_verificacao(telefone: str, codigo: str) -> bool:
        mensagem = (
            f"Seu código de verificação do app Plantões é: *{codigo}*\n"
            f"Válido por 1 minuto. Não compartilhe com ninguém."
        )
        enviado = SmsService._enviar_whatsapp(telefone, mensagem)
        if not enviado:
            logger.warning("[VERIFICACAO] WhatsApp indisponível, tentando SMS para %s", _mascarar_telefone(telefone))
            enviado = SmsService._enviar_sms(telefone, mensagem)
        return enviado

    @staticmethod
    def _enviar_whatsapp(telefone: str, mensagem: str) -> bool:
        from app.core.config import settings
        import httpx

        if not settings.whatsapp_token or not settings.whatsapp_phone_number_id:
            logger.warning("[WHATSAPP] Credenciais não configuradas. Para: %s", _mascarar_telefone(telefone))
            return False

        url = f"https://graph.facebook.com/v19.0/{settings.whatsapp_phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {settings.whatsapp_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "messaging_product": "whatsapp",
            "to": telefone,
            "type": "text",
            "text": {"body": mensagem},
        }
        try:
            response = httpx.post(url, json=payload, headers=headers, timeout=10)
            if response.status_code == 200:
                logger.info("[WHATSAPP] Mensagem enviada para %s", _mascarar_telefone(telefone))
                return True
            logger.warning("[WHATSAPP] Falha ao enviar para %s: %s", _mascarar_telefone(telefone), response.text)
            return False
        except Exception:
            logger.exception("[WHATSAPP] Erro ao enviar mensagem para %s", _mascarar_telefone(telefone))
            return False
