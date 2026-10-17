"""render.yaml (Blueprint do Render) coerente com o que o backend exige para
subir em produção, sem nenhum segredo no arquivo."""

import sys
import unittest
from pathlib import Path

import yaml

from app.main import app

_BLUEPRINT = Path(__file__).resolve().parents[1] / "render.yaml"


class TestRenderBlueprint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        servicos = yaml.safe_load(_BLUEPRINT.read_text(encoding="utf-8"))["services"]
        cls.servico = next(s for s in servicos if s["name"] == "plantoes-backend")
        cls.env = {v["key"]: v for v in cls.servico["envVars"]}

    def test_migra_antes_de_subir_atras_do_proxy(self):
        start = self.servico["startCommand"]
        self.assertTrue(start.startswith("alembic upgrade head && uvicorn app.main:app"), start)
        for parte in ("--host 0.0.0.0", "--port $PORT", "--proxy-headers", "--forwarded-allow-ips='*'"):
            self.assertIn(parte, start)
        self.assertEqual(self.servico["buildCommand"], "pip install -r requirements.txt")

    def test_health_check_e_uma_rota_que_existe(self):
        caminho = self.servico["healthCheckPath"]
        self.assertIn(caminho, {getattr(rota, "path", None) for rota in app.routes})

    def test_variaveis_de_producao_sem_segredo(self):
        self.assertEqual(self.env["ENVIRONMENT"]["value"], "production")
        self.assertIs(self.env["JWT_SECRET_KEY"].get("generateValue"), True)
        for chave in ("DATABASE_URL", "SUPABASE_URL", "SUPABASE_ANON_KEY"):
            self.assertIs(self.env[chave].get("sync"), False, chave)
            self.assertNotIn("value", self.env[chave], f"{chave} não pode ter valor no arquivo")
        self.assertNotIn("SUPABASE_SERVICE_ROLE_KEY", self.env)
        # "*" derruba o import em produção (app/main.py).
        self.assertNotIn("*", self.env["ALLOWED_ORIGINS"]["value"])
        self.assertEqual(self.env["TRUSTED_PROXY_IPS"]["value"], "*")

    def test_python_do_render_igual_ao_do_venv(self):
        versao = self.env["PYTHON_VERSION"]["value"]
        self.assertEqual(versao.split(".")[:2], [str(sys.version_info.major), str(sys.version_info.minor)])


if __name__ == "__main__":
    unittest.main()
