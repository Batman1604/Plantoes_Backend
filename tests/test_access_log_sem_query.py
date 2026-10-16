"""AUD-033 (resto): o access log do uvicorn gravava a query string.

/api/usuarios/busca leva o e-mail e o telefone procurados na query string, e o
uvicorn registra o caminho completo em cada requisição. O contrato com o app
não muda (a busca continua por query string); o que muda é o que vai ao log.

O teste principal sobe o servidor de verdade com `uvicorn app.main:app`, num
subprocesso, porque o que importa é o filtro valer depois que o próprio
uvicorn configura o logging — e o TestClient não passa pelo access log.
"""

import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

from app.core.mascaras import mascarar_query_string

REPO = Path(__file__).resolve().parents[1]


def test_mascarar_query_string_mantem_chaves_e_esconde_valores():
    assert mascarar_query_string("/api/usuarios/busca?email=a%40b.com&telefone=11999998888") == (
        "/api/usuarios/busca?email=***&telefone=***"
    )
    assert mascarar_query_string("/api/usuarios/busca?fulano%40b.com") == "/api/usuarios/busca?***"
    assert mascarar_query_string("/api/plantoes") == "/api/plantoes"


def _porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_access_log_do_uvicorn_nao_grava_email_nem_telefone_da_busca():
    porta = _porta_livre()
    email = "fulana.busca@example.com"
    telefone = "11987654321"
    with tempfile.TemporaryDirectory() as tmp:
        env = {k: v for k, v in os.environ.items() if not k.startswith(("WHATSAPP_", "SMTP_"))}
        env.update(
            DATABASE_URL=f"sqlite:///{Path(tmp, 'x.db').as_posix()}",
            ENVIRONMENT="development",
            JWT_SECRET_KEY="segredo-de-teste-com-tamanho-suficiente",
            PYTHONPATH=str(REPO),
            PYTHONUNBUFFERED="1",
        )
        # Saída para arquivo, não PIPE: com PIPE sem leitor, o buffer do pipe
        # enche (o worker de notificação loga um traceback no banco vazio) e o
        # processo filho trava segurando o lock do logging.
        arquivo_saida = Path(tmp, "saida.log")
        saida_fd = open(arquivo_saida, "w", encoding="utf-8")
        processo = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(porta)],
            cwd=tmp, env=env, stdout=saida_fd, stderr=subprocess.STDOUT,
        )
        try:
            resposta = None
            limite = time.monotonic() + 60
            while time.monotonic() < limite:
                try:
                    resposta = httpx.get(
                        f"http://127.0.0.1:{porta}/api/usuarios/busca",
                        params={"email": email, "telefone": telefone},
                        timeout=2,
                    )
                    break
                except httpx.TransportError:
                    if processo.poll() is not None:
                        break
                    time.sleep(0.3)
            # Sem token: 401. O que interessa é a linha do access log.
            assert resposta is not None, "o servidor não subiu"
            assert resposta.status_code == 401
            time.sleep(0.5)
        finally:
            processo.terminate()
            processo.wait(timeout=30)
            saida_fd.close()
        saida = arquivo_saida.read_text(encoding="utf-8", errors="replace")

    linhas_da_busca = [linha for linha in saida.splitlines() if "/api/usuarios/busca" in linha]
    assert linhas_da_busca, saida[-3000:]
    registro = "\n".join(linhas_da_busca)
    assert "email=***" in registro and "telefone=***" in registro, registro
    assert "fulana.busca" not in saida and "%40example.com" not in saida, registro
    assert telefone not in saida, registro
