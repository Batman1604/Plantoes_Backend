"""AUD-056: o 500 das rotas centrais de plantão registra a causa no log.
Antes o `except Exception` virava HTTPException(500) e o handler registrava só
o `detail` — um IntegrityError ou falha no total do mês sumia."""

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.services.plantao_service import PlantaoService
from tests.fixtures_usuario import criar_usuario_no_banco


class TestLogDoErro500(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, raise_server_exceptions=False)
        _, self.headers = criar_usuario_no_banco()

    def _assert_500_com_causa(self, metodo, url, json, servico):
        with patch.object(PlantaoService, servico, side_effect=RuntimeError("causa-real-AUD056")), \
             self.assertLogs("app.routes.plantao_routes", level="ERROR") as logs:
            r = self.client.request(metodo, url, headers=self.headers, json=json)
        self.assertEqual(r.status_code, 500)
        self.assertIn("causa-real-AUD056", "\n".join(logs.output))

    def test_criar_plantao(self):
        self._assert_500_com_causa("POST", "/api/plantoes", {
            "local_id": 1, "data_inicio": "2026-03-10T07:00:00", "data_fim": "2026-03-10T19:00:00"}, "criar")

    def test_atualizar_plantao(self):
        self._assert_500_com_causa("PATCH", "/api/plantoes/1", {"valor": "10.00"}, "atualizar")


if __name__ == "__main__":
    unittest.main()
