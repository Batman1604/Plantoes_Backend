import unittest
from datetime import date

from app.services.pagamento_service import PagamentoService


class TestPagamentoServiceMes3031(unittest.TestCase):
    def test_mes_de_30_dias_trata_data_final_31_como_30(self):
        data_pagamento = PagamentoService.calcular_data_pagamento_plantao(
            data_plantao=date(2026, 4, 30),
            indice_dia_pagamento=1,
            data_inicio_ciclo=date(2026, 4, 1),
            data_final_ciclo=date(2026, 1, 31),
        )

        self.assertEqual(data_pagamento, date(2026, 5, 1))


if __name__ == "__main__":
    unittest.main()
