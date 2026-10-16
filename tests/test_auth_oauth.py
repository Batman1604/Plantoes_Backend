import json
import os
import time
import unittest
from unittest.mock import patch

os.environ["GOOGLE_CLIENT_ID"] = "test-google-client"
os.environ["APPLE_CLIENT_ID"] = "test-apple-client"

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
import jwt
from jwt.algorithms import RSAAlgorithm

import app.services.oauth_service as oauth_service
from app.main import app
from tests.fixtures_usuario import criar_usuario_no_banco

_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PRIVATE_PEM = _PRIVATE_KEY.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
).decode()
_JWK = json.loads(RSAAlgorithm.to_jwk(_PRIVATE_KEY.public_key()))
_JWK["kid"] = "test-kid"
_FAKE_JWKS = {"keys": [_JWK]}


def _sign(claims: dict) -> str:
    payload = {"exp": int(time.time()) + 300, **claims}
    return jwt.encode(payload, _PRIVATE_PEM, algorithm="RS256", headers={"kid": "test-kid"})


def _google_claims(**overrides) -> dict:
    claims = {
        "iss": "https://accounts.google.com",
        "aud": "test-google-client",
        "sub": "google-user-default",
        "email": "default_google@example.com",
        "email_verified": True,
        "name": "Usuário Google",
        "picture": "https://example.com/avatar.png",
    }
    claims.update(overrides)
    return claims


def _apple_claims(**overrides) -> dict:
    claims = {
        "iss": "https://appleid.apple.com",
        "aud": "test-apple-client",
        "sub": "apple-user-default",
        "email": "default_apple@example.com",
        "email_verified": True,
    }
    claims.update(overrides)
    return claims


class TestAuthOAuth(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls._jwks_patch = patch.object(oauth_service, "_get_jwks", return_value=_FAKE_JWKS)
        cls._jwks_patch.start()

    @classmethod
    def tearDownClass(cls):
        cls._jwks_patch.stop()

    # ---------------------------------------------------------------
    # Google
    # ---------------------------------------------------------------

    def test_google_cria_novo_usuario(self):
        token = _sign(_google_claims(sub="google-new-1", email="ganhou_conta@example.com"))
        response = self.client.post("/api/auth/google", json={"id_token": token})
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        self.assertTrue(data["autenticado"])
        self.assertEqual(data["usuario"]["email"], "ganhou_conta@example.com")
        self.assertTrue(data["access_token"])

    def test_google_login_repetido_mesmo_google_id_retorna_mesma_conta(self):
        token = _sign(_google_claims(sub="google-repeat-1", email="repete@example.com"))
        primeiro = self.client.post("/api/auth/google", json={"id_token": token})
        segundo = self.client.post("/api/auth/google", json={"id_token": token})
        self.assertEqual(primeiro.status_code, 200)
        self.assertEqual(segundo.status_code, 200)
        self.assertEqual(primeiro.json()["data"]["usuario"]["id"], segundo.json()["data"]["usuario"]["id"])

    def test_google_vincula_conta_email_senha_existente_quando_email_verificado(self):
        # AUD-010: a senha só sobrevive ao vínculo quando a conta JÁ tinha o
        # e-mail verificado (o dono provou a posse antes). Antes este teste usava
        # POST /api/usuarios, que cria a conta com e-mail NÃO verificado — e
        # afirmava que a senha continuava valendo, que é o próprio pré-sequestro.
        usuario_id, _ = criar_usuario_no_banco("vincular@example.com", "Conta Senha", email_verificado=True)

        token = _sign(_google_claims(sub="google-link-1", email="vincular@example.com"))
        resposta = self.client.post("/api/auth/google", json={"id_token": token})
        self.assertEqual(resposta.status_code, 200, resposta.text)
        self.assertEqual(resposta.json()["data"]["usuario"]["id"], usuario_id)

        # Login normal por senha continua funcionando (vínculo não quebra o login antigo).
        login = self.client.post("/api/auth/login", json={"email": "vincular@example.com", "senha": "SenhaForte123"})
        self.assertEqual(login.status_code, 200)
        self.assertEqual(login.json()["data"]["usuario"]["id"], usuario_id)

    def test_google_vincula_conta_com_email_nao_verificado_anula_a_senha(self):
        """AUD-010: quem cadastrou antes com o e-mail alheio (nunca verificado)
        perde a senha quando o dono real entra com o Google."""
        usuario_id, _ = criar_usuario_no_banco("presequestro@example.com", "Conta Invasor")

        token = _sign(_google_claims(sub="google-presequestro-1", email="presequestro@example.com"))
        resposta = self.client.post("/api/auth/google", json={"id_token": token})
        self.assertEqual(resposta.status_code, 200, resposta.text)
        self.assertEqual(resposta.json()["data"]["usuario"]["id"], usuario_id)

        login = self.client.post("/api/auth/login", json={"email": "presequestro@example.com", "senha": "SenhaForte123"})
        self.assertEqual(login.status_code, 401)

    def test_google_nao_vincula_quando_email_nao_verificado_retorna_409(self):
        criar_usuario_no_banco("seguro@example.com", "Conta Segura")

        token = _sign(_google_claims(sub="google-naolink-1", email="seguro@example.com", email_verified=False))
        resposta = self.client.post("/api/auth/google", json={"id_token": token})
        self.assertEqual(resposta.status_code, 409)

    def test_google_audiencia_errada_retorna_401(self):
        token = _sign(_google_claims(aud="outro-client-id"))
        resposta = self.client.post("/api/auth/google", json={"id_token": token})
        self.assertEqual(resposta.status_code, 401)

    def test_google_token_expirado_retorna_401(self):
        claims = _google_claims()
        claims["exp"] = int(time.time()) - 60
        token = jwt.encode(claims, _PRIVATE_PEM, algorithm="RS256", headers={"kid": "test-kid"})
        resposta = self.client.post("/api/auth/google", json={"id_token": token})
        self.assertEqual(resposta.status_code, 401)

    def test_google_sem_email_retorna_422(self):
        claims = _google_claims()
        del claims["email"]
        token = _sign(claims)
        resposta = self.client.post("/api/auth/google", json={"id_token": token})
        self.assertEqual(resposta.status_code, 422)

    # ---------------------------------------------------------------
    # Apple
    # ---------------------------------------------------------------

    def test_apple_cria_novo_usuario_com_nome_enviado_pelo_app(self):
        token = _sign(_apple_claims(sub="apple-new-1", email="applenovo@example.com"))
        resposta = self.client.post("/api/auth/apple", json={"identity_token": token, "nome": "Maria Apple"})
        self.assertEqual(resposta.status_code, 200, resposta.text)
        self.assertEqual(resposta.json()["data"]["usuario"]["nome"], "Maria Apple")

    def test_apple_login_subsequente_sem_email_usa_conta_ja_vinculada(self):
        primeiro_token = _sign(_apple_claims(sub="apple-repeat-1", email="appleRepete@example.com"))
        primeiro = self.client.post("/api/auth/apple", json={"identity_token": primeiro_token, "nome": "Repetido"})
        self.assertEqual(primeiro.status_code, 200, primeiro.text)
        usuario_id = primeiro.json()["data"]["usuario"]["id"]

        claims_sem_email = _apple_claims(sub="apple-repeat-1")
        del claims_sem_email["email"]
        segundo_token = _sign(claims_sem_email)
        segundo = self.client.post("/api/auth/apple", json={"identity_token": segundo_token})
        self.assertEqual(segundo.status_code, 200, segundo.text)
        self.assertEqual(segundo.json()["data"]["usuario"]["id"], usuario_id)

    def test_apple_sem_email_e_sem_conta_vinculada_retorna_422(self):
        claims = _apple_claims(sub="apple-nunca-visto")
        del claims["email"]
        token = _sign(claims)
        resposta = self.client.post("/api/auth/apple", json={"identity_token": token})
        self.assertEqual(resposta.status_code, 422)

    def test_apple_vincula_conta_email_senha_existente_quando_email_verificado(self):
        # AUD-011: POST /api/usuarios foi removido; a conta é gravada direto no banco.
        usuario_id, _ = criar_usuario_no_banco("vincularapple@example.com", "Conta Apple Vinculo")

        token = _sign(_apple_claims(sub="apple-link-1", email="vincularapple@example.com"))
        resposta = self.client.post("/api/auth/apple", json={"identity_token": token})
        self.assertEqual(resposta.status_code, 200, resposta.text)
        self.assertEqual(resposta.json()["data"]["usuario"]["id"], usuario_id)

    def test_apple_nao_vincula_quando_email_nao_verificado_retorna_409(self):
        criar_usuario_no_banco("seguroapple@example.com", "Conta Apple Segura")

        token = _sign(_apple_claims(sub="apple-naolink-1", email="seguroapple@example.com", email_verified=False))
        resposta = self.client.post("/api/auth/apple", json={"identity_token": token})
        self.assertEqual(resposta.status_code, 409)

    def test_apple_audiencia_errada_retorna_401(self):
        token = _sign(_apple_claims(aud="outro-bundle-id"))
        resposta = self.client.post("/api/auth/apple", json={"identity_token": token})
        self.assertEqual(resposta.status_code, 401)


if __name__ == "__main__":
    unittest.main()
