"""python-jose trocado por PyJWT.

O python-jose puxava a `ecdsa`, que tem CVE sem correção. A troca não pode
derrubar sessões: um token HS256 emitido pela versão anterior (python-jose)
precisa continuar válido. O token de compatibilidade abaixo é montado só com
a stdlib, no formato que o python-jose gerava (cabeçalho com chaves
ordenadas, JSON compacto, `exp` inteiro, `iat`/`auth_time` com fração), para
não depender de nenhuma das duas bibliotecas.
"""

import base64
import hashlib
import hmac
import json
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm

import app.services.oauth_service as oauth_service
from app.core.config import settings
from app.core.security import decode_access_token, decode_purpose_token
from app.main import app
from tests.fixtures_usuario import criar_usuario_no_banco

REPO = Path(__file__).resolve().parents[1]


def _b64(dados: bytes) -> str:
    return base64.urlsafe_b64encode(dados).rstrip(b"=").decode()


def _token_hs256_como_o_jose(payload: dict) -> str:
    cabecalho = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":"), sort_keys=True).encode())
    corpo = _b64(json.dumps(payload, separators=(",", ":")).encode())
    assinatura = hmac.new(settings.jwt_secret_key.encode(), f"{cabecalho}.{corpo}".encode(), hashlib.sha256).digest()
    return f"{cabecalho}.{corpo}.{_b64(assinatura)}"


class TestSemPythonJose(unittest.TestCase):
    def test_python_jose_saiu_das_dependencias_e_do_codigo(self):
        linhas = (REPO / "requirements.txt").read_text(encoding="utf-8").lower().splitlines()
        pacotes = [linha for linha in linhas if linha.strip() and not linha.lstrip().startswith("#")]
        self.assertFalse([p for p in pacotes if p.startswith("python-jose")], pacotes)
        self.assertTrue([p for p in pacotes if p.startswith("pyjwt")], pacotes)
        for arquivo in (REPO / "app").rglob("*.py"):
            codigo = arquivo.read_text(encoding="utf-8")
            self.assertNotIn("from jose", codigo, arquivo)
            self.assertNotIn("import jose", codigo, arquivo)


class TestCompatibilidadeDosTokens(unittest.TestCase):
    def test_token_de_acesso_emitido_pelo_python_jose_continua_valido(self):
        user_id, _ = criar_usuario_no_banco()
        agora = time.time()
        token = _token_hs256_como_o_jose({
            "sub": str(user_id), "jti": "11111111-2222-3333-4444-555555555555",
            "exp": int(agora) + 600, "iat": agora, "auth_time": agora,
        })
        dados = decode_access_token(token)
        self.assertIsNotNone(dados)
        self.assertEqual(dados["user_id"], user_id)

        r = TestClient(app).get("/api/usuarios", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(r.status_code, 200, r.text)

    def test_token_de_proposito_emitido_pelo_python_jose_continua_valido(self):
        token = _token_hs256_como_o_jose({
            "sub": "fulano@example.com", "purpose": "email_verification",
            "jti": "x", "exp": int(time.time()) + 600,
        })
        self.assertEqual(decode_purpose_token(token, "email_verification"), "fulano@example.com")

    def test_token_expirado_ou_com_outra_chave_e_recusado(self):
        expirado = _token_hs256_como_o_jose({"sub": "1", "jti": "x", "exp": int(time.time()) - 60})
        self.assertIsNone(decode_access_token(expirado))
        outra_chave = jwt.encode({"sub": "1", "jti": "x", "exp": int(time.time()) + 60}, "outra-chave-com-32-bytes-ou-mais!!", algorithm="HS256")
        self.assertIsNone(decode_access_token(outra_chave))


class TestJwksEscolhePeloKid(unittest.TestCase):
    """O python-jose escolhia a chave do JWKS sozinho; no PyJWT a escolha pelo
    `kid` é explícita (oauth_service._chave_do_token)."""

    def test_token_assinado_por_chave_fora_do_jwks_e_recusado(self):
        chave_do_jwks = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        outra = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = json.loads(RSAAlgorithm.to_jwk(chave_do_jwks.public_key()))
        jwk["kid"] = "kid-certo"
        pem_outra = outra.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
        claims = {"iss": "https://accounts.google.com", "aud": "test-google-client", "sub": "g1",
                  "email": "g1@example.com", "email_verified": True, "exp": int(time.time()) + 300}
        with patch.object(oauth_service, "_get_jwks", return_value={"keys": [jwk]}):
            for kid in ("kid-certo", "kid-desconhecido"):
                token = jwt.encode(claims, pem_outra, algorithm="RS256", headers={"kid": kid})
                with self.assertRaises(oauth_service.OAuthTokenError):
                    oauth_service.OAuthService.verificar_token_google(token)


if __name__ == "__main__":
    unittest.main()
