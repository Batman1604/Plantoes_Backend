import logging
import smtplib
from email.message import EmailMessage

from app.core.config import settings
from app.core.mascaras import mascarar_email
from app.models import User


logger = logging.getLogger(__name__)


class FeedbackService:
    @staticmethod
    def enviar_feedback(usuario: User, mensagem: str) -> None:
        """Envia o feedback do usuário para o email configurado da equipe de desenvolvimento."""
        if not settings.smtp_username or not settings.smtp_password:
            raise RuntimeError("Configuração SMTP ausente. Defina SMTP_USERNAME e SMTP_PASSWORD.")
        if not settings.feedback_email_to:
            # AUD-012: sem default (era um e-mail pessoal no código-fonte).
            raise RuntimeError("Destino do feedback ausente. Defina FEEDBACK_EMAIL_TO.")

        msg = EmailMessage()
        msg["Subject"] = f"Feedback do app - usuário {usuario.id}"
        msg["From"] = settings.smtp_username
        msg["To"] = settings.feedback_email_to
        msg["Reply-To"] = usuario.email

        corpo = (
            "Novo feedback enviado pelo aplicativo.\n\n"
            f"Usuário ID: {usuario.id}\n"
            f"Nome: {usuario.nome}\n"
            f"Email: {usuario.email}\n"
            f"Telefone: {usuario.telefone or 'Não informado'}\n"
            f"Plano: {usuario.plano}\n\n"
            "Mensagem:\n"
            f"{mensagem}\n"
        )
        msg.set_content(corpo)

        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
            smtp.ehlo()
            if settings.smtp_use_tls:
                smtp.starttls()
                smtp.ehlo()
            smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(msg)

        logger.info("Feedback enviado por %s para %s", mascarar_email(usuario.email), settings.feedback_email_to)
