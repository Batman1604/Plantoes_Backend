"""Fuso horário da janela de notificação (auditoria A-16).

data_inicio + horario_inicio são HORA DE PAREDE: "das 07:00 às 19:00 do dia
10/04" é o que o profissional combinou e vale igual em qualquer fuso. A
decisão de "já é hora de avisar?" comparava isso com datetime.now(), que
devolve a hora de parede do SERVIDOR.

Hospedagem roda em UTC por padrão. Atendendo o Brasil (UTC-3), um plantão das
07:00 era comparado com um "agora" três horas à frente, e a notificação saía
às 04:00 — ainda de madrugada, para um plantão que só começa às 07:00.
"""

import unittest
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.core.database import SessionLocal
from app.models import Local, Plantao, User
from app.services.notificacao_service import (
    FUSO_DO_PLANTAO,
    NotificacaoService,
    agora_em_hora_de_parede,
)


class TestNotificacaoFuso(unittest.TestCase):
    def setUp(self):
        self.db = SessionLocal()
        sufixo = datetime.now().strftime("%H%M%S%f")
        self.usuario = User(
            nome="Plantonista",
            email=f"fuso-{sufixo}@example.com",
            senha_hash="x",
        )
        self.db.add(self.usuario)
        self.db.flush()
        self.local = Local(
            usuario_id=self.usuario.id,
            nome_local="Hospital do Fuso",
            data_inicio=date(2026, 1, 1),
            data_final=date(2026, 12, 31),
        )
        self.db.add(self.local)
        self.db.flush()
        # Plantão das 07:00, com uma hora de antecedência: a janela de aviso é
        # das 06:00 às 07:00, em hora de parede.
        self.plantao = Plantao(
            usuario_id=self.usuario.id,
            local_id=self.local.id,
            valor=1000,
            data_inicio=date(2026, 10, 19),
            horario_inicio=time(7, 0),
            data_fim=date(2026, 10, 19),
            horario_fim=time(19, 0),
            notificacao_ativa=True,
            notificacao_antecedencia_minutos=60,
            notificado=False,
        )
        self.db.add(self.plantao)
        self.db.commit()

    def tearDown(self):
        self.db.query(Plantao).filter(Plantao.usuario_id == self.usuario.id).delete()
        self.db.query(Local).filter(Local.usuario_id == self.usuario.id).delete()
        self.db.query(User).filter(User.id == self.usuario.id).delete()
        self.db.commit()
        self.db.close()

    def encontrados(self, agora):
        ids = {p.id for p in NotificacaoService.buscar_plantoes_para_notificar(self.db, agora=agora)}
        return self.plantao.id in ids

    def test_avisa_dentro_da_janela_em_hora_de_parede(self):
        self.assertTrue(self.encontrados(datetime(2026, 10, 19, 6, 30)))

    def test_nao_avisa_antes_da_janela(self):
        self.assertFalse(self.encontrados(datetime(2026, 10, 19, 5, 30)))

    def test_nao_avisa_depois_de_comecar(self):
        self.assertFalse(self.encontrados(datetime(2026, 10, 19, 7, 1)))

    def test_hora_do_servidor_em_utc_nao_antecipa_o_aviso(self):
        # Este é o defeito, escrito como teste. Quando no Brasil são 06:30
        # (dentro da janela), em UTC são 09:30 — e o código antigo, usando a
        # hora do servidor, concluía que a janela já havia passado. No sentido
        # inverso, às 03:30 UTC (00:30 no Brasil) ele avisaria de madrugada.
        #
        # A régua é hora de parede: passar um instante em UTC não pode cair na
        # janela de um plantão que começa às 07:00 locais.
        utc_equivalente_a_meia_noite_e_meia = datetime(2026, 10, 19, 3, 30)
        self.assertFalse(
            self.encontrados(utc_equivalente_a_meia_noite_e_meia),
            "03:30 é 00:30 no Brasil — seis horas antes da janela de aviso",
        )

    def test_agora_em_hora_de_parede_segue_o_fuso_do_plantao(self):
        parede = agora_em_hora_de_parede()
        esperado = datetime.now(FUSO_DO_PLANTAO).replace(tzinfo=None)
        self.assertIsNone(parede.tzinfo, "precisa ser comparável com datetime.combine")
        self.assertLess(abs(parede - esperado), timedelta(seconds=5))

    def test_fuso_do_plantao_difere_de_utc(self):
        # Guarda contra a correção ser desfeita por engano: se FUSO_DO_PLANTAO
        # voltasse a ser UTC (ou o fuso do servidor), este teste cai.
        em_utc = datetime.now(ZoneInfo("UTC")).replace(tzinfo=None)
        self.assertNotEqual(
            round((agora_em_hora_de_parede() - em_utc).total_seconds() / 3600),
            0,
            "o Brasil não está em UTC; se isto passar, o fuso voltou a ser o do servidor",
        )
