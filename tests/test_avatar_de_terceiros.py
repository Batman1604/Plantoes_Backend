"""Avatar visto por OUTROS usuários.

O app grava em `usuarios.avatar` o caminho local da foto no aparelho do dono
(a foto fica só lá, A-18). Esse caminho chegava aos amigos — na busca, na
listagem de amizades, no pull e em /amigos/plantoes — sem servir para nada e
expondo a estrutura de pastas do aparelho de outra pessoa. Para terceiros,
passa só URL https (foto do Google) e os avatares embutidos no app; a resposta
do próprio usuário não muda.
"""

import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.schemas.plantao_schema import avatar_visivel_a_terceiros
from tests.fixtures_usuario import criar_usuario_no_banco

CAMINHO_LOCAL = "/data/user/0/br.com.plantoes/app_flutter/img_persons/avatar_1760000000000.jpg"


class TestAvatarVisivelATerceiros(unittest.TestCase):
    def test_regra(self):
        self.assertIsNone(avatar_visivel_a_terceiros(CAMINHO_LOCAL))
        self.assertIsNone(avatar_visivel_a_terceiros("C:/Users/fulano/Pictures/eu.png"))
        self.assertIsNone(avatar_visivel_a_terceiros("http://exemplo.com/foto.png"))
        self.assertIsNone(avatar_visivel_a_terceiros("file:///sdcard/foto.png"))
        self.assertIsNone(avatar_visivel_a_terceiros(""))
        self.assertIsNone(avatar_visivel_a_terceiros(None))
        google = "https://lh3.googleusercontent.com/a/foto=s96-c"
        self.assertEqual(avatar_visivel_a_terceiros(google), google)
        self.assertEqual(avatar_visivel_a_terceiros("img_persons/person_girl2.png"), "img_persons/person_girl2.png")


class TestAvatarNasRespostasDeTerceiros(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def _amigos_com_avatar(self, avatar_b):
        sufixo = uuid4().hex[:8]
        id_a, h_a = criar_usuario_no_banco(f"av-a-{sufixo}@example.com")
        id_b, h_b = criar_usuario_no_banco(f"av-b-{sufixo}@example.com")
        perfil = self.client.patch("/api/usuarios/perfil", headers=h_b, json={"avatar": avatar_b})
        self.assertEqual(perfil.status_code, 200, perfil.text)
        # A resposta do próprio dono continua com o caminho local.
        self.assertEqual(perfil.json()["data"]["avatar"], avatar_b)

        amizade = self.client.post("/api/amizades", headers=h_a, json={"destinatario_usuario_id": id_b}).json()["data"]
        self.client.patch(f"/api/amizades/{amizade['id']}/aceitar", headers=h_b)
        local = self.client.post("/api/locais", headers=h_b, json={
            "nome_local": "Hospital Avatar", "data_inicio": "2026-01-01", "data_final": "2026-01-31",
        }).json()["data"]
        criado = self.client.post("/api/plantoes", headers=h_b, json={
            "local_id": local["id"], "data_inicio": "2026-07-17T07:00:00",
            "data_fim": "2026-07-17T19:00:00", "valor": "1200.00",
        })
        self.assertEqual(criado.status_code, 201, criado.text)
        return f"av-b-{sufixo}@example.com", h_a, h_b

    def _vistos_por_a(self, email_b, h_a):
        # A busca exclui quem já é amigo: quem busca B é um terceiro, C.
        _, h_c = criar_usuario_no_banco()
        busca = self.client.get("/api/usuarios/busca", headers=h_c, params={"email": email_b}).json()["data"]
        amizades = self.client.get("/api/amizades", headers=h_a).json()["data"]
        pull = self.client.get("/api/sync/pull", headers=h_a).json()["data"]["amizades"]
        plantoes = self.client.get("/api/amigos/plantoes", headers=h_a).json()["data"]
        self.assertTrue(busca and amizades and pull and plantoes)
        return {
            "busca": busca[0]["avatar"],
            "amizades": amizades[0]["amigo"]["avatar"],
            "pull": pull[0]["amigo"]["avatar"],
            "amigos/plantoes": plantoes[0]["amigo_avatar"],
        }

    def test_caminho_local_nao_chega_ao_amigo(self):
        email_b, h_a, h_b = self._amigos_com_avatar(CAMINHO_LOCAL)
        self.assertEqual(self._vistos_por_a(email_b, h_a), dict.fromkeys(
            ("busca", "amizades", "pull", "amigos/plantoes"), None))
        # E o próprio dono continua vendo o dele.
        self.assertEqual(self.client.get("/api/usuarios", headers=h_b).json()["avatar"], CAMINHO_LOCAL)

    def test_avatar_embutido_no_app_continua_chegando(self):
        email_b, h_a, _ = self._amigos_com_avatar("img_persons/person_boy3.png")
        self.assertEqual(set(self._vistos_por_a(email_b, h_a).values()), {"img_persons/person_boy3.png"})


if __name__ == "__main__":
    unittest.main()
