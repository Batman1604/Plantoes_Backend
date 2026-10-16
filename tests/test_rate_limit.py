"""A-12: IP real só de proxy confiável; limite por usuário além do IP."""

import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from starlette.requests import Request

from app.core import rate_limiter
from app.core.config import settings
from app.main import app
from tests.fixtures_usuario import criar_usuario_no_banco


def _requisicao(peer: str, encaminhado: str | None = None) -> Request:
    headers = [(b"x-forwarded-for", encaminhado.encode())] if encaminhado else []
    return Request({"type": "http", "client": (peer, 1234), "headers": headers, "path": "/x"})


class TestIpReal(unittest.TestCase):
    def setUp(self):
        self._anterior = settings.trusted_proxy_ips
        self.addCleanup(setattr, settings, "trusted_proxy_ips", self._anterior)

    def test_sem_proxy_confiavel_ignora_x_forwarded_for(self):
        settings.trusted_proxy_ips = ""
        self.assertEqual(rate_limiter.ip_real(_requisicao("203.0.113.9", "1.2.3.4")), "203.0.113.9")

    def test_peer_nao_listado_nao_consegue_forjar(self):
        settings.trusted_proxy_ips = "10.0.0.1"
        self.assertEqual(rate_limiter.ip_real(_requisicao("203.0.113.9", "1.2.3.4")), "203.0.113.9")

    def test_proxy_confiavel_entrega_o_cliente(self):
        settings.trusted_proxy_ips = "10.0.0.1, 10.0.0.2"
        # O cliente forjou "6.6.6.6"; o proxy de borda acrescentou o IP real.
        r = _requisicao("10.0.0.1", "6.6.6.6, 198.51.100.7, 10.0.0.2")
        self.assertEqual(rate_limiter.ip_real(r), "198.51.100.7")


class TestLimitePorUsuario(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def setUp(self):
        self._anterior = settings.rate_limit_enabled
        settings.rate_limit_enabled = "true"
        rate_limiter.reset()
        self.addCleanup(rate_limiter.reset)
        self.addCleanup(setattr, settings, "rate_limit_enabled", self._anterior)

    def test_feedback_estoura_por_conta_sem_travar_a_outra_do_mesmo_ip(self):
        _, headers_a = criar_usuario_no_banco(f"fa-{uuid4().hex[:8]}@example.com")
        _, headers_b = criar_usuario_no_banco(f"fb-{uuid4().hex[:8]}@example.com")
        with patch("app.services.feedback_service.FeedbackService.enviar_feedback", return_value=None):
            codigos_a = [
                self.client.post("/api/feedback", headers=headers_a, json={"mensagem": "Mensagem de teste"}).status_code
                for _ in range(6)
            ]
            b = self.client.post("/api/feedback", headers=headers_b, json={"mensagem": "Mensagem de teste"})
        self.assertEqual(codigos_a, [200] * 5 + [429])
        self.assertEqual(b.status_code, 200, b.text)

    def test_sync_push_tem_limite_por_conta(self):
        _, headers = criar_usuario_no_banco()
        codigos = [
            self.client.post("/api/sync/push", headers=headers, json={}).status_code for _ in range(61)
        ]
        self.assertEqual(codigos[:60], [200] * 60)
        self.assertEqual(codigos[60], 429)


if __name__ == "__main__":
    unittest.main()
