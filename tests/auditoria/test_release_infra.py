"""Release / infraestrutura do backend — agente 7 C-03, C-04, C-05, C-06, C-08 e
agente 5 S19 (AUD-505).

- AUD-703 / AUD-505 (P1): `ENVIRONMENT` tem default "development" e a
  comparação é exata ("production"); sem a variável, ou escrita diferente,
  todas as guardas de produção ficam desligadas. Testado num SUBPROCESSO com
  cwd temporário (sem ler o `.env` do repositório) e DATABASE_URL SQLite
  temporário — nunca toca no Postgres.
- C-04 (regressão): com ENVIRONMENT=production, falta de JWT_SECRET_KEY ou
  ALLOWED_ORIGINS=* derrubam o import.
- AUD-709: /health não olha o banco. C-05 é a regressão do worker.
- AUD-704: a política publicada não cita o que o app coleta/compartilha.
"""

import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy.exc import OperationalError

import aud_helpers as h

REPO = Path(__file__).resolve().parents[2]


def _importar_app(**env_extra) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items()
           if k not in ("ENVIRONMENT", "JWT_SECRET_KEY", "ALLOWED_ORIGINS", "DATABASE_URL")}
    with tempfile.TemporaryDirectory() as tmp:
        env["DATABASE_URL"] = f"sqlite:///{Path(tmp, 'x.db').as_posix()}"
        env["PYTHONPATH"] = str(REPO)
        env.update(env_extra)
        return subprocess.run(
            [sys.executable, "-c", "import app.main"],
            cwd=tmp, env=env, capture_output=True, text=True, timeout=120,
        )


class TestGuardasDeAmbiente(unittest.TestCase):
    def test_sem_environment_o_import_recusa_subir__AUD703(self):
        """Falhava antes da correção (2026-10-15); hoje é regressão: sobe como development com o segredo JWT público."""
        r = _importar_app()
        self.assertNotEqual(r.returncode, 0, "subiu sem ENVIRONMENT e sem JWT_SECRET_KEY")

    def test_environment_com_grafia_diferente_nao_desliga_guardas__AUD505(self):
        """Falhava antes da correção (2026-10-15); hoje é regressão: 'Production' (P maiúsculo) não é 'production'."""
        r = _importar_app(ENVIRONMENT="Production")
        self.assertNotEqual(r.returncode, 0, "subiu com ENVIRONMENT=Production e JWT padrão")

    def test_producao_sem_jwt_recusa__C04(self):
        """Regressão (PASSA). Dente: com ENVIRONMENT=development o mesmo import sobe (ver relatório)."""
        r = _importar_app(ENVIRONMENT="production")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("JWT_SECRET_KEY", r.stderr)

    def test_producao_com_cors_aberto_recusa__C04(self):
        """Regressão (PASSA)."""
        r = _importar_app(ENVIRONMENT="production", JWT_SECRET_KEY="x" * 40, ALLOWED_ORIGINS="*")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("ALLOWED_ORIGINS", r.stderr)


class TestHealth(unittest.TestCase):
    """O worker real roda a cada 60 s sobre o banco de teste; aqui ele é
    trocado por um laço inerte, para o teste não mexer em dados de outros."""

    def setUp(self):
        import app.main as main_mod
        self._parar = threading.Event()
        p = patch.object(main_mod, "notificacao_worker", lambda: self._parar.wait(30))
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self._parar.set)

    def test_health_ok_com_worker_vivo__C05(self):
        """Regressão (PASSA). Dente: sem iniciar a thread, 503 (ver relatório)."""
        with h.cliente() as client:
            r = client.get("/health")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["status"], "ok")

    def test_health_responde_503_com_banco_fora__AUD709(self):
        """Falhava antes da correção (2026-10-15); hoje é regressão: /health não consulta o banco."""
        import app.main as main_mod
        from app.core import database

        def _fora(*a, **k):
            raise OperationalError("SELECT 1", {}, Exception("banco fora"))

        with patch.object(database.engine, "connect", _fora), \
             patch.object(main_mod, "SessionLocal", _fora), \
             patch.object(database, "SessionLocal", _fora):
            with h.cliente() as client:
                r = client.get("/health")
        self.assertEqual(r.status_code, 503, r.text)


class TestPoliticaPublicada(unittest.TestCase):
    @unittest.skip("adiado pelo usuário: Play Store (AUD-013)")
    def test_politica_menciona_o_que_o_app_coleta_e_compartilha__AUD704(self):
        """Falhava antes da correção (2026-10-15); hoje é regressão. Verificação textual mínima — o texto jurídico é decisão do usuário."""
        r = h.cliente().get("/politicas")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/html", r.headers["content-type"])
        texto = r.text.lower()
        exigidos = {
            "telefone": ["telefone"],
            "foto de perfil": ["foto"],
            "Crashlytics/Firebase": ["crashlytics", "firebase"],
            "WhatsApp": ["whatsapp"],
            "assistente de IA (Anthropic)": ["anthropic", "inteligência artificial"],
            "compartilhamento com amigos": ["amigo"],
            "como excluir a conta": ["excluir sua conta", "exclusão da conta", "excluir a conta"],
        }
        faltando = [nome for nome, termos in exigidos.items() if not any(t in texto for t in termos)]
        self.assertEqual(faltando, [], "a política não cita estes itens")


if __name__ == "__main__":
    unittest.main()
