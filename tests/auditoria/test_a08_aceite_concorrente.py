"""A-08 (aceite concorrente da mesma solicitação de troca) — agente 2 S-17.

Duas requisições de aceite chegam juntas. As duas leem a solicitação ainda
"pendente" antes de qualquer uma gravar; sem trava de linha
(SELECT ... FOR UPDATE) nem checagem atômica do status, as duas criam clone e
Troca. A barreira é posta em `PlanoService.verificar_limite_serie`, chamada
DEPOIS das leituras e ANTES das escritas em aceitar_solicitacao_troca.

Viabilidade em SQLite: a suíte usa SQLite em ARQUIVO; o pysqlite só abre
transação no primeiro DML, então as leituras não seguram trava e o segundo
escritor espera o primeiro (busy timeout). Se der "database is locked", é
limitação de ambiente — a prova definitiva exige Postgres.

Só observa o fluxo de troca (validado em aparelho); não o altera.
"""

import threading
import unittest
from unittest.mock import patch

from sqlalchemy import func, select

import aud_helpers as h
from app.core.database import SessionLocal
from app.models import Plantao, Troca


class TestA08AceiteConcorrente(unittest.TestCase):
    def test_dois_aceites_simultaneos_criam_um_so_clone__A08(self):
        """Falhava antes da correção (2026-10-15); hoje é regressão (2 clones e 2 trocas)."""
        client = h.cliente()
        id_a, headers_a, *_ = h.criar_usuario(client)
        id_b, headers_b, *_ = h.criar_usuario(client)
        h.tornar_amigos(client, id_a, headers_a, id_b, headers_b)
        local = h.criar_local(client, headers_a)
        plantao = h.criar_plantao(client, headers_a, local["id"])
        sid = client.post("/api/solicitacoes-troca", headers=headers_a, json={
            "destinatario_usuario_id": id_b, "tipo": "transferir", "plantao_transferido_id": plantao["id"],
        }).json()["data"]["id"]

        barreira = threading.Barrier(2, timeout=10)
        status = []

        def _esperar(*_a, **_k):
            barreira.wait()

        def _aceitar():
            c = h.cliente(raise_server_exceptions=False)
            r = c.patch(f"/api/solicitacoes-troca/{sid}/aceitar", headers=headers_b)
            status.append(r.status_code)

        with patch("app.routes.plantao_routes.PlanoService.verificar_limite_serie", side_effect=_esperar):
            threads = [threading.Thread(target=_aceitar) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=60)

        self.assertEqual(len(status), 2, f"threads não terminaram: {status}")
        with SessionLocal() as db:
            clones = db.scalar(select(func.count()).select_from(Plantao).where(
                Plantao.usuario_id == id_b, Plantao.recebido_via_transferencia.is_(True)))
            trocas = db.scalar(select(func.count()).select_from(Troca).where(
                Troca.plantao_id_transferido == plantao["id"]))
        self.assertEqual((clones, trocas), (1, 1), f"status HTTP: {sorted(status)}")

    def test_controle_aceite_sequencial_repetido_da_404(self):
        """Controle (PASSA): o segundo aceite em sequência é recusado."""
        client = h.cliente()
        id_a, headers_a, *_ = h.criar_usuario(client)
        id_b, headers_b, *_ = h.criar_usuario(client)
        h.tornar_amigos(client, id_a, headers_a, id_b, headers_b)
        local = h.criar_local(client, headers_a)
        plantao = h.criar_plantao(client, headers_a, local["id"])
        sid = client.post("/api/solicitacoes-troca", headers=headers_a, json={
            "destinatario_usuario_id": id_b, "tipo": "transferir", "plantao_transferido_id": plantao["id"],
        }).json()["data"]["id"]
        self.assertEqual(client.patch(f"/api/solicitacoes-troca/{sid}/aceitar", headers=headers_b).status_code, 200)
        self.assertEqual(client.patch(f"/api/solicitacoes-troca/{sid}/aceitar", headers=headers_b).status_code, 404)


if __name__ == "__main__":
    unittest.main()
