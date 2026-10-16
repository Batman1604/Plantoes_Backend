import logging
import smtplib
from email.message import EmailMessage

from app.core.config import settings
from app.core.mascaras import mascarar_email

logger = logging.getLogger(__name__)


def _enviar_email(*, para: str, assunto: str, corpo: str) -> bool:
    """Função interna para enviar email usando SMTP configurado nas variáveis de ambiente."""
    if not settings.smtp_username or not settings.smtp_password:
        logger.warning("Email não enviado (SMTP não configurado): %s -> %s", assunto, mascarar_email(para))
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
    logger.info("Email enviado: %s -> %s", assunto, mascarar_email(para))
    return True


class EmailService:
    @staticmethod
    def enviar_verificacao(email: str, token: str) -> bool:
        """Envia email de verificação de conta para o usuário."""
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
    def enviar_codigo_whatsapp(email: str, codigo: str) -> bool:
        """Envia o código de autenticação para acesso via WhatsApp."""
        corpo = (
            "Olá!\n\n"
            "Seu código de verificação para acesso via WhatsApp ao Plantões App é:\n\n"
            f"  {codigo}\n\n"
            "Este código é válido por 10 minutos.\n\n"
            "Se você não solicitou este código, ignore este email."
        )
        return _enviar_email(
            para=email,
            assunto="Código de acesso WhatsApp — Plantões App",
            corpo=corpo,
        )

    @staticmethod
    def enviar_reset_senha(email: str, codigo: str) -> bool:
        """Envia código de redefinição de senha por email.

        Devolve se o código chegou a algum canal: o e-mail, ou — só fora de
        produção — o log do servidor, que é o modo de desenvolvimento (C-10:
        a rota responde 502 quando isto devolve False)."""
        corpo = (
            "Olá!\n\n"
            "Recebemos uma solicitação de redefinição de senha para sua conta.\n\n"
            "Use o código abaixo para redefinir sua senha (válido por 1 minuto):\n\n"
            f"  {codigo}\n\n"
            "Se você não solicitou a redefinição, ignore este email."
        )
        enviado = _enviar_email(
            para=email,
            assunto="Redefinição de senha — Plantões App",
            corpo=corpo,
        )
        if not enviado:
            # Sem SMTP configurado não há como o código chegar ao usuário. Em
            # desenvolvimento, registrar o código permite testar o fluxo à mão.
            #
            # A guarda de ambiente NÃO é excesso de zelo: o raciocínio anterior
            # era "só executa quando o envio falhou, então nunca aparece em
            # produção com SMTP funcionando" — mas SMTP cai, e nesse momento o
            # código de redefinição de senha de todo mundo passaria a ser
            # escrito em texto puro no log, que é justamente onde ele não pode
            # estar (mesma classe de problema da auditoria C-10).
            if settings.environment != "production":
                logger.warning(
                    "Email de reset não enviado (SMTP não configurado) — código para %s: %s",
                    mascarar_email(email),
                    codigo,
                )
                # Modo de desenvolvimento: o código está no log, conta como entregue.
                return True
            else:
                logger.error(
                    "Email de reset NÃO enviado para %s: SMTP não configurado em produção.",
                    mascarar_email(email),
                )
        return enviado
