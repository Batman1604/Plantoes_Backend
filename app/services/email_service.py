import logging
import smtplib
from email.message import EmailMessage

from app.core.config import settings

logger = logging.getLogger(__name__)


def _enviar_email(*, para: str, assunto: str, corpo: str) -> bool:
    if not settings.smtp_username or not settings.smtp_password:
        logger.warning("Email não enviado (SMTP não configurado): %s -> %s", assunto, para)
        return False
    msg = EmailMessage()
    msg["Subject"] = assunto
    msg["From"] = settings.smtp_username
    msg["To"] = para
    msg.set_content(corpo)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
        smtp.ehlo()
        if settings.smtp_use_tls:
            smtp.starttls()
            smtp.ehlo()
        smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(msg)
    logger.info("Email enviado: %s -> %s", assunto, para)
    return True


class EmailService:
    @staticmethod
    def enviar_verificacao(email: str, token: str) -> bool:
        corpo = (
            "Olá!\n\n"
            "Para verificar seu email no Plantões App, use o token abaixo:\n\n"
            f"  {token}\n\n"
            "Este token expira em 24 horas.\n\n"
            "Se você não criou uma conta, ignore este email."
        )
        return _enviar_email(
            para=email,
            assunto="Verificação de email — Plantões App",
            corpo=corpo,
        )

    @staticmethod
    def enviar_reset_senha(email: str, token: str) -> bool:
        corpo = (
            "Olá!\n\n"
            "Recebemos uma solicitação de redefinição de senha para sua conta.\n\n"
            "Use o token abaixo para redefinir sua senha (válido por 1 hora):\n\n"
            f"  {token}\n\n"
            "Se você não solicitou a redefinição, ignore este email."
        )
        return _enviar_email(
            para=email,
            assunto="Redefinição de senha — Plantões App",
            corpo=corpo,
        )
