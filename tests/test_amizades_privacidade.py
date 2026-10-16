"""Exposição de dados pessoais na listagem de amizades (auditoria C-09).

POST /api/amizades aceita qualquer destinatario_usuario_id e não exigia nada
além de autenticação. GET /api/amizades devolvia, para CADA amizade — inclusive
as meramente pendentes —, um resumo do outro usuário contendo telefone. Somadas,
as duas coisas permitiam varrer a base inteira: criar uma solicitação para cada
id e ler nome, email, telefone e avatar de todos os usuários.
"""

import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from tests.fixtures_usuario import criar_usuario_no_banco


class TestAmizadesPrivacidade(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def criar_usuario(self, telefone=None):
        sufixo = uuid4().hex[:8]
        if telefone is None:
            telefone = f"119{uuid4().int % 100000000:08d}"
        # AUD-011: POST /api/usuarios foi removido; o usuário é gravado direto no banco.
        usuario_id, headers = criar_usuario_no_banco(
            f"priv-{sufixo}@example.com", "Usuário Privacidade", telefone=telefone
        )
        return usuario_id, headers, telefone

    def _amigo_na_listagem(self, headers, amizade_id=None):
        resposta = self.client.get("/api/amizades", headers=headers)
        self.assertEqual(resposta.status_code, 200, resposta.text)
        itens = resposta.json()["data"]
        self.assertTrue(itens, "esperava ao menos uma amizade na listagem")
        if amizade_id is not None:
            itens = [i for i in itens if i["id"] == amizade_id]
            self.assertTrue(itens)
        return itens[0]["amigo"]

    def test_solicitacao_pendente_nao_expoe_telefone_do_alvo(self):
        _, headers_a, _ = self.criar_usuario()
        id_b, _, telefone_b = self.criar_usuario()

        # A dispara uma solicitação unilateral para B. B não aceitou nada.
        criada = self.client.post(
            "/api/amizades", headers=headers_a, json={"destinatario_usuario_id": id_b}
        )
        self.assertEqual(criada.status_code, 201, criada.text)

        amigo = self._amigo_na_listagem(headers_a)

        self.assertIsNone(
            amigo.get("telefone"),
            "telefone só pode aparecer depois que a amizade for aceita",
        )
        # Garantia direta contra o vazamento, independente do nome do campo.
        self.assertNotIn(telefone_b, str(amigo))

    def test_telefone_aparece_depois_de_aceita(self):
        id_a, headers_a, _ = self.criar_usuario()
        id_b, headers_b, telefone_b = self.criar_usuario()

        criada = self.client.post(
            "/api/amizades", headers=headers_a, json={"destinatario_usuario_id": id_b}
        )
        amizade_id = criada.json()["data"]["id"]

        aceita = self.client.patch(f"/api/amizades/{amizade_id}/aceitar", headers=headers_b)
        self.assertEqual(aceita.status_code, 200, aceita.text)

        amigo = self._amigo_na_listagem(headers_a, amizade_id)
        self.assertEqual(amigo.get("telefone"), telefone_b)

    def test_solicitacao_recusada_volta_a_esconder_o_telefone(self):
        _, headers_a, _ = self.criar_usuario()
        id_b, headers_b, telefone_b = self.criar_usuario()

        criada = self.client.post(
            "/api/amizades", headers=headers_a, json={"destinatario_usuario_id": id_b}
        )
        amizade_id = criada.json()["data"]["id"]
        self.client.patch(f"/api/amizades/{amizade_id}/rejeitar", headers=headers_b)

        resposta = self.client.get("/api/amizades", headers=headers_a)
        for item in resposta.json()["data"]:
            if item["id"] == amizade_id:
                self.assertIsNone(item["amigo"].get("telefone"))
                self.assertNotIn(telefone_b, str(item["amigo"]))


if __name__ == "__main__":
    unittest.main()


class TestVarreduraLimitada(unittest.TestCase):
    """O limitador de requisições existia mas era desligado fora de produção por
    uma comparação fixa, então nenhuma proteção contra varredura ou força bruta
    chegava a ser exercitada em teste (auditoria A-12)."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def setUp(self):
        from app.core import rate_limiter
        from app.core.config import settings

        self._valor_anterior = settings.rate_limit_enabled
        settings.rate_limit_enabled = "true"
        rate_limiter.reset()

    def tearDown(self):
        from app.core import rate_limiter
        from app.core.config import settings

        settings.rate_limit_enabled = self._valor_anterior
        rate_limiter.reset()

    def _autenticar(self):
        sufixo = uuid4().hex[:8]
        return criar_usuario_no_banco(
            f"scan-{sufixo}@example.com", "Usuário Varredura",
            telefone=f"119{uuid4().int % 100000000:08d}",
        )[1]

    def test_criar_amizade_em_sequencia_acaba_bloqueado(self):
        headers = self._autenticar()

        codigos = [
            self.client.post(
                "/api/amizades", headers=headers, json={"destinatario_usuario_id": alvo}
            ).status_code
            for alvo in range(1, 40)
        ]

        self.assertIn(429, codigos, "varrer ids em sequência precisa esbarrar no limite")

    def test_busca_por_prefixo_curto_e_recusada(self):
        headers = self._autenticar()

        # Com min_length=1, percorrer prefixos devolvia a tabela de emails de
        # cinco em cinco resultados.
        resposta = self.client.get("/api/usuarios/busca?email=a", headers=headers)

        self.assertEqual(resposta.status_code, 422, resposta.text)
