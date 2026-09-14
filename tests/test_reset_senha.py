import os
import unittest
from pathlib import Path
from unittest.mock import patch


from fastapi.testclient import TestClient

from app.main import app


class TestResetSenha(unittest.TestCase):
    """Cobre o fluxo de redefinição de senha por código de 6 dígitos: /auth/forgot-password
    (ou /auth/esqueci-senha), /auth/verificar-codigo-senha e /auth/reset-password (ou
    /auth/redefinir-senha)."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        db_file = Path("test_reset_senha.db")
        try:
            if db_file.exists():
                db_file.unlink()
        except PermissionError:
            pass

    def _criar_usuario(self, email, telefone):
        resposta = self.client.post(
            "/api/usuarios",
            json={"nome": "Usuário Teste", "email": email, "telefone": telefone, "senha": "SenhaAntiga123"},
        )
        self.assertEqual(resposta.status_code, 201, resposta.text)

    def _pedir_codigo(self, email, codigo="654321", path="/api/auth/forgot-password"):
        with patch("app.routes.plantao_routes.secrets.randbelow", return_value=int(codigo)):
            resposta = self.client.post(path, json={"email": email})
        self.assertEqual(resposta.status_code, 200, resposta.text)
        return resposta

    def test_fluxo_completo_en_forgot_verify_reset(self):
        email = "reset-en@example.com"
        self._criar_usuario(email, "11999990001")
        self._pedir_codigo(email, codigo="111222", path="/api/auth/forgot-password")

        errado = self.client.post("/api/auth/verificar-codigo-senha", json={"email": email, "code": "000000"})
        self.assertEqual(errado.status_code, 400, errado.text)

        certo = self.client.post("/api/auth/verificar-codigo-senha", json={"email": email, "code": "111222"})
        self.assertEqual(certo.status_code, 200, certo.text)

        # Verificar o código não deve consumi-lo.
        certo_de_novo = self.client.post("/api/auth/verificar-codigo-senha", json={"email": email, "code": "111222"})
        self.assertEqual(certo_de_novo.status_code, 200, certo_de_novo.text)

        reset = self.client.post(
            "/api/auth/reset-password",
            json={"email": email, "code": "111222", "newPassword": "SenhaNova456"},
        )
        self.assertEqual(reset.status_code, 200, reset.text)

        login_senha_antiga = self.client.post("/api/auth/login", json={"email": email, "senha": "SenhaAntiga123"})
        self.assertEqual(login_senha_antiga.status_code, 401, login_senha_antiga.text)

        login_senha_nova = self.client.post("/api/auth/login", json={"email": email, "senha": "SenhaNova456"})
        self.assertEqual(login_senha_nova.status_code, 200, login_senha_nova.text)

        # Código já foi consumido pelo reset — não pode ser reutilizado.
        reset_de_novo = self.client.post(
            "/api/auth/reset-password",
            json={"email": email, "code": "111222", "newPassword": "OutraSenha789"},
        )
        self.assertEqual(reset_de_novo.status_code, 400, reset_de_novo.text)

    def test_fluxo_completo_pt_esqueci_redefinir(self):
        email = "reset-pt@example.com"
        self._criar_usuario(email, "11999990002")
        self._pedir_codigo(email, codigo="333444", path="/api/auth/esqueci-senha")

        redefinir = self.client.post(
            "/api/auth/redefinir-senha",
            json={"email": email, "codigo": "333444", "nova_senha": "SenhaNova456"},
        )
        self.assertEqual(redefinir.status_code, 200, redefinir.text)

        login = self.client.post("/api/auth/login", json={"email": email, "senha": "SenhaNova456"})
        self.assertEqual(login.status_code, 200, login.text)

    def test_email_inexistente_retorna_404(self):
        resposta = self.client.post("/api/auth/forgot-password", json={"email": "nao-existe@example.com"})
        self.assertEqual(resposta.status_code, 404, resposta.text)
        self.assertIn("Verifique se digitou certo", resposta.json()["message"])

        verificar = self.client.post(
            "/api/auth/verificar-codigo-senha", json={"email": "nao-existe@example.com", "code": "654321"}
        )
        self.assertEqual(verificar.status_code, 400, verificar.text)

    def test_pedir_novo_codigo_invalida_o_anterior(self):
        email = "reenvio-reset@example.com"
        self._criar_usuario(email, "11999990003")
        self._pedir_codigo(email, codigo="111111", path="/api/auth/forgot-password")
        self._pedir_codigo(email, codigo="222222", path="/api/auth/forgot-password")

        antigo = self.client.post("/api/auth/verificar-codigo-senha", json={"email": email, "code": "111111"})
        self.assertEqual(antigo.status_code, 400, antigo.text)

        novo = self.client.post("/api/auth/verificar-codigo-senha", json={"email": email, "code": "222222"})
        self.assertEqual(novo.status_code, 200, novo.text)


if __name__ == "__main__":
    unittest.main()
