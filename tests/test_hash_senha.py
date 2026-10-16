"""AUD-059: iterações do PBKDF2 atualizadas, com rehash transparente no login."""

import hashlib
import secrets
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from app.core import security
from app.core.database import SessionLocal
from app.main import app
from app.models import User
from tests.fixtures_usuario import SENHA, criar_usuario_no_banco


def _hash_legado(senha: str, iteracoes: int = 100_000) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", senha.encode(), salt.encode(), iteracoes)
    return f"{iteracoes}${salt}${digest.hex()}"


class TestHashDeSenha(unittest.TestCase):
    def test_hash_novo_usa_ao_menos_600_mil_iteracoes(self):
        self.assertGreaterEqual(int(security.hash_password("x" * 8).split("$")[0]), 600_000)

    def test_login_com_hash_legado_refaz_o_hash_e_mantem_a_senha(self):
        client = TestClient(app)
        email = f"legado-{uuid4().hex[:8]}@example.com"
        uid, headers = criar_usuario_no_banco(email)
        with SessionLocal() as db:
            db.get(User, uid).senha_hash = _hash_legado(SENHA)
            db.commit()

        login = client.post("/api/auth/login", json={"email": email, "senha": SENHA})
        self.assertEqual(login.status_code, 200, login.text)
        with SessionLocal() as db:
            novo = db.get(User, uid).senha_hash
        self.assertGreaterEqual(int(novo.split("$")[0]), 600_000)
        self.assertTrue(security.verify_password(SENHA, novo))
        # Rehash não é troca de senha: a sessão aberta continua valendo.
        self.assertEqual(client.get("/api/auth/status", headers=headers).status_code, 200)
        self.assertEqual(client.post("/api/auth/login", json={"email": email, "senha": SENHA}).status_code, 200)

    def test_login_errado_nao_refaz_o_hash(self):
        client = TestClient(app)
        email = f"legado-erro-{uuid4().hex[:8]}@example.com"
        uid, _ = criar_usuario_no_banco(email)
        legado = _hash_legado(SENHA)
        with SessionLocal() as db:
            db.get(User, uid).senha_hash = legado
            db.commit()
        self.assertEqual(client.post("/api/auth/login", json={"email": email, "senha": "Errada12345"}).status_code, 401)
        with SessionLocal() as db:
            self.assertEqual(db.get(User, uid).senha_hash, legado)


if __name__ == "__main__":
    unittest.main()
