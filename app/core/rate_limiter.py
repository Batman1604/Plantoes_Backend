import time
from collections import defaultdict
from threading import Lock

from fastapi import HTTPException, Request

from app.core.config import settings

_buckets: dict[str, list[float]] = defaultdict(list)
_lock = Lock()


def _check(key: str, max_requests: int, window_seconds: int) -> bool:
    """Verifica e atualiza o contador de requisições para a chave fornecida."""
    now = time.monotonic()
    with _lock:
        ts = _buckets[key]
        cutoff = now - window_seconds
        idx = 0
        while idx < len(ts) and ts[idx] < cutoff:
            idx += 1
        del ts[:idx]
        if len(ts) >= max_requests:
            return False
        ts.append(now)
        return True


def esta_ativo() -> bool:
    """Se o limitador deve agir. Por padrão, só em produção; RATE_LIMIT_ENABLED
    força um dos dois lados, o que permite exercitá-lo em teste."""
    configurado = settings.rate_limit_enabled.strip().lower()
    if configurado in {"1", "true", "yes", "on"}:
        return True
    if configurado in {"0", "false", "no", "off"}:
        return False
    return settings.environment == "production"


def reset() -> None:
    """Zera os contadores. Usado entre testes — sem isto, um teste que estoura o
    limite contamina o seguinte."""
    with _lock:
        _buckets.clear()


def rate_limit(max_requests: int = 10, window_seconds: int = 60):
    """Cria dependência FastAPI que limita chamadas por IP e rota."""
    def dependency(request: Request) -> None:
        if not esta_ativo():
            return
        if request.client is None:
            return
        key = f"{request.client.host}:{request.url.path}"
        if not _check(key, max_requests, window_seconds):
            raise HTTPException(
                status_code=429,
                detail="Muitas tentativas. Tente novamente em alguns instantes.",
            )
    return dependency
