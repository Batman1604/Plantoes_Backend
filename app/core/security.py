import hashlib
import hmac
import secrets
from uuid import uuid4
from datetime import UTC, datetime, timedelta

from jose import jwt, JWTError

from app.core.config import settings

# AUD-059: 600.000 iterações para PBKDF2-HMAC-SHA256 (recomendação atual da
# OWASP). Eram 100.000. O formato guarda a contagem de cada hash, então os
# antigos continuam verificando e são refeitos no próximo login bem-sucedido
# (ver precisa_rehash e UserRepository.authenticate).
_ITERATIONS = 600_000

# AUD-031: idade máxima de uma cadeia de /auth/refresh, contada do login
# original (claim auth_time). Sem limite, um token roubado renovava para
# sempre. Generoso de propósito: a biometria do app (A-17) depende do refresh.
IDADE_MAXIMA_DA_SESSAO = timedelta(days=30)


def hash_password(password: str) -> str:
    """Gera hash seguro para senha usando PBKDF2-HMAC-SHA256 e salt aleatório."""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), _ITERATIONS)
    return f"{_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, password_hash: str) -> bool:
    """Verifica se a senha fornecida corresponde ao hash armazenado."""
    try:
        iterations_str, salt, expected_hash = password_hash.split("$", 2)
        iterations = int(iterations_str)
    except (ValueError, AttributeError):
        return False

    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations)
    return hmac.compare_digest(digest.hex(), expected_hash)


def precisa_rehash(password_hash: str) -> bool:
    """AUD-059: hash gravado com menos iterações que as atuais."""
    try:
        return int(password_hash.split("$", 1)[0]) < _ITERATIONS
    except (ValueError, AttributeError):
        return False


def create_purpose_token(email: str, purpose: str, expire_minutes: int) -> str:
    """Gera token JWT usado para propósitos específicos como verificação de email ou reset de senha."""
    expires_at = datetime.now(UTC) + timedelta(minutes=expire_minutes)
    payload = {
        "sub": email,
        "purpose": purpose,
        "jti": str(uuid4()),
        "exp": expires_at,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_purpose_token(token: str, purpose: str) -> str | None:
    """Valida um token de propósito e retorna o email se o propósito for compatível."""
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        if payload.get("purpose") != purpose:
            return None
        email = payload.get("sub")
        return str(email) if email else None
    except (JWTError, ValueError):
        return None


def create_access_token(user_id: int, autenticado_em: float | None = None) -> str:
    """Gera token de acesso JWT que identifica o usuário e vence após tempo configurado.

    AUD-031: `iat` (com fração de segundo) permite recusar tokens emitidos antes
    de uma troca de senha; `auth_time` é o instante do login original e passa
    adiante a cada /auth/refresh (`autenticado_em`), limitando a cadeia."""
    agora = datetime.now(UTC)
    expires_at = agora + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {
        "sub": str(user_id),
        "jti": str(uuid4()),
        "exp": expires_at,
        "iat": agora.timestamp(),
        "auth_time": autenticado_em if autenticado_em is not None else agora.timestamp(),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, str | int | datetime] | None:
    """Decodifica um token de acesso JWT e retorna os dados do usuário se válido."""
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        subject = payload.get("sub")
        token_id = payload.get("jti")
        expires_at = payload.get("exp")
        if subject is None:
            return None
        if token_id is None:
            return None
        if expires_at is None:
            return None

        if isinstance(expires_at, (int, float)):
            expires_at = datetime.fromtimestamp(expires_at, tz=UTC)
        elif isinstance(expires_at, datetime):
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
        else:
            return None

        # AUD-031: ausentes em tokens emitidos antes desta versão (None).
        emitido_em = payload.get("iat")
        autenticado_em = payload.get("auth_time")
        return {
            "user_id": int(subject),
            "jti": str(token_id),
            "exp": expires_at,
            "iat": float(emitido_em) if emitido_em is not None else None,
            "auth_time": float(autenticado_em) if autenticado_em is not None else None,
        }
    except (JWTError, ValueError, TypeError):
        return None
