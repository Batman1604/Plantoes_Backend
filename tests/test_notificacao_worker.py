"""AUD-027 / A-11: o worker de notificação filtra a janela em SQL e não loga
e-mail nem valor. O efeito em atualizado_em (AUD-022) está em
tests/auditoria/test_sync_contrato.py (AUD204)."""

import unittest
from datetime import date, datetime, time
from uuid import uuid4

from sqlalchemy import event

from app.core.database import SessionLocal
from app.models import Local, Plantao, User
from app.services.notificacao_service import NotificacaoService


class TestWorkerDeNotificacao(unittest.TestCase):
    def setUp(self):
        self.agora = datetime(2026, 10, 19, 6, 30)
        with SessionLocal() as db:
            self.email = f"worker-{uuid4().hex[:8]}@example.com"
            usuario = User(nome="Worker", email=self.email, senha_hash="x")
            db.add(usuario)
            db.flush()
            local = Local(usuario_id=usuario.id, nome_local="H", data_inicio=date(2025, 1, 1), data_final=date(2026, 12, 31))
            db.add(local)
            db.flush()

            def _plantao(dia):
                p = Plantao(usuario_id=usuario.id, local_id=local.id, valor=4321, data_inicio=dia,
                            data_fim=dia, horario_inicio=time(7), horario_fim=time(19),
                            notificacao_ativa=True, notificacao_antecedencia_minutos=60, notificado=False)
                db.add(p)
                return p

            antigo = _plantao(date(2025, 3, 1))     # janela já passou: nunca será notificado
            na_janela = _plantao(date(2026, 10, 19))  # janela 06:00–07:00
            db.commit()
            self.antigo_id, self.na_janela_id = antigo.id, na_janela.id

    def test_plantao_com_janela_passada_nem_e_carregado(self):
        carregados = set()

        def _carregado(alvo, _contexto):
            carregados.add(alvo.id)

        event.listen(Plantao, "load", _carregado)
        self.addCleanup(event.remove, Plantao, "load", _carregado)
        with SessionLocal() as db:
            encontrados = NotificacaoService.buscar_plantoes_para_notificar(db, agora=self.agora)
        self.assertIn(self.na_janela_id, [p.id for p in encontrados])
        self.assertNotIn(self.antigo_id, carregados, "a consulta ainda varre plantões fora da janela")

    def test_log_nao_leva_email_nem_valor(self):
        with SessionLocal() as db, self.assertLogs("app.services.notificacao_service", level="INFO") as logs:
            for plantao in NotificacaoService.buscar_plantoes_para_notificar(db, agora=self.agora):
                NotificacaoService.registrar_notificacao(plantao)
        saida = "\n".join(logs.output)
        self.assertNotIn(self.email, saida)
        self.assertNotIn("4321", saida)


if __name__ == "__main__":
    unittest.main()
