"""Limitador de requisições em memória.

Os contadores vivem NO PROCESSO (A-12): com N workers ou N réplicas, cada um
conta sozinho e o limite efetivo vira N vezes o configurado. Isso está
documentado no README; um limitador compartilhado (Redis) fica para quando o
deploy tiver mais de um processo.
"""

import time
from collections import defaultdict
from threading import Lock
from typing import Callable

from fastapi import Depends, HTTPException, Request

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


def _proxies_confiaveis() -> set[str]:
    return {ip.strip() for ip in settings.trusted_proxy_ips.split(",") if ip.strip()}


def ip_real(request: Request) -> str | None:
    """IP do cliente para o limitador (A-12).

    Atrás de proxy, `request.client.host` é o IP do PROXY: todos os usuários
    dividiam o mesmo limite. X-Forwarded-For só é lido quando a conexão vem de
    um proxy listado em TRUSTED_PROXY_IPS — de qualquer outro peer o cabeçalho
    é forjável e é ignorado. Percorre a lista da direita para a esquerda
    pulando os proxies confiáveis; o primeiro IP que não é proxy é o cliente.
    """
    if request.client is None:
        return None
    peer = request.client.host
    confiaveis = _proxies_confiaveis()
    if peer not in confiaveis:
        return peer
    encaminhado = request.headers.get("x-forwarded-for", "")
    for ip in reversed([parte.strip() for parte in encaminhado.split(",") if parte.strip()]):
        if ip not in confiaveis:
            return ip
    return peer


def _barrar_se_estourou(chave: str, max_requests: int, window_seconds: int) -> None:
    if not _check(chave, max_requests, window_seconds):
        raise HTTPException(
            status_code=429,
            detail="Muitas tentativas. Tente novamente em alguns instantes.",
        )


def rate_limit(max_requests: int = 10, window_seconds: int = 60):
    """Cria dependência FastAPI que limita chamadas por IP e rota."""
    def dependency(request: Request) -> None:
        if not esta_ativo():
            return
        ip = ip_real(request)
        if ip is None:
            return
        _barrar_se_estourou(f"{ip}:{request.url.path}", max_requests, window_seconds)
    return dependency


def rate_limit_por_usuario(obter_usuario: Callable, max_requests: int, window_seconds: int):
    """Limite por USUÁRIO autenticado e rota (A-12), somado ao limite por IP.

    Por IP só, uma conta espalhada em vários IPs não tinha teto, e vários
    usuários atrás do mesmo NAT (a rede do hospital) dividiam um teto só.
    `obter_usuario` é a dependência de autenticação (get_current_user), passada
    por quem usa para o core não importar as rotas; o FastAPI a resolve uma vez
    por requisição, então não há segunda consulta ao banco.
    """
    def dependency(request: Request, usuario=Depends(obter_usuario)) -> None:
        if not esta_ativo():
            return
        _barrar_se_estourou(f"usuario:{usuario.id}:{request.url.path}", max_requests, window_seconds)
    return dependency
