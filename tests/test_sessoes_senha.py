"""AUD-031: trocar/redefinir a senha derruba as sessões; cadeia de refresh limitada."""

import time
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.services.oauth_service as oauth_service
from app.core.database import SessionLocal
from app.core.security import IDADE_MAXIMA_DA_SESSAO, create_access_token, decode_access_token
from app.main import app
from app.repositories.plantao_repository import CodigoResetSenhaRepository
from tests.fixtures_usuario import SENHA, criar_usuario_no_banco


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class TestSessoesAposSenha(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def _status(self, headers) -> int:
        return self.client.get("/api/auth/status", headers=headers).status_code

    def test_alterar_senha_derruba_token_anterior_e_novo_login_vale(self):
        email = f"troca-{time.time_ns()}@example.com"
        _, headers = criar_usuario_no_banco(email)
        r = self.client.patch("/api/usuarios/senha", headers=headers, json={
            "senha_atual": SENHA, "nova_senha": "NovaSenha456",
        })
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self._status(headers), 401)

        login = self.client.post("/api/auth/login", json={"email": email, "senha": "NovaSenha456"})
        self.assertEqual(login.status_code, 200, login.text)
        self.assertEqual(self._status(_bearer(login.json()["data"]["access_token"])), 200)

    def test_reset_password_derruba_token_anterior(self):
        email = f"reset-{time.time_ns()}@example.com"
        uid, headers = criar_usuario_no_banco(email)
        with SessionLocal() as db:
            CodigoResetSenhaRepository.upsert(db, uid, "123456", datetime.now(UTC) + timedelta(minutes=5))
        r = self.client.post("/api/auth/reset-password", json={
            "email": email, "code": "123456", "newPassword": "OutraSenha456",
        })
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self._status(headers), 401)

    def test_refresh_rotaciona_e_preserva_o_login_original(self):
        uid, _ = criar_usuario_no_banco()
        login_original = (datetime.now(UTC) - timedelta(days=2)).timestamp()
        antigo = create_access_token(uid, autenticado_em=login_original)
        r = self.client.post("/api/auth/refresh", headers=_bearer(antigo))
        self.assertEqual(r.status_code, 200, r.text)
        novo = r.json()["data"]["access_token"]
        self.assertAlmostEqual(decode_access_token(novo)["auth_time"], login_original, places=3)
        self.assertEqual(self._status(_bearer(antigo)), 401, "o token renovado continua valendo")
        self.assertEqual(self._status(_bearer(novo)), 200)

    def test_refresh_alem_da_idade_maxima_recusa(self):
        uid, _ = criar_usuario_no_banco()
        velho = (datetime.now(UTC) - IDADE_MAXIMA_DA_SESSAO - timedelta(minutes=1)).timestamp()
        token = create_access_token(uid, autenticado_em=velho)
        self.assertEqual(self._status(_bearer(token)), 200, "premissa: o token em si ainda vale")
        r = self.client.post("/api/auth/refresh", headers=_bearer(token))
        self.assertEqual(r.status_code, 401, r.text)


class TestVinculoOAuthDerrubaSessoes(unittest.TestCase):
    """AUD-010 + AUD-031: ao anular a senha de quem criou a conta com o e-mail
    alheio, o token que essa pessoa já tinha também cai."""

    def test_token_de_quem_cadastrou_antes_cai(self):
        from tests.test_auth_oauth import _FAKE_JWKS, _google_claims, _sign

        client = TestClient(app)
        email = f"presequestro-{time.time_ns()}@example.com"
        _, headers_invasor = criar_usuario_no_banco(email)
        with patch.object(oauth_service, "_get_jwks", return_value=_FAKE_JWKS):
            token = _sign(_google_claims(sub=f"g-{time.time_ns()}", email=email))
            r = client.post("/api/auth/google", json={"id_token": token})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(client.get("/api/auth/status", headers=headers_invasor).status_code, 401)
        dono = _bearer(r.json()["data"]["access_token"])
        self.assertEqual(client.get("/api/auth/status", headers=dono).status_code, 200)


if __name__ == "__main__":
    unittest.main()
