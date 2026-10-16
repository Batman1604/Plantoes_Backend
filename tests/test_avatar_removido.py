"""A-18: upload de avatar e /uploads removidos; a foto fica só no aparelho."""

import unittest

from fastapi.testclient import TestClient

from app.main import app
from tests.fixtures_usuario import criar_usuario_no_banco


class TestAvatarSoNoAparelho(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.uid, cls.headers = criar_usuario_no_banco()

    def test_rotas_de_upload_nao_existem(self):
        envio = self.client.post(
            "/api/usuarios/avatar", headers=self.headers,
            files={"file": ("a.png", b"\x89PNG\r\n", "image/png")},
        )
        self.assertIn(envio.status_code, (404, 405), envio.text)
        remocao = self.client.delete("/api/usuarios/avatar", headers=self.headers)
        self.assertIn(remocao.status_code, (404, 405), remocao.text)

    def test_uploads_nao_e_servido(self):
        self.assertEqual(self.client.get(f"/uploads/avatars/{self.uid}.webp").status_code, 404)
        self.assertFalse(any(getattr(r, "path", None) == "/uploads" for r in app.routes))

    def test_perfil_continua_aceitando_o_caminho_local(self):
        caminho = "/data/user/0/app/files/avatar.jpg"
        r = self.client.patch("/api/usuarios/perfil", headers=self.headers, json={"avatar": caminho})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["data"]["avatar"], caminho)


if __name__ == "__main__":
    unittest.main()
