"""Achados de segurança que falhavam antes da correção (2026-10-15; hoje são regressão) (agente 5: S5, S6, S7, S10).

- AUD-503 (P1): vincular Google a uma conta com e-mail igual mantém a senha de
  quem cadastrou antes (pré-sequestro). OBS: o teste existente
  `tests/test_auth_oauth.py::test_google_vincula_conta_email_senha_existente_quando_email_verificado`
  afirma o comportamento OPOSTO (login por senha continua) — a correção exige
  rever aquele teste.
- AUD-504 (P1): `POST /api/usuarios` grava telefone sem verificação, e
  `/plantoes/{id}/transferir` vincula a transferência ao dono desse número.
- AUD-506 (P2): solicitação `receber` com `plantao_alvo_id` lê o valor de um
  plantão que o amigo escondeu.
- AUD-508 (P2): redefinir a senha não derruba sessões existentes.

Usuários fictícios; JWKS do Google falso (mesma técnica de test_auth_oauth.py).
"""

import time
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt
from jose.backends.cryptography_backend import CryptographyRSAKey

import app.services.oauth_service as oauth_service
import aud_helpers as h
from app.core.config import settings

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PEM = _KEY.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
).decode()
_JWK = CryptographyRSAKey(_KEY.public_key(), algorithm="RS256").to_dict()
_JWK["kid"] = "aud-kid"


def _token_google(sub: str, email: str) -> str:
    aud = settings.google_client_id.split(",")[0].strip()
    claims = {
        "iss": "https://accounts.google.com", "aud": aud, "sub": sub, "email": email,
        "email_verified": True, "name": "Vitima", "exp": int(time.time()) + 300,
    }
    return jwt.encode(claims, _PEM, algorithm="RS256", headers={"kid": "aud-kid"})


class TestAud503PreSequestro(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = h.cliente()
        cls._p = patch.object(oauth_service, "_get_jwks", return_value={"keys": [_JWK]})
        cls._p.start()

    @classmethod
    def tearDownClass(cls):
        cls._p.stop()

    def test_vinculo_google_anula_senha_de_quem_cadastrou_antes__AUD503(self):
        """Falhava antes da correção (2026-10-15); hoje é regressão (login com a senha do invasor continua 200)."""
        email_vitima = f"vitima-{uuid4().hex[:8]}@example.com"
        id_conta, *_ = h.criar_usuario(self.client, email=email_vitima)  # invasor cadastra com o e-mail da vítima

        google = self.client.post("/api/auth/google", json={"id_token": _token_google(f"g-{uuid4().hex}", email_vitima)})
        self.assertEqual(google.status_code, 200, google.text)
        self.assertEqual(google.json()["data"]["usuario"]["id"], id_conta, "premissa: o Google vinculou à conta existente")

        login_invasor = self.client.post("/api/auth/login", json={"email": email_vitima, "senha": h.SENHA})
        self.assertEqual(login_invasor.status_code, 401, "a senha definida antes do vínculo continua valendo")


class TestAud504TelefoneNaoVerificado(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = h.cliente()

    def test_transferencia_nao_vincula_telefone_nao_verificado__AUD504(self):
        """Falhava antes da correção (2026-10-15); hoje é regressão."""
        tel = h.telefone_aleatorio()
        id_ocupante, *_ = h.criar_usuario(self.client, telefone=tel)  # nunca verificou o número

        _, headers_b, *_ = h.criar_usuario(self.client)
        local = h.criar_local(self.client, headers_b)
        plantao = h.criar_plantao(self.client, headers_b, local["id"])
        resposta = self.client.post(f"/api/plantoes/{plantao['id']}/transferir", headers=headers_b, json={
            "destinatario_nome": "Medico Real", "destinatario_telefone": tel, "canal": "sms",
        })
        self.assertEqual(resposta.status_code, 200, resposta.text)
        self.assertNotEqual(
            resposta.json()["data"]["destinatario_usuario_id"], id_ocupante,
            "a transferência foi entregue a quem só digitou o número no cadastro legado",
        )


class TestAud506PlantaoAlvoOculto(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = h.cliente()

    def test_solicitacao_receber_nao_le_plantao_oculto__AUD506(self):
        """Falhava antes da correção (2026-10-15); hoje é regressão (201 e valor 1234.00 visível)."""
        id_a, headers_a, *_ = h.criar_usuario(self.client)
        id_b, headers_b, *_ = h.criar_usuario(self.client)
        h.tornar_amigos(self.client, id_a, headers_a, id_b, headers_b)
        l1 = h.criar_local(self.client, headers_b, nome="Visivel")
        l2 = h.criar_local(self.client, headers_b, nome="Oculto")
        p2 = h.criar_plantao(self.client, headers_b, l2["id"], valor="1234.00")

        cfg_b = self.client.get("/api/amizades", headers=headers_b).json()["data"][0]["configuracao_id"]
        r = self.client.patch(f"/api/amigo-configuracoes/{cfg_b}", headers=headers_b, json={
            "modo_visibilidade": "personalizado", "locais_permitidos_ids": [l1["id"]], "mostrar_valores": False,
        })
        self.assertEqual(r.status_code, 200, r.text)
        # Controle com dente: a configuração está ativa (A não vê P2 na agenda do amigo).
        vistos = [i["plantao"]["id"] for i in self.client.get("/api/amigos/plantoes", headers=headers_a).json()["data"]]
        self.assertNotIn(p2["id"], vistos)

        criada = self.client.post("/api/solicitacoes-troca", headers=headers_a, json={
            "destinatario_usuario_id": id_b, "tipo": "receber", "plantao_alvo_id": p2["id"],
        })
        if criada.status_code in (403, 404):
            return  # correção aceitável: recusar
        self.assertEqual(criada.status_code, 201, criada.text)
        lista = self.client.get("/api/solicitacoes-troca", headers=headers_a).json()["data"]
        valor = lista[0]["plantao_recebido"]["valor"]
        self.assertEqual(float(valor or 0), 0.0, "A leu o valor de um plantão que B escondeu dele")


class TestAud508SessaoAposRedefinir(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = h.cliente()

    def test_redefinir_senha_derruba_token_antigo__AUD508(self):
        """Falhava antes da correção (2026-10-15); hoje é regressão (o token anterior continua 200)."""
        from app.core.database import SessionLocal
        from app.repositories.plantao_repository import CodigoResetSenhaRepository

        uid, headers, email, *_ = h.criar_usuario(self.client)
        self.assertEqual(self.client.get("/api/auth/status", headers=headers).status_code, 200)
        with SessionLocal() as db:
            CodigoResetSenhaRepository.upsert(db, uid, "123456", datetime.now(UTC) + timedelta(minutes=5))
        # Setup ajustado: /auth/redefinir-senha (duplicata em português) foi
        # removida no AUD-032; a rota que o app e o site usam é reset-password.
        r = self.client.post("/api/auth/reset-password", json={
            "email": email, "code": "123456", "newPassword": "OutraSenha456",
        })
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.client.get("/api/auth/status", headers=headers).status_code, 401)


if __name__ == "__main__":
    unittest.main()
