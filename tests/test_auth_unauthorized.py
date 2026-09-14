import os
import unittest
from pathlib import Path


from fastapi.testclient import TestClient

from app.main import app


class TestUnauthorizedResponse(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        db_file = Path("test_auth_unauthorized.db")
        try:
            if db_file.exists():
                db_file.unlink()
        except PermissionError:
            pass

    def assert_unauthorized(self, response):
        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.json(),
            {
                "success": False,
                "message": "Não autenticado",
                "code": "UNAUTHORIZED",
            },
        )

    def test_usuarios_sem_token_retorna_401_padronizado(self):
        response = self.client.get("/api/usuarios")
        self.assert_unauthorized(response)

    def test_locais_com_token_invalido_retorna_401_padronizado(self):
        response = self.client.get(
            "/api/locais",
            headers={"Authorization": "Bearer token-invalido"},
        )
        self.assert_unauthorized(response)

    def test_plantoes_sem_token_retorna_401_padronizado(self):
        response = self.client.get("/api/plantoes")
        self.assert_unauthorized(response)


if __name__ == "__main__":
    unittest.main()
