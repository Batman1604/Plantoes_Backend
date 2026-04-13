import unittest
from datetime import date, time

from app.schemas.plantao_schema import PlantaoCreate
from app.services.pagamento_service import PagamentoService


class TestPagamentoEHorario(unittest.TestCase):
    def test_calcula_dia_util_quando_cai_no_fim_de_semana(self):
        # 13/09/2026 é domingo, então deve avançar para segunda-feira 14.
        dia_util = PagamentoService.calcular_dia_util(2026, 9, 13)
        self.assertEqual(dia_util, 14)

    def test_calcula_data_pagamento_para_mes_seguinte_quando_dia_ja_passou(self):
        data_pagamento = PagamentoService.calcular_data_pagamento_plantao(
            data_plantao=date(2026, 4, 20),
            dia_pagamento=13,
        )
        self.assertEqual(data_pagamento, date(2026, 5, 13))

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


if __name__ == "__main__":
    unittest.main()
