import unittest
from datetime import date, time

from pydantic import ValidationError

from app.schemas.plantao_schema import PlantaoCreate
from app.services.pagamento_service import PagamentoService


class TestPagamentoEHorario(unittest.TestCase):
    def test_calcula_indice_de_dia_util_no_mes(self):
        # Em setembro/2026, o 5º dia útil é 07/09/2026.
        data_dia_util = PagamentoService.calcular_data_dia_util_do_mes(2026, 9, 5)
        self.assertEqual(data_dia_util, date(2026, 9, 7))

    def test_calcula_data_pagamento_para_mes_seguinte_quando_dia_ja_passou(self):
        data_pagamento = PagamentoService.calcular_data_pagamento_plantao(
            data_plantao=date(2026, 4, 20),
            indice_dia_pagamento=5,
            data_inicio_ciclo=date(2026, 4, 21),
            data_final_ciclo=date(2026, 5, 20),
        )
        self.assertEqual(data_pagamento, date(2026, 6, 5))

    def test_calcula_data_pagamento_no_mes_de_fechamento_quando_dia_util_apos_fechamento(self):
        data_pagamento = PagamentoService.calcular_data_pagamento_plantao(
            data_plantao=date(2026, 4, 22),
            indice_dia_pagamento=22,
            data_inicio_ciclo=date(2026, 4, 21),
            data_final_ciclo=date(2026, 5, 20),
        )
        self.assertEqual(data_pagamento, date(2026, 5, 29))

    def test_permite_plantao_virando_a_madrugada(self):
        payload = PlantaoCreate(
            valor="200.00",
            data=date(2026, 4, 10),
            horario_inicio=time(12, 0),
            horario_fim=time(0, 0),
            local_id=1,
        )
        self.assertEqual(payload.horario_inicio, time(12, 0))
        self.assertEqual(payload.horario_fim, time(0, 0))

    def test_rejeita_data_com_horario_ou_fuso(self):
        with self.assertRaises(ValidationError):
            PlantaoCreate(
                valor="200.00",
                data="2026-05-05T00:00:00.000Z",
                horario_inicio=time(12, 0),
                horario_fim=time(18, 0),
                local_id=1,
            )


if __name__ == "__main__":
    unittest.main()
