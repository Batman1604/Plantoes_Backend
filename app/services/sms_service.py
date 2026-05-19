import logging

logger = logging.getLogger(__name__)


class SmsService:
    """
    Serviço de envio de mensagens de texto.

    Atualmente registra o conteúdo no log (modo stub).
    Para ativar o envio real via Twilio, implemente _enviar_twilio() e chame-o em enviar().
    Para WhatsApp (Twilio Sandbox ou API oficial), implemente _enviar_whatsapp() e adicione
    o canal como parâmetro.
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
        # TODO: substituir pelo envio real via Twilio quando as credenciais estiverem configuradas.
        # Exemplo de integração:
        #   from app.core.config import settings
        #   import requests
        #   from requests.auth import HTTPBasicAuth
        #   url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/Messages.json"
        #   r = requests.post(url, data={"From": settings.twilio_phone_number, "To": telefone, "Body": mensagem},
        #                     auth=HTTPBasicAuth(settings.twilio_account_sid, settings.twilio_auth_token), timeout=10)
        #   return r.status_code == 201
        logger.info("[SMS STUB] Para: %s | Mensagem: %s", telefone, mensagem)
        return True

    @staticmethod
    def _enviar_whatsapp(telefone: str, mensagem: str) -> bool:
        from app.core.config import settings
        import httpx

        if not settings.whatsapp_token or not settings.whatsapp_phone_number_id:
            logger.warning("[WHATSAPP] Credenciais não configuradas. Para: %s", telefone)
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
                logger.info("[WHATSAPP] Mensagem enviada para %s", telefone)
                return True
            logger.warning("[WHATSAPP] Falha ao enviar para %s: %s", telefone, response.text)
            return False
        except Exception:
            logger.exception("[WHATSAPP] Erro ao enviar mensagem para %s", telefone)
            return False
