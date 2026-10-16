import time

import httpx
import jwt
from jwt import PyJWK, PyJWTError

from app.core.config import settings

GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ("accounts.google.com", "https://accounts.google.com")
APPLE_JWKS_URL = "https://appleid.apple.com/auth/keys"
APPLE_ISSUER = "https://appleid.apple.com"

_JWKS_CACHE_TTL_SECONDS = 3600
_jwks_cache: dict[str, tuple[float, dict]] = {}


class OAuthTokenError(Exception):
    """Erro ao validar um token de identidade emitido por um provedor OAuth externo."""


def _allowed_audiences(client_id_setting: str) -> list[str]:
    return [v.strip() for v in client_id_setting.split(",") if v.strip()]


def _get_jwks(url: str) -> dict:
    """Busca o JWKS (conjunto de chaves públicas) do provedor, com cache em memória por
    processo — evita uma chamada HTTP externa a cada login."""
    cached = _jwks_cache.get(url)
    now = time.monotonic()
    if cached and now - cached[0] < _JWKS_CACHE_TTL_SECONDS:
        return cached[1]

    response = httpx.get(url, timeout=10)
    response.raise_for_status()
    jwks = response.json()
    _jwks_cache[url] = (now, jwks)
    return jwks


def parse_bool_claim(value) -> bool:
    """A claim email_verified vem como bool no Google e, historicamente, como string
    'true'/'false' em alguns SDKs da Apple — normaliza os dois formatos."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() == "true"


def _chave_do_token(token: str, jwks: dict):
    """A chave pública do JWKS cujo `kid` é o do cabeçalho do token. O python-jose
    fazia essa escolha sozinho ao receber o JWKS inteiro; no PyJWT ela é
    explícita. Sem `kid` correspondente, o token é recusado."""
    kid = jwt.get_unverified_header(token).get("kid")
    for jwk in jwks.get("keys", []):
        if kid is not None and jwk.get("kid") == kid:
            return PyJWK(jwk, algorithm="RS256").key
    raise PyJWTError("Nenhuma chave do provedor corresponde ao kid do token.")


def _decode_and_verify(token: str, jwks_url: str, valid_issuers: tuple[str, ...], allowed_audiences: list[str]) -> dict:
    """Valida assinatura (RS256, via JWKS do provedor) e expiração de um ID Token, depois
    confere manualmente issuer e audience — mais explícito e portável entre versões da
    biblioteca JWT do que depender das opções embutidas de audience/issuer
    (que só aceitam um único valor esperado de cada, e o Google usa dois issuers válidos)."""
    try:
        chave = _chave_do_token(token, _get_jwks(jwks_url))
        payload = jwt.decode(token, chave, algorithms=["RS256"], options={"verify_aud": False})
    except (PyJWTError, httpx.HTTPError, ValueError) as exc:
        raise OAuthTokenError("Token inválido ou expirado.") from exc

    if payload.get("iss") not in valid_issuers:
        raise OAuthTokenError("Emissor do token não corresponde ao esperado.")
    # AUD-089: sem o `allowed_audiences and`, uma configuração só com vírgulas
    # (lista vazia) recusa tudo, em vez de pular a checagem de audiência.
    if payload.get("aud") not in allowed_audiences:
        raise OAuthTokenError("Audiência do token não corresponde a este aplicativo.")
    if not payload.get("sub"):
        raise OAuthTokenError("Token não contém identificador de usuário (sub).")

    return payload


class OAuthService:
    """Validação de identidade de provedores OAuth externos (Google, Apple).

    Não implementa um mecanismo de sessão próprio: apenas confirma quem é o usuário
    perante o provedor. A sessão do app continua sendo o JWT emitido por
    app.core.security.create_access_token, igual ao login por email/senha.
    """

    @staticmethod
    def verificar_token_google(id_token: str) -> dict:
        """Retorna o payload do ID Token do Google (sub, email, email_verified, name,
        picture) após validar assinatura, emissor e audiência."""
        if not settings.google_client_id:
            raise OAuthTokenError("Login com Google não está configurado no servidor.")
        return _decode_and_verify(
            id_token, GOOGLE_JWKS_URL, GOOGLE_ISSUERS, _allowed_audiences(settings.google_client_id)
        )

    @staticmethod
    def verificar_token_apple(identity_token: str) -> dict:
        """Retorna o payload do Identity Token da Apple (sub, email, email_verified) após
        validar assinatura, emissor e audiência."""
        if not settings.apple_client_id:
            raise OAuthTokenError("Login com Apple não está configurado no servidor.")
        return _decode_and_verify(
            identity_token, APPLE_JWKS_URL, (APPLE_ISSUER,), _allowed_audiences(settings.apple_client_id)
        )
