"""AUD-006 (decisão: desvincular), AUD-025, AUD-055 — contrato K1.

Cenário base: A e B são amigos; A cria o local "Hospital Desvinculo" e um
plantão; A transfere o plantão a B por solicitação de troca; B fica com um
clone que aponta para o local de A (o aceite não foi alterado).

Os testes de tests/auditoria/test_aud202_local_apaga_recebido.py provam que o
clone sobrevive. Estes amarram o resto do contrato K1: o pull de B traz o
plantão com local_id nulo e local_nome preservado, sem tombstone; a exclusão de
conta gera tombstone de amizade/configuração para o amigo; e o financeiro do
local de A deixa de somar o plantão cedido e as bonificações de B.
"""

import unittest
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import event

from app.core.database import engine
from app.main import app

SENHA = "SenhaForte123"


def _usuario(client, email):
    r = client.post("/api/usuarios", json={"nome": email.split("@")[0], "email": email, "senha": SENHA})
    assert r.status_code == 201, r.text
    dados = r.json()["data"]
    return dados["usuario"]["id"], {"Authorization": f"Bearer {dados['access_token']}"}


def _fk_ligadas(caso: unittest.TestCase) -> None:
    """Liga PRAGMA foreign_keys no SQLite durante o teste (como o Postgres)."""
    def _on(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    engine.dispose()
    event.listen(engine, "connect", _on)

    def _desfazer():
        event.remove(engine, "connect", _on)
        engine.dispose()

    caso.addCleanup(_desfazer)


class TestDesvincular(unittest.TestCase):
    def setUp(self):
        _fk_ligadas(self)
        self.client = TestClient(app)

    def _cenario(self, sufixo):
        c = self.client
        id_a, h_a = _usuario(c, f"desv-a-{sufixo}@example.com")
        id_b, h_b = _usuario(c, f"desv-b-{sufixo}@example.com")
        amizade = c.post("/api/amizades", headers=h_a, json={"destinatario_usuario_id": id_b}).json()["data"]["id"]
        self.assertEqual(c.patch(f"/api/amizades/{amizade}/aceitar", headers=h_b).status_code, 200)
        local = c.post("/api/locais", headers=h_a, json={
            "nome_local": "Hospital Desvinculo", "data_inicio": "2026-01-01", "data_final": "2026-12-31",
            "dia_pagamento": 5, "cor": "#123456"}).json()["data"]
        plantao = c.post("/api/plantoes", headers=h_a, json={
            "local_id": local["id"], "data_inicio": "2026-03-15T07:00:00",
            "data_fim": "2026-03-15T19:00:00", "valor": "1200.00"}).json()["data"]
        sid = c.post("/api/solicitacoes-troca", headers=h_a, json={
            "destinatario_usuario_id": id_b, "tipo": "transferir",
            "plantao_transferido_id": plantao["id"]}).json()["data"]["id"]
        self.assertEqual(c.patch(f"/api/solicitacoes-troca/{sid}/aceitar", headers=h_b).status_code, 200)
        clone = next(p for p in c.get("/api/plantoes", headers=h_b).json()["data"] if p["recebido_via_transferencia"])
        return id_a, h_a, id_b, h_b, local, plantao, clone

    def _pull(self, headers, since=None):
        r = self.client.get("/api/sync/pull", headers=headers, params={"since": since} if since else None)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["data"]

    def test_excluir_local_desvincula_o_recebido_e_o_pull_traz_local_nome(self):
        _, h_a, _, h_b, local, plantao_a, clone = self._cenario("local")
        marcador_b = self._pull(h_b)["servidor_timestamp"]
        marcador_a = self._pull(h_a)["servidor_timestamp"]

        self.assertEqual(self.client.delete(f"/api/locais/{local['id']}", headers=h_a).status_code, 200)

        dados_b = self._pull(h_b, since=marcador_b)
        trazido = [p for p in dados_b["plantoes"] if p["id"] == clone["id"]]
        self.assertEqual(len(trazido), 1, "o pull incremental de B precisa trazer o plantão desvinculado")
        self.assertIsNone(trazido[0]["local_id"])
        self.assertEqual(trazido[0]["local_nome"], "Hospital Desvinculo")
        self.assertEqual([e for e in dados_b["exclusoes"] if e["entidade"] == "plantao"], [],
                         "nenhum tombstone para o plantão de B")

        # Tombstones de A só para o que era dele.
        exclusoes_a = {(e["entidade"], e["id"]) for e in self._pull(h_a, since=marcador_a)["exclusoes"]}
        self.assertIn(("plantao", plantao_a["id"]), exclusoes_a)
        self.assertIn(("local", local["id"]), exclusoes_a)
        self.assertNotIn(("plantao", clone["id"]), exclusoes_a)

        # B continua dono do plantão: pode excluí-lo.
        self.assertEqual(self.client.delete(f"/api/plantoes/{clone['id']}", headers=h_b).status_code, 200)

    def test_excluir_conta_de_a_desvincula_e_avisa_b_da_amizade(self):
        id_a, h_a, _, h_b, _, _, clone = self._cenario("conta")
        marcador_b = self._pull(h_b)["servidor_timestamp"]
        config_b = next(c["id"] for c in self._pull(h_b)["amigo_configuracoes"] if c["amigo_usuario_id"] == id_a)

        r = self.client.request("DELETE", "/api/usuarios", headers=h_a, json={"senha_atual": SENHA})
        self.assertEqual(r.status_code, 200, r.text)

        dados_b = self._pull(h_b, since=marcador_b)
        trazido = next(p for p in dados_b["plantoes"] if p["id"] == clone["id"])
        self.assertEqual((trazido["local_id"], trazido["local_nome"]), (None, "Hospital Desvinculo"))
        entidades = {e["entidade"] for e in dados_b["exclusoes"]}
        self.assertIn("amizade", entidades)
        self.assertIn(("amigo_configuracao", config_b), {(e["entidade"], e["id"]) for e in dados_b["exclusoes"]})
        self.assertIn("solicitacao_troca", entidades)

    def test_financeiro_do_local_nao_soma_cedido_nem_bonificacao_de_b__AUD055(self):
        _, h_a, _, h_b, local, _, clone = self._cenario("fin")
        outro = self.client.post("/api/plantoes", headers=h_a, json={
            "local_id": local["id"], "data_inicio": "2026-03-20T07:00:00",
            "data_fim": "2026-03-20T19:00:00", "valor": "300.00"})
        self.assertEqual(outro.status_code, 201, outro.text)

        # B cria bonificação no clone: dispara sync_mes do local de A.
        r = self.client.post("/api/bonificacoes", headers=h_b, json={"plantao_id": clone["id"], "valor": "50.00"})
        self.assertEqual(r.status_code, 201, r.text)

        registros = self.client.get(f"/api/financeiro/local/{local['id']}", headers=h_a).json()["data"]
        self.assertEqual([float(x["valor"]) for x in registros], [300.0])


if __name__ == "__main__":
    unittest.main()
