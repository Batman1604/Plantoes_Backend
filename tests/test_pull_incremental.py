"""Custo do pull incremental (auditoria A-04).

SyncService.pull carregava TODAS as linhas do usuário e descartava em Python o
que não tinha mudado. O custo era proporcional ao histórico inteiro mesmo quando
nada havia mudado — e o cliente chama o pull a cada poucos minutos. Medido antes
da correção: 22 ms para 300 plantões e 52 ms para 1.500, devolvendo zero itens
nos dois casos. Depois: 11 ms e 12 ms, ou seja, plano.

Os testes não cronometram (medida de tempo oscila e daria falso vermelho). Eles
contam quantas instâncias de Plantao o ORM cria a partir do banco: se o filtro
voltar a ser feito em Python, os plantões inalterados voltam a ser carregados e
o teste falha.
"""

import unittest
from datetime import UTC, date, datetime, time, timedelta
from uuid import uuid4

from sqlalchemy import event, select

from app.core.database import SessionLocal
from app.models import Local, Plantao, User
from app.services.sync_service import SyncService


class ContadorDePlantoesCarregados:
    """Conta as instâncias de Plantao que o ORM cria a partir do banco.

    Contar db.identity_map não serve: ele guarda referências FRACAS, e os
    objetos somem assim que o pull os converte em resposta — a contagem daria
    zero mesmo com o histórico inteiro carregado. O evento "load" dispara no
    momento em que cada instância nasce de uma linha.
    """

    def __init__(self):
        self.total = 0

    def __enter__(self):
        self.total = 0
        event.listen(Plantao, "load", self._contar)
        return self

    def __exit__(self, *_):
        event.remove(Plantao, "load", self._contar)

    def _contar(self, alvo, contexto):
        self.total += 1


class TestPullIncremental(unittest.TestCase):
    def criar_cenario(self, db, quantidade):
        sufixo = uuid4().hex[:8]
        usuario = User(nome="Pull User", email=f"pull-{sufixo}@example.com")
        db.add(usuario)
        db.flush()

        local = Local(
            usuario_id=usuario.id,
            nome_local="Hospital Pull",
            data_inicio=date(2026, 1, 1),
            data_final=date(2026, 12, 31),
            dia_pagamento=5,
        )
        db.add(local)
        db.flush()

        antigo = datetime.now(UTC) - timedelta(days=30)
        db.add_all([
            Plantao(
                usuario_id=usuario.id,
                local_id=local.id,
                valor=300,
                data_inicio=date(2026, (i // 28) % 12 + 1, (i % 28) + 1),
                data_fim=date(2026, (i // 28) % 12 + 1, (i % 28) + 1),
                horario_inicio=time(8, 0),
                horario_fim=time(14, 0),
                uuid_cliente=f"{sufixo}-{i}",
                atualizado_em=antigo,
            )
            for i in range(quantidade)
        ])
        db.commit()
        return usuario.id

    def test_pull_sem_novidade_nao_carrega_o_historico(self):
        with SessionLocal() as db:
            usuario_id = self.criar_cenario(db, quantidade=40)
            desde = datetime.now(UTC)
            db.expunge_all()
            usuario = db.get(User, usuario_id)

            with ContadorDePlantoesCarregados() as contador:
                resultado = SyncService.pull(db, usuario, desde)

            self.assertEqual(resultado.plantoes, [], "nada mudou, nada deve voltar")
            self.assertEqual(
                contador.total,
                0,
                "o pull não pode carregar plantões que não mudaram — se carregou, "
                "o filtro voltou a ser feito em Python",
            )

    def test_pull_traz_apenas_o_que_mudou(self):
        with SessionLocal() as db:
            usuario_id = self.criar_cenario(db, quantidade=40)
            desde = datetime.now(UTC)

            alvo = db.scalars(
                select(Plantao).where(Plantao.usuario_id == usuario_id).limit(1)
            ).one()
            alvo.valor = 999
            db.commit()
            db.expunge_all()
            usuario = db.get(User, usuario_id)

            with ContadorDePlantoesCarregados() as contador:
                resultado = SyncService.pull(db, usuario, desde)

            self.assertEqual(len(resultado.plantoes), 1)
            self.assertEqual(
                contador.total,
                1,
                f"um plantão mudou entre 40; o pull carregou {contador.total}",
            )

    def test_pull_completo_ainda_traz_tudo(self):
        # Sem `desde` (primeira sincronização ou reinstalação), o contrato é
        # trazer o conjunto inteiro — o filtro em SQL não pode ter quebrado isso.
        with SessionLocal() as db:
            usuario_id = self.criar_cenario(db, quantidade=12)
            db.expunge_all()
            usuario = db.get(User, usuario_id)

            resultado = SyncService.pull(db, usuario, None)

            self.assertEqual(len(resultado.plantoes), 12)


if __name__ == "__main__":
    unittest.main()
