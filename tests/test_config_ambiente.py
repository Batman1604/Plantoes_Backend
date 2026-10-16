"""AUD-012: guardas de configuração que falham fechadas.

Cada caso importa `app.core.config` num subprocesso com cwd temporário (sem
ler o `.env` do repositório), com ENVIRONMENT/JWT_SECRET_KEY válidos, e muda só
a variável sob teste.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_VARIAVEIS = ("ENVIRONMENT", "JWT_SECRET_KEY", "DATABASE_URL", "WHATSAPP_TOKEN", "WHATSAPP_APP_SECRET",
              "WHATSAPP_VERIFY_TOKEN", "FEEDBACK_EMAIL_TO")


def _importar_config(**env_extra) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in _VARIAVEIS}
    with tempfile.TemporaryDirectory() as tmp:
        env.update(
            DATABASE_URL=f"sqlite:///{Path(tmp, 'x.db').as_posix()}",
            PYTHONPATH=str(REPO),
            ENVIRONMENT="development",
            JWT_SECRET_KEY="segredo-de-teste-com-tamanho-suficiente",
        )
        env.update(env_extra)
        return subprocess.run(
            [sys.executable, "-c", "from app.core.config import settings; print(repr(settings.feedback_email_to))"],
            cwd=tmp, env=env, capture_output=True, text=True, timeout=120,
        )


def test_configuracao_valida_sobe():
    r = _importar_config()
    assert r.returncode == 0, r.stderr[-2000:]


def test_whatsapp_token_sem_app_secret_recusa():
    r = _importar_config(WHATSAPP_TOKEN="token-falso")
    assert r.returncode != 0
    assert "WHATSAPP_APP_SECRET" in r.stderr


def test_whatsapp_token_com_app_secret_e_verify_token_sobe():
    r = _importar_config(WHATSAPP_TOKEN="token-falso", WHATSAPP_APP_SECRET="segredo-falso",
                         WHATSAPP_VERIFY_TOKEN="verify-falso")
    assert r.returncode == 0, r.stderr[-2000:]


def test_whatsapp_token_sem_verify_token_recusa__AUD089():
    r = _importar_config(WHATSAPP_TOKEN="token-falso", WHATSAPP_APP_SECRET="segredo-falso")
    assert r.returncode != 0
    assert "WHATSAPP_VERIFY_TOKEN" in r.stderr


def test_verify_token_sem_default_publico__AUD089():
    env = {k: v for k, v in os.environ.items() if k not in _VARIAVEIS}
    with tempfile.TemporaryDirectory() as tmp:
        env.update(DATABASE_URL=f"sqlite:///{Path(tmp, 'x.db').as_posix()}", PYTHONPATH=str(REPO),
                   ENVIRONMENT="development", JWT_SECRET_KEY="segredo-de-teste-com-tamanho-suficiente")
        r = subprocess.run(
            [sys.executable, "-c", "from app.core.config import settings; print(repr(settings.whatsapp_verify_token))"],
            cwd=tmp, env=env, capture_output=True, text=True, timeout=120,
        )
    assert r.stdout.strip().splitlines()[-1] == "''"


def test_jwt_ausente_nomeia_a_variavel():
    sem_jwt = {k: v for k, v in os.environ.items() if k not in _VARIAVEIS}
    with tempfile.TemporaryDirectory() as tmp:
        sem_jwt.update(DATABASE_URL=f"sqlite:///{Path(tmp, 'x.db').as_posix()}", PYTHONPATH=str(REPO),
                       ENVIRONMENT="development")
        r = subprocess.run([sys.executable, "-c", "import app.core.config"], cwd=tmp, env=sem_jwt,
                           capture_output=True, text=True, timeout=120)
    assert r.returncode != 0
    assert "JWT_SECRET_KEY" in r.stderr


def test_feedback_sem_email_pessoal_no_codigo():
    r = _importar_config()
    assert r.stdout.strip().splitlines()[-1] == "''"
