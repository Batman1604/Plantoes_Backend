"""Contrato de /api/sync (push/pull/tombstone/conflito) — agente 3 P6/P7/P8/P10,
agente 2 S-05/S-06/S-07.

Regressões que devem PASSAR hoje: P6 (bonificação de topo no pull), P7
(tombstone + 410), P10 (conflito por igualdade).
Defeitos que falhavam antes da correção (2026-10-15; hoje são regressão): AUD-309 (TrocaResponse sem campos),
AUD-204 (worker reescreve atualizado_em e a edição vira conflito),
AUD-206 (recálculo usa regra diferente da criação).
AUD-205/308 (cursor sem margem) é DEMONSTRATIVO: simula o commit tardio
gravando a linha direto no banco.
"""

import unittest
from datetime import datetime, timedelta

import aud_helpers as h


class TestContratoRegressao(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = h.cliente()

    def _usuario_com_plantao(self):
        _, headers, *_ = h.criar_usuario(self.client)
        local = h.criar_local(self.client, headers)
        plantao = h.criar_plantao(self.client, headers, local["id"])
        return headers, local, plantao

    def test_bonificacao_nova_chega_no_pull_pela_lista_de_topo__P6(self):
        """PASSA: o servidor cumpre o contrato; o cliente é que ignora a lista (AUD-107/307)."""
        headers, _, plantao = self._usuario_com_plantao()
        # Setup (correção AUD-023): o pull recua o `since` em 5 s; o plantão
        # precisa ser mais velho que essa margem para "não ter mudado".
        from sqlalchemy import update
        from app.core.database import SessionLocal
        from app.models import Plantao
        from datetime import UTC
        with SessionLocal() as db:
            db.execute(update(Plantao).where(Plantao.id == plantao["id"])
                       .values(atualizado_em=datetime.now(UTC) - timedelta(minutes=1)))
            db.commit()
        marcador = h.pull(self.client, headers)["servidor_timestamp"]
        r = self.client.post("/api/sync/push", headers=headers, json={"bonificacoes": [{
            "uuid_cliente": "p6-bon", "plantao_id": plantao["id"], "valor": "75.00",
            "atualizado_em": h.agora_iso()}]})
        self.assertEqual(len(r.json()["data"]["bonificacoes_criadas"]), 1, r.text)

        dados = h.pull(self.client, headers, since=marcador)
        self.assertEqual([b["uuid_cliente"] for b in dados["bonificacoes"]], ["p6-bon"])
        self.assertEqual(dados["plantoes"], [], "o plantão em si não mudou")

    def test_bonificacao_excluida_gera_tombstone_e_recriacao_recebe_410__P7(self):
        headers, _, plantao = self._usuario_com_plantao()
        base = {"uuid_cliente": "p7-bon", "plantao_id": plantao["id"], "valor": "75.00"}
        self.client.post("/api/sync/push", headers=headers, json={"bonificacoes": [{**base, "atualizado_em": h.agora_iso()}]})
        marcador = h.pull(self.client, headers)["servidor_timestamp"]

        r = self.client.post("/api/sync/push", headers=headers, json={"bonificacoes": [
            {**base, "deletado": True, "atualizado_em": h.agora_iso(timedelta(minutes=1))}]})
        self.assertEqual(len(r.json()["data"]["bonificacoes_deletadas"]), 1, r.text)

        exclusoes = h.pull(self.client, headers, since=marcador)["exclusoes"]
        self.assertIn(("bonificacao", "p7-bon"), [(e["entidade"], e["uuid_cliente"]) for e in exclusoes])

        r = self.client.post("/api/sync/push", headers=headers, json={"bonificacoes": [
            {**base, "atualizado_em": h.agora_iso(timedelta(minutes=2))}]})
        erros = r.json()["data"]["erros"]
        self.assertEqual([(e["uuid_cliente"], e["status_code"]) for e in erros], [("p7-bon", 410)])

    def test_edicao_com_atualizado_em_igual_ao_do_servidor_vira_conflito__P10(self):
        headers, local, plantao = self._usuario_com_plantao()
        r = self.client.post("/api/sync/push", headers=headers, json={"plantoes": [{
            "uuid_cliente": plantao["uuid_cliente"] or "p10", "id": plantao["id"], "local_id": local["id"],
            "data_inicio": "2026-07-17T07:00:00", "data_fim": "2026-07-17T19:00:00", "valor": "1.00",
            "atualizado_em": plantao["atualizado_em"]}]})
        dados = r.json()["data"]
        self.assertEqual([c["id"] for c in dados["conflitos"]], [plantao["id"]], r.text)
        self.assertEqual(h.listar_plantoes(self.client, headers)[0]["valor"], "1200.00")


class TestContratoDefeitos(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = h.cliente()

    def test_pull_de_trocas_traz_observacao_criado_em_e_contrapartes__AUD309(self):
        """Falhava antes da correção (2026-10-15); hoje é regressão: TrocaResponse não tem esses campos."""
        id_a, headers_a, *_ = h.criar_usuario(self.client)
        id_b, headers_b, *_ = h.criar_usuario(self.client)
        h.tornar_amigos(self.client, id_a, headers_a, id_b, headers_b)
        local = h.criar_local(self.client, headers_a)
        plantao = h.criar_plantao(self.client, headers_a, local["id"])
        h.transferir_por_solicitacao(self.client, headers_a, id_b, headers_b, plantao["id"])

        trocas = h.pull(self.client, headers_a)["trocas"]
        self.assertEqual(len(trocas), 1)
        faltando = [c for c in ("observacao_transferir", "observacao_receber", "criado_em",
                                "contraparte_usuario_id", "contraparte_usuario_id_receber")
                    if c not in trocas[0]]
        self.assertEqual(faltando, [])

    def test_worker_de_notificacao_nao_transforma_edicao_em_conflito__AUD204(self):
        """Falhava antes da correção (2026-10-15); hoje é regressão: o worker grava `notificado=True`, o onupdate muda
        atualizado_em e a edição feita ANTES (T1) perde no last-write-wins."""
        from app.core.database import SessionLocal
        from app.services.notificacao_service import NotificacaoService, agora_em_hora_de_parede

        _, headers, *_ = h.criar_usuario(self.client)
        local = h.criar_local(self.client, headers)
        inicio = agora_em_hora_de_parede().replace(second=0, microsecond=0) + timedelta(minutes=30)
        fim = inicio + timedelta(hours=4)
        corpo = {"local_id": local["id"], "data_inicio": inicio.isoformat(), "data_fim": fim.isoformat(),
                 "valor": "100.00", "notificacao_antecedencia_minutos": 60}
        criado = self.client.post("/api/plantoes", headers=headers, json=corpo)
        self.assertEqual(criado.status_code, 201, criado.text)
        plantao = criado.json()["data"]

        t1 = h.agora_iso()  # o médico edita offline aqui
        with SessionLocal() as db:
            NotificacaoService.processar_notificacoes(db)
        self.assertTrue(h.listar_plantoes(self.client, headers)[0]["notificado"], "premissa: o worker rodou")

        r = self.client.post("/api/sync/push", headers=headers, json={"plantoes": [{
            "uuid_cliente": plantao["uuid_cliente"] or "aud204", "id": plantao["id"], "local_id": local["id"],
            "data_inicio": inicio.isoformat(), "data_fim": fim.isoformat(), "valor": "999.00",
            "atualizado_em": t1}]})
        dados = r.json()["data"]
        self.assertEqual(len(dados["plantoes_atualizados"]), 1, f"conflitos={dados['conflitos']}")

    def test_recalculo_do_local_usa_a_mesma_regra_da_criacao__AUD206(self):
        """Falhava antes da correção (2026-10-15); hoje é regressão (agente 2 S-07)."""
        _, headers, *_ = h.criar_usuario(self.client)
        local = h.criar_local(self.client, headers, data_inicio="2026-01-01", data_final="2026-12-31",
                              dia_pagamento=5, util_corrido=True)
        antigo = h.criar_plantao(self.client, headers, local["id"], data="2026-03-15")
        r = self.client.patch(f"/api/locais/{local['id']}", headers=headers, json={"dia_pagamento": 10})
        self.assertEqual(r.status_code, 200, r.text)
        novo = h.criar_plantao(self.client, headers, local["id"], data="2026-03-15", inicio="20:00", fim="23:00")

        recalculado = next(p for p in h.listar_plantoes(self.client, headers) if p["id"] == antigo["id"])
        self.assertEqual(recalculado["pagamento_plantao"], novo["pagamento_plantao"])

    def test_pull_com_cursor_nao_perde_commit_tardio__AUD205_demonstrativo(self):
        """Falhava antes da correção (2026-10-15); hoje é regressão (demonstrativo): uma linha cujo atualizado_em ficou
        1 s antes do servidor_timestamp — o que acontece quando o commit de outra
        requisição termina depois do pull — nunca mais aparece no incremental."""
        from sqlalchemy import update
        from app.core.database import SessionLocal
        from app.models import Plantao

        _, headers, *_ = h.criar_usuario(self.client)
        local = h.criar_local(self.client, headers)
        s = h.pull(self.client, headers)["servidor_timestamp"]
        plantao = h.criar_plantao(self.client, headers, local["id"])
        s_dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        with SessionLocal() as db:
            db.execute(update(Plantao).where(Plantao.id == plantao["id"])
                       .values(atualizado_em=s_dt - timedelta(seconds=1)))
            db.commit()

        ids = [p["id"] for p in h.pull(self.client, headers, since=s)["plantoes"]]
        self.assertIn(plantao["id"], ids)


if __name__ == "__main__":
    unittest.main()
