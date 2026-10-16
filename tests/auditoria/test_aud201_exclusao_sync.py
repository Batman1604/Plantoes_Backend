"""AUD-201 (P0): excluir UM plantão pela sincronização apaga a série inteira.

`SyncService._push_plantao` chama `deletar_plantao(...)` como função Python, sem
passar `aplicar_em`. O default do parâmetro é o objeto `Query("somente_este")`,
que é diferente da string "somente_este", e a rota cai no ramo
`list_futuros_da_serie` — que, para plantões avulsos, casa qualquer plantão do
mesmo local e horário a partir daquela data.

Cenários: agente 2 S-01 / S-15.
"""

import unittest
from datetime import timedelta

import aud_helpers as h


class TestAud201ExclusaoPeloSync(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = h.cliente()

    def _tres_avulsos(self):
        _, headers, *_ = h.criar_usuario(self.client)
        local = h.criar_local(self.client, headers)
        plantoes = [
            h.criar_plantao(self.client, headers, local["id"], data=d)
            for d in ("2026-07-17", "2026-07-24", "2026-07-31")
        ]
        return headers, local, plantoes

    def test_push_exclui_somente_o_plantao_enviado__AUD201(self):
        """Falhava antes da correção (2026-10-15); hoje é regressão: os três somem e o pull traz três exclusões."""
        headers, local, plantoes = self._tres_avulsos()
        marcador = h.pull(self.client, headers)["servidor_timestamp"]
        primeiro = plantoes[0]

        resposta = self.client.post("/api/sync/push", headers=headers, json={"plantoes": [{
            "uuid_cliente": primeiro["uuid_cliente"] or "aud201-p1",
            "id": primeiro["id"],
            "local_id": local["id"],
            "data_inicio": "2026-07-17T07:00:00",
            "data_fim": "2026-07-17T19:00:00",
            "atualizado_em": h.agora_iso(timedelta(minutes=1)),
            "deletado": True,
        }]})
        self.assertEqual(resposta.status_code, 200, resposta.text)

        restantes = sorted(p["id"] for p in h.listar_plantoes(self.client, headers))
        self.assertEqual(
            restantes, sorted(p["id"] for p in plantoes[1:]),
            "excluir um plantão pelo sync não pode levar junto os outros dois avulsos",
        )
        exclusoes = [e for e in h.pull(self.client, headers, since=marcador)["exclusoes"]
                     if e["entidade"] == "plantao"]
        self.assertEqual([e["id"] for e in exclusoes], [primeiro["id"]])

    def test_push_exclui_um_da_serie_semanal_e_preserva_os_outros__AUD201(self):
        """Falhava antes da correção (2026-10-15); hoje é regressão: série semanal criada com recorrência perde todos os futuros."""
        _, headers, *_ = h.criar_usuario(self.client)
        local = h.criar_local(self.client, headers)
        serie = [
            h.criar_plantao(self.client, headers, local["id"], data=d, recorrencia="Semanal")
            for d in ("2026-08-07", "2026-08-14", "2026-08-21")
        ]
        alvo = serie[1]
        resposta = self.client.post("/api/sync/push", headers=headers, json={"plantoes": [{
            "uuid_cliente": alvo["uuid_cliente"] or "aud201-s2",
            "id": alvo["id"],
            "local_id": local["id"],
            "data_inicio": "2026-08-14T07:00:00",
            "data_fim": "2026-08-14T19:00:00",
            "recorrencia": "Semanal",
            "atualizado_em": h.agora_iso(timedelta(minutes=1)),
            "deletado": True,
        }]})
        self.assertEqual(resposta.status_code, 200, resposta.text)
        restantes = sorted(p["id"] for p in h.listar_plantoes(self.client, headers))
        self.assertEqual(restantes, sorted([serie[0]["id"], serie[2]["id"]]))


class TestAud201RegressaoRest(unittest.TestCase):
    """S-15: o DELETE REST sem query exclui só um (PASSA hoje). Protege a
    correção do AUD-201 de trocar o default para "este_e_futuros"."""

    @classmethod
    def setUpClass(cls):
        cls.client = h.cliente()

    def test_delete_rest_sem_query_exclui_somente_um(self):
        _, headers, *_ = h.criar_usuario(self.client)
        local = h.criar_local(self.client, headers)
        plantoes = [
            h.criar_plantao(self.client, headers, local["id"], data=d)
            for d in ("2026-07-17", "2026-07-24", "2026-07-31")
        ]
        resposta = self.client.delete(f"/api/plantoes/{plantoes[0]['id']}", headers=headers)
        self.assertEqual(resposta.status_code, 200, resposta.text)
        self.assertEqual(len(h.listar_plantoes(self.client, headers)), 2)


if __name__ == "__main__":
    unittest.main()
