"""Código de 6 dígitos por e-mail, entregue e conferido pelo Supabase Auth.

O Supabase serve APENAS de carteiro: usuários, sessões (JWT próprio) e
tabelas continuam no backend. Usa só SUPABASE_URL e SUPABASE_ANON_KEY (a
chave anon é pública por natureza); a service_role não é usada.

- `enviar(email)`: POST {SUPABASE_URL}/auth/v1/otp. Com `create_user: true`,
  o Supabase cria o usuário dele se for a primeira vez e manda o e-mail.
- `verificar(email, codigo)`: POST {SUPABASE_URL}/auth/v1/verify com
  `type: "email"`. Um código só passa UMA vez; a sessão do Supabase que volta
  na resposta é descartada.

Erros: 429 vira `LimiteDeEnvio` (com os segundos, quando o Supabase diz);
qualquer outra falha do Supabase vira `FalhaNoSupabase`, registrada no log
com o e-mail mascarado. Código errado ou expirado não é erro: `verificar`
devolve False e quem chama conta a tentativa.
"""

import logging
import re

import httpx

from app.core.config import settings
from app.core.mascaras import mascarar_email

logger = logging.getLogger(__name__)

TIMEOUT_SEGUNDOS = 10

# "For security purposes, you can only request this after 37 seconds."
_SEGUNDOS_NA_MENSAGEM = re.compile(r"(\d+)\s*seconds?", re.IGNORECASE)


class LimiteDeEnvio(Exception):
    """O Supabase recusou por limite (429). `segundos` é None quando ele não
    disse quanto esperar."""

    def __init__(self, segundos: int | None):
        super().__init__(f"Limite de envio do Supabase (aguardar {segundos} s)")
        self.segundos = segundos


class FalhaNoSupabase(Exception):
    """Fora do ar, timeout, chave errada ou recusa que não é de código."""


def _corpo(resposta: httpx.Response) -> dict:
    try:
        corpo = resposta.json()
    except ValueError:
        return {}
    return corpo if isinstance(corpo, dict) else {}


def _codigo_de_erro(resposta: httpx.Response) -> str:
    """Só o código do erro vai para o log, nunca a mensagem: a do Supabase
    pode trazer o e-mail completo ('Email address "x@y" is invalid')."""
    corpo = _corpo(resposta)
    return str(corpo.get("error_code") or corpo.get("error") or "?")


def _segundos_de_espera(resposta: httpx.Response) -> int | None:
    retry_after = resposta.headers.get("retry-after", "")
    if retry_after.isdigit():
        return int(retry_after)
    corpo = _corpo(resposta)
    mensagem = str(corpo.get("msg") or corpo.get("error_description") or corpo.get("message") or "")
    achado = _SEGUNDOS_NA_MENSAGEM.search(mensagem)
    return int(achado.group(1)) if achado else None


def _post(caminho: str, corpo: dict, email: str, acao: str) -> httpx.Response:
    url = f"{settings.supabase_url.rstrip('/')}/auth/v1/{caminho}"
    try:
        resposta = httpx.post(
            url,
            json=corpo,
            headers={"apikey": settings.supabase_anon_key},
            timeout=TIMEOUT_SEGUNDOS,
        )
    except httpx.HTTPError as exc:
        logger.exception("Supabase Auth não respondeu ao %s para %s", acao, mascarar_email(email))
        raise FalhaNoSupabase(f"Supabase Auth não respondeu ao {acao}") from exc
    if resposta.status_code == 429:
        segundos = _segundos_de_espera(resposta)
        logger.warning(
            "Supabase Auth limitou o %s para %s (%s; aguardar %s s)",
            acao, mascarar_email(email), _codigo_de_erro(resposta), segundos,
        )
        raise LimiteDeEnvio(segundos)
    if resposta.status_code >= 500 or resposta.status_code == 401:
        # 401: chave anon errada ou de outro projeto — configuração, não código.
        logger.error(
            "Supabase Auth falhou no %s para %s: HTTP %s (%s)",
            acao, mascarar_email(email), resposta.status_code, _codigo_de_erro(resposta),
        )
        raise FalhaNoSupabase(f"Supabase Auth respondeu HTTP {resposta.status_code} ao {acao}")
    return resposta


class EmailOtpService:
    @staticmethod
    def configurado() -> bool:
        return bool(settings.supabase_url and settings.supabase_anon_key)

    @staticmethod
    def enviar(email: str) -> None:
        """Pede ao Supabase o envio do código. Levanta LimiteDeEnvio ou
        FalhaNoSupabase; sem exceção, o Supabase aceitou o envio."""
        resposta = _post("otp", {"email": email, "create_user": True}, email, "envio do código")
        if resposta.status_code >= 300:
            # 4xx que não é limite: e-mail recusado pelo Supabase (ex.: o
            # e-mail embutido só entrega para membros da organização), envio
            # desligado no painel... Nada chegou ao usuário.
            logger.error(
                "Supabase Auth recusou o envio do código para %s: HTTP %s (%s)",
                mascarar_email(email), resposta.status_code, _codigo_de_erro(resposta),
            )
            raise FalhaNoSupabase(f"Supabase Auth recusou o envio: HTTP {resposta.status_code}")
        logger.info("Código enviado por e-mail (Supabase) para %s", mascarar_email(email))

    @staticmethod
    def verificar(email: str, codigo: str) -> bool:
        """True se o Supabase aceitou o código. False se ele está errado,
        expirado ou já foi usado. Levanta LimiteDeEnvio ou FalhaNoSupabase."""
        resposta = _post(
            "verify", {"type": "email", "email": email, "token": codigo}, email, "verificação do código"
        )
        if resposta.status_code < 300:
            return True
        # 403 otp_expired é o caso normal (errado, expirado ou já usado). Os
        # demais 4xx também significam "este código não vale", mas ficam no log
        # para não esconder uma mudança de contrato do Supabase.
        if resposta.status_code != 403:
            logger.warning(
                "Supabase Auth recusou o código de %s com HTTP %s (%s)",
                mascarar_email(email), resposta.status_code, _codigo_de_erro(resposta),
            )
        return False
