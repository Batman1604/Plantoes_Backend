import os
import unittest
from pathlib import Path
from unittest.mock import patch


from fastapi.testclient import TestClient

from app.main import app


class TestCadastroPendente(unittest.TestCase):
    """Cobre /auth/cadastro, /auth/confirmar-cadastro e /auth/reenviar-cadastro:
    a conta só deve existir de verdade depois que o código é confirmado."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        db_file = Path("test_cadastro_pendente.db")
        try:
            if db_file.exists():
                db_file.unlink()
        except PermissionError:
            pass

    def _iniciar_cadastro(self, email, telefone="11999990000", codigo="123456"):
        with patch("app.routes.plantao_routes.secrets.randbelow", return_value=int(codigo)):
            response = self.client.post(
                "/api/auth/cadastro",
                json={"nome": "Usuário Teste", "email": email, "telefone": telefone, "senha": "SenhaForte123"},
            )
        return response

    def _login_falha(self, email):
        response = self.client.post("/api/auth/login", json={"email": email, "senha": "SenhaForte123"})
        self.assertEqual(response.status_code, 401, response.text)

    def test_conta_so_existe_apos_confirmar_codigo(self):
        email = "pendente@example.com"

        resposta = self._iniciar_cadastro(email)
        self.assertEqual(resposta.status_code, 200, resposta.text)
        self.assertIsNone(resposta.json()["data"])

        # Antes de confirmar, a conta não existe de verdade.
        self._login_falha(email)

        # Código errado não cria a conta.
        errado = self.client.post("/api/auth/confirmar-cadastro", json={"email": email, "codigo": "000000"})
        self.assertEqual(errado.status_code, 400, errado.text)
        self._login_falha(email)

        # Código certo cria a conta e já devolve token de acesso.
        certo = self.client.post("/api/auth/confirmar-cadastro", json={"email": email, "codigo": "123456"})
        self.assertEqual(certo.status_code, 201, certo.text)
        data = certo.json()["data"]
        self.assertTrue(data["autenticado"])
        self.assertTrue(data["usuario"]["telefone_verificado"])
        self.assertNotIn("access_token", [None, ""])

        login = self.client.post("/api/auth/login", json={"email": email, "senha": "SenhaForte123"})
        self.assertEqual(login.status_code, 200, login.text)

        # Cadastro pendente foi consumido — confirmar de novo não repete a criação.
        repetir = self.client.post("/api/auth/confirmar-cadastro", json={"email": email, "codigo": "123456"})
        self.assertEqual(repetir.status_code, 400, repetir.text)

    def test_reenviar_cadastro_invalida_codigo_anterior(self):
        email = "reenvio@example.com"
        self._iniciar_cadastro(email, telefone="11955550000", codigo="111111")

        with patch("app.routes.plantao_routes.secrets.randbelow", return_value=222222):
            reenviar = self.client.post("/api/auth/reenviar-cadastro", json={"email": email})
        self.assertEqual(reenviar.status_code, 200, reenviar.text)

        antigo = self.client.post("/api/auth/confirmar-cadastro", json={"email": email, "codigo": "111111"})
        self.assertEqual(antigo.status_code, 400, antigo.text)

        novo = self.client.post("/api/auth/confirmar-cadastro", json={"email": email, "codigo": "222222"})
        self.assertEqual(novo.status_code, 201, novo.text)

    def test_iniciar_cadastro_com_email_ja_confirmado_retorna_409(self):
        email = "existente@example.com"
        criado = self.client.post(
            "/api/usuarios",
            json={"nome": "Já existe", "email": email, "telefone": "11988880000", "senha": "SenhaForte123"},
        )
        self.assertEqual(criado.status_code, 201, criado.text)

        resposta = self._iniciar_cadastro(email, telefone="11977770000")
        self.assertEqual(resposta.status_code, 409, resposta.text)

    def test_falha_no_envio_do_codigo_nao_deixa_nada_pendente(self):
        email = "falha-envio@example.com"
        with patch("app.services.sms_service.SmsService.enviar_codigo_verificacao", return_value=False):
            resposta = self.client.post(
                "/api/auth/cadastro",
                json={"nome": "Usuário Teste", "email": email, "telefone": "11966660000", "senha": "SenhaForte123"},
            )
        self.assertEqual(resposta.status_code, 502, resposta.text)

        # Falha no envio não deixa conta nenhuma criada.
        self._login_falha(email)


if __name__ == "__main__":
    unittest.main()