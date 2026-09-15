import unittest
from datetime import date, datetime, time

from pydantic import ValidationError

from app.schemas.plantao_schema import PlantaoCreate
from app.services.pagamento_service import PagamentoService


class TestPagamentoEHorario(unittest.TestCase):
    def test_calcula_indice_de_dia_util_no_mes(self):
        # Em setembro/2026, o 5º dia útil é 07/09/2026.
        data_dia_util = PagamentoService.calcular_data_dia_util_do_mes(2026, 9, 5)
        self.assertEqual(data_dia_util, date(2026, 9, 7))

    def test_calcula_data_pagamento_para_mes_seguinte_quando_dia_ja_passou(self):
        # Ciclo 21/abr → 20/mai; plantão em 20/abr está no ciclo anterior (21/mar→20/abr).
        # 5º dia útil de abril = 07/04, que é ≤ ao fechamento 20/04, logo avança para maio.
        # 5º dia útil de maio/2026 = 07/05.
        data_pagamento = PagamentoService.calcular_data_pagamento_plantao(
            data_plantao=date(2026, 4, 20),
            indice_dia_pagamento=5,
            data_inicio_ciclo=date(2026, 4, 21),
            data_final_ciclo=date(2026, 5, 20),
        )
        self.assertEqual(data_pagamento, date(2026, 5, 7))

    def test_calcula_data_pagamento_no_mes_de_fechamento_quando_dia_util_apos_fechamento(self):
        data_pagamento = PagamentoService.calcular_data_pagamento_plantao(
            data_plantao=date(2026, 4, 22),
            indice_dia_pagamento=22,
            data_inicio_ciclo=date(2026, 4, 21),
            data_final_ciclo=date(2026, 5, 20),
        )
        self.assertEqual(data_pagamento, date(2026, 5, 29))

    def test_permite_plantao_virando_a_madrugada(self):
        # O modelo trocou o campo único `data` por data_inicio/data_fim
        # explícitos; este teste continuava usando o campo antigo e falhava por
        # isso, deixando o caso da madrugada sem cobertura.
        payload = PlantaoCreate(
            valor="200.00",
            data_inicio=datetime(2026, 4, 10, 22, 0),
            data_fim=datetime(2026, 4, 11, 6, 0),
            local_id=1,
        )
        self.assertEqual(payload.data_inicio.date(), date(2026, 4, 10))
        self.assertEqual(payload.data_inicio.time(), time(22, 0))
        self.assertEqual(payload.data_fim.date(), date(2026, 4, 11))
        self.assertEqual(payload.data_fim.time(), time(6, 0))

    def test_rejeita_data_com_fuso(self):
        """data_inicio/data_fim são hora de parede e não aceitam fuso.

        Este teste guardava o campo `data`, que deixou de existir. Ele passou a
        falhar por falta dos campos novos — e, pior, o companheiro dele passou a
        PASSAR pelo motivo errado: esperava ValidationError e recebia um, mas
        por campo ausente, não pela rejeição de fuso. A proteção tinha sumido
        sem ninguém notar (ver auditoria FUSO-01).
        """
        for valor_com_fuso in ("2026-05-05T08:00:00Z", "2026-05-05T08:00:00-03:00"):
            with self.subTest(valor=valor_com_fuso):
                with self.assertRaises(ValidationError):
                    PlantaoCreate(
                        valor="200.00",
                        data_inicio=valor_com_fuso,
                        data_fim="2026-05-05T18:00:00",
                        local_id=1,
                    )

    def test_aceita_hora_de_parede_sem_fuso(self):
        payload = PlantaoCreate(
            valor="200.00",
            data_inicio="2026-05-05T07:00:00",
            data_fim="2026-05-05T19:00:00",
            local_id=1,
        )

        # O horário chega e é guardado exatamente como enviado — é isto que o
        # cliente depende para o plantão não mudar de hora ao sincronizar.
        self.assertIsNone(payload.data_inicio.tzinfo)
        self.assertEqual(payload.data_inicio.time(), time(7, 0))
        self.assertEqual(payload.data_fim.time(), time(19, 0))


if __name__ == "__main__":
    unittest.main()
