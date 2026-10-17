import os
import unittest
from unittest.mock import patch


from fastapi.testclient import TestClient

from app.main import app
from tests.fixtures_usuario import criar_usuario_no_banco


class TestResetSenha(unittest.TestCase):
    """Cobre o fluxo de redefinição de senha por código de 6 dígitos: /auth/forgot-password,
    /auth/verificar-codigo-senha e /auth/reset-password. As rotas duplicadas em português
    foram removidas (AUD-032)."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def _criar_usuario(self, email, telefone):
        # AUD-011: POST /api/usuarios foi removido; o usuário é gravado direto no banco.
        criar_usuario_no_banco(email, telefone=telefone, senha="SenhaAntiga123")

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
        # Sem Supabase não há token: o app manda o próprio código no reset.
        self.assertIsNone(certo.json()["data"])

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

    def test_rotas_pt_duplicadas_foram_removidas(self):
        """AUD-032: o fluxo em português (esqueci-senha/redefinir-senha) testava o
        mesmo código com orçamento próprio no limitador. Substitui o antigo
        test_fluxo_completo_pt_esqueci_redefinir."""
        email = "reset-pt@example.com"
        self._criar_usuario(email, "11999990002")
        esqueci = self.client.post("/api/auth/esqueci-senha", json={"email": email})
        self.assertEqual(esqueci.status_code, 404, esqueci.text)
        redefinir = self.client.post(
            "/api/auth/redefinir-senha",
            json={"email": email, "codigo": "333444", "nova_senha": "SenhaNova456"},
        )
        self.assertEqual(redefinir.status_code, 404, redefinir.text)

    def test_cinco_erros_invalidam_o_codigo__AUD032(self):
        email = "tentativas-reset@example.com"
        self._criar_usuario(email, "11999990004")
        self._pedir_codigo(email, codigo="424242", path="/api/auth/forgot-password")
        for _ in range(5):
            errado = self.client.post("/api/auth/verificar-codigo-senha", json={"email": email, "code": "000000"})
            self.assertEqual(errado.status_code, 400, errado.text)

        # A sexta tentativa, mesmo com o código certo, é recusada nas duas rotas.
        certo = self.client.post("/api/auth/verificar-codigo-senha", json={"email": email, "code": "424242"})
        self.assertEqual(certo.status_code, 400, certo.text)
        reset = self.client.post(
            "/api/auth/reset-password",
            json={"email": email, "code": "424242", "newPassword": "SenhaNova456"},
        )
        self.assertEqual(reset.status_code, 400, reset.text)

        # Um código novo zera o contador.
        self._pedir_codigo(email, codigo="535353", path="/api/auth/forgot-password")
        novo = self.client.post("/api/auth/verificar-codigo-senha", json={"email": email, "code": "535353"})
        self.assertEqual(novo.status_code, 200, novo.text)

    def test_quatro_erros_ainda_aceitam_o_codigo_certo__AUD032(self):
        email = "tentativas-limite@example.com"
        self._criar_usuario(email, "11999990005")
        self._pedir_codigo(email, codigo="616161", path="/api/auth/forgot-password")
        for _ in range(4):
            self.client.post("/api/auth/verificar-codigo-senha", json={"email": email, "code": "000000"})
        certo = self.client.post("/api/auth/verificar-codigo-senha", json={"email": email, "code": "616161"})
        self.assertEqual(certo.status_code, 200, certo.text)

    def test_cinco_erros_invalidam_o_codigo_de_telefone__AUD032(self):
        from datetime import UTC, datetime, timedelta

        from app.core.database import SessionLocal
        from app.repositories.plantao_repository import VerificacaoTelefoneRepository

        uid, headers = criar_usuario_no_banco(telefone="11999990006")
        with SessionLocal() as db:
            VerificacaoTelefoneRepository.upsert(db, uid, "717171", datetime.now(UTC) + timedelta(minutes=5))
        for _ in range(5):
            errado = self.client.post("/api/auth/verificar-telefone", headers=headers, json={"codigo": "000000"})
            self.assertEqual(errado.status_code, 400, errado.text)
        certo = self.client.post("/api/auth/verificar-telefone", headers=headers, json={"codigo": "717171"})
        self.assertEqual(certo.status_code, 400, certo.text)

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
