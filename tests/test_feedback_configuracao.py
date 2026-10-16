"""POST /api/feedback sem configuração respondia 500.

Sem FEEDBACK_EMAIL_TO (sem default desde o AUD-012) ou sem SMTP, o serviço
não tem para onde mandar o e-mail. Isso é configuração ausente, não defeito:
503 com mensagem clara para o app, e o nome da variável no log.
"""

import logging
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from tests.fixtures_usuario import criar_usuario_no_banco


class TestFeedbackSemConfiguracao(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def _enviar(self, **config):
        _, headers = criar_usuario_no_banco()
        with (
            patch.multiple("app.services.feedback_service.settings", **config),
            patch("app.services.feedback_service.smtplib.SMTP", side_effect=AssertionError("não deveria conectar")),
            self.assertLogs("app.routes.plantao_routes", level=logging.ERROR) as capturado,
        ):
            r = self.client.post("/api/feedback", headers=headers, json={"mensagem": "Mensagem de teste"})
        return r, "\n".join(capturado.output)

    def test_sem_feedback_email_to_responde_503_e_registra(self):
        r, registro = self._enviar(smtp_username="u", smtp_password="p", feedback_email_to="")
        self.assertEqual(r.status_code, 503, r.text)
        self.assertIn("não está configurado", r.json()["message"])
        self.assertIn("FEEDBACK_EMAIL_TO", registro)

    def test_sem_smtp_responde_503_e_registra(self):
        r, registro = self._enviar(smtp_username="", smtp_password="", feedback_email_to="equipe@example.com")
        self.assertEqual(r.status_code, 503, r.text)
        self.assertIn("SMTP_USERNAME", registro)


if __name__ == "__main__":
    unittest.main()
