"""A-24: a documentação interativa não pode ficar exposta em produção."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import urls_de_documentacao

_ROTAS = ("/docs", "/redoc", "/openapi.json")


def _status(environment: str) -> dict[str, int]:
    client = TestClient(FastAPI(**urls_de_documentacao(environment)))
    return {rota: client.get(rota).status_code for rota in _ROTAS}


def test_producao_nao_expoe_documentacao():
    assert _status("production") == {rota: 404 for rota in _ROTAS}


def test_desenvolvimento_mantem_documentacao():
    assert _status("development") == {rota: 200 for rota in _ROTAS}


def _openapi_url_do_app_real(environment: str) -> str:
    """Importa o `app` de verdade num subprocesso (cwd temporário, sem `.env`,
    SQLite temporário) e devolve `app.openapi_url`. Testar só a função não pega
    a remoção do `**urls_de_documentacao(...)` de dentro do `FastAPI(...)`."""
    import os
    import subprocess
    import sys
    import tempfile
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    env = {k: v for k, v in os.environ.items()
           if k not in ("ENVIRONMENT", "JWT_SECRET_KEY", "ALLOWED_ORIGINS", "DATABASE_URL")}
    with tempfile.TemporaryDirectory() as tmp:
        env.update(
            DATABASE_URL=f"sqlite:///{Path(tmp, 'x.db').as_posix()}",
            PYTHONPATH=str(repo),
            ENVIRONMENT=environment,
            JWT_SECRET_KEY="segredo-de-teste-com-tamanho-suficiente",
            ALLOWED_ORIGINS="https://exemplo.invalid",
        )
        r = subprocess.run(
            [sys.executable, "-c", "from app.main import app; print(app.openapi_url)"],
            cwd=tmp, env=env, capture_output=True, text=True, timeout=120,
        )
    assert r.returncode == 0, r.stderr[-2000:]
    return r.stdout.strip().splitlines()[-1]


def test_app_real_em_producao_nao_expoe_openapi():
    assert _openapi_url_do_app_real("production") == "None"
