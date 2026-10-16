"""K9 (AUD-064): a busca de usuários não revela e-mails."""

import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from tests.fixtures_usuario import criar_usuario_no_banco


class TestBuscaSemEmail(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def setUp(self):
        self.prefixo = f"k9{uuid4().hex[:6]}"
        self.email_alvo = f"{self.prefixo}.alvo@example.com"
        self.telefone = f"119{uuid4().int % 100000000:08d}"
        self.id_alvo, _ = criar_usuario_no_banco(self.email_alvo, "Alvo", telefone=self.telefone)
        _, self.headers = criar_usuario_no_banco()

    def _buscar(self, **params):
        r = self.client.get("/api/usuarios/busca", headers=self.headers, params=params)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["data"]

    def test_busca_por_prefixo_devolve_email_nulo(self):
        itens = self._buscar(email=self.prefixo)
        self.assertEqual([(i["id"], i["email"]) for i in itens], [(self.id_alvo, None)])
        self.assertIn("email", itens[0], "a chave continua no contrato")

    def test_busca_por_telefone_devolve_email_nulo(self):
        itens = self._buscar(telefone=self.telefone)
        self.assertEqual([(i["id"], i["email"]) for i in itens], [(self.id_alvo, None)])

    def test_busca_pelo_email_exato_devolve_o_email(self):
        itens = self._buscar(email=self.email_alvo.upper())
        self.assertEqual([(i["id"], i["email"]) for i in itens], [(self.id_alvo, self.email_alvo)])


if __name__ == "__main__":
    unittest.main()
