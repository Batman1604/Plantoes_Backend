"""AUD-501 (P0): /api/sync/pull devolve o telefone de quem nunca aceitou a amizade.

O C-09 escondeu o telefone em GET /api/amizades para amizades pendentes, mas o
pull monta `AmizadeAmigoResumo.model_validate(outro)` sem a mesma regra
(sync_service.py, bloco `itens_amizade`). Cenário: agente 5 S1.
"""

import unittest

import aud_helpers as h


class TestAud501PullTelefone(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = h.cliente()

    def test_pull_nao_expoe_telefone_de_amizade_pendente__AUD501(self):
        """Falhava antes da correção (2026-10-15); hoje é regressão."""
        _, headers_a, *_ = h.criar_usuario(self.client, telefone=h.telefone_aleatorio())
        tel_b = h.telefone_aleatorio()
        id_b, *_ = h.criar_usuario(self.client, telefone=tel_b)

        criada = self.client.post("/api/amizades", headers=headers_a, json={"destinatario_usuario_id": id_b})
        self.assertEqual(criada.status_code, 201, criada.text)

        # Controle: a listagem REST já esconde (C-09) — prova que o cenário é o certo.
        listagem = self.client.get("/api/amizades", headers=headers_a)
        self.assertNotIn(tel_b, listagem.text)

        resposta = self.client.get("/api/sync/pull", headers=headers_a)
        self.assertEqual(resposta.status_code, 200, resposta.text)
        amizades = resposta.json()["data"]["amizades"]
        self.assertEqual(len(amizades), 1)
        self.assertEqual(amizades[0]["status"], "pendente")
        self.assertIsNone(amizades[0]["amigo"].get("telefone"))
        self.assertNotIn(tel_b, resposta.text)

    def test_pull_expoe_telefone_depois_de_aceita(self):
        """Regressão (PASSA): depois de aceita, o telefone aparece no pull.
        Garante que a correção do AUD-501 não esconda o telefone de amigos."""
        id_a, headers_a, *_ = h.criar_usuario(self.client, telefone=h.telefone_aleatorio())
        tel_b = h.telefone_aleatorio()
        id_b, headers_b, *_ = h.criar_usuario(self.client, telefone=tel_b)
        h.tornar_amigos(self.client, id_a, headers_a, id_b, headers_b)

        amizades = h.pull(self.client, headers_a)["amizades"]
        self.assertEqual(amizades[0]["amigo"].get("telefone"), tel_b)


if __name__ == "__main__":
    unittest.main()
