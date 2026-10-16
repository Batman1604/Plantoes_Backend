"""AUD-089 (parte não-iOS): segredo do upgrade em tempo constante e audiência OAuth vazia."""

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.services.oauth_service as oauth_service
from app.core.config import settings
from app.main import app
from tests.fixtures_usuario import criar_usuario_no_banco


class TestUpgradeSecret(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.uid, _ = criar_usuario_no_banco()
        p = patch.object(settings, "upgrade_webhook_secret", "segredo-do-gateway")
        p.start()
        self.addCleanup(p.stop)

    def test_segredo_comparado_com_compare_digest(self):
        import app.routes.plantao_routes as rotas

        with patch.object(rotas.hmac, "compare_digest", wraps=rotas.hmac.compare_digest) as comparar:
            errado = self.client.post("/api/usuarios/upgrade", headers={"X-Upgrade-Secret": "outro"},
                                      json={"usuario_id": self.uid})
            certo = self.client.post("/api/usuarios/upgrade", headers={"X-Upgrade-Secret": "segredo-do-gateway"},
                                     json={"usuario_id": self.uid})
        self.assertEqual(errado.status_code, 401, errado.text)
        self.assertEqual(certo.status_code, 200, certo.text)
        self.assertEqual(comparar.call_count, 2)


class TestAudienciaVazia(unittest.TestCase):
    def test_client_id_so_com_virgulas_recusa_o_token(self):
        from tests.test_auth_oauth import _FAKE_JWKS, _google_claims, _sign

        client = TestClient(app)
        token = _sign(_google_claims(sub="g-aud-vazia", email="aud-vazia@example.com", aud="qualquer-app"))
        with patch.object(oauth_service, "_get_jwks", return_value=_FAKE_JWKS), \
             patch.object(settings, "google_client_id", " , "):
            r = client.post("/api/auth/google", json={"id_token": token})
        self.assertEqual(r.status_code, 401, r.text)


if __name__ == "__main__":
    unittest.main()
