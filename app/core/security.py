import hashlib
import hmac
import secrets
from uuid import uuid4
from datetime import UTC, datetime, timedelta

from jose import jwt, JWTError

from app.core.config import settings

_ITERATIONS = 100_000


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


def create_access_token(user_id: int) -> str:
    """Gera token de acesso JWT que identifica o usuário e vence após tempo configurado."""
    expires_at = datetime.now(UTC) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {
        "sub": str(user_id),
        "jti": str(uuid4()),
        "exp": expires_at,
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

        return {"user_id": int(subject), "jti": str(token_id), "exp": expires_at}
    except (JWTError, ValueError, TypeError):
        return None
