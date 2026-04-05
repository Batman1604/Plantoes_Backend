import unittest
from datetime import datetime
from decimal import Decimal

from sqlalchemy import create_engine, delete
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Plantao
from app.services.financeiro_service import FinanceiroService


class TestFinanceiroService(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(bind=cls.engine)
        cls.SessionLocal = sessionmaker(bind=cls.engine, autoflush=False, autocommit=False)

    def setUp(self):
        self.db = self.SessionLocal()

    def tearDown(self):
        self.db.execute(delete(Plantao))
        self.db.commit()
        self.db.close()

    def criar_plantao(self, **kwargs) -> Plantao:
        plantao = Plantao(**kwargs)
        self.db.add(plantao)
        self.db.commit()
        return plantao

    def test_calcular_total_mensal_retorna_soma_para_usuario(self):
        self.criar_plantao(
            usuario_id=1,
            local_id=1,
            valor=Decimal("100.50"),
            horario_inicio=datetime(2000, 1, 10, 10, 0),
            horario_fim=datetime(2000, 1, 10, 12, 0),
        )
        self.criar_plantao(
            usuario_id=1,
            local_id=1,
            valor=Decimal("200.00"),
            horario_inicio=datetime(2000, 1, 11, 11, 0),
            horario_fim=datetime(2000, 1, 11, 13, 0),
        )
        self.criar_plantao(
            usuario_id=2,
            local_id=1,
            valor=Decimal("50.00"),
            horario_inicio=datetime(2000, 1, 11, 11, 0),
            horario_fim=datetime(2000, 1, 11, 13, 0),
        )

        resultado = FinanceiroService.calcular_total_mensal(self.db, usuario_id=1, mes=1, ano=2000)

        self.assertEqual(resultado["total"], Decimal("300.50"))
        self.assertEqual(resultado["quantidade_plantoes"], 2)

    def test_calcular_total_anual_retorna_soma_para_usuario(self):
        self.criar_plantao(
            usuario_id=1,
            local_id=1,
            valor=Decimal("120.00"),
            horario_inicio=datetime(2000, 1, 10, 10, 0),
            horario_fim=datetime(2000, 1, 10, 12, 0),
        )
        self.criar_plantao(
            usuario_id=1,
            local_id=1,
            valor=Decimal("180.00"),
            horario_inicio=datetime(2000, 1, 11, 11, 0),
            horario_fim=datetime(2000, 1, 11, 13, 0),
        )
        self.criar_plantao(
            usuario_id=2,
            local_id=1,
            valor=Decimal("75.00"),
            horario_inicio=datetime(2000, 1, 11, 11, 0),
            horario_fim=datetime(2000, 1, 11, 13, 0),
        )

        resultado = FinanceiroService.calcular_total_anual(self.db, usuario_id=1, ano=2000)

        self.assertEqual(resultado["total"], Decimal("300.00"))
        self.assertEqual(resultado["quantidade_plantoes"], 2)

    def test_calcular_total_mensal_retorna_zero_sem_registros(self):
        resultado = FinanceiroService.calcular_total_mensal(self.db, usuario_id=1, mes=1, ano=2000)
        self.assertEqual(resultado["total"], Decimal("0"))
        self.assertEqual(resultado["quantidade_plantoes"], 0)

    def test_calcular_total_anual_retorna_zero_sem_registros(self):
        resultado = FinanceiroService.calcular_total_anual(self.db, usuario_id=1, ano=2000)
        self.assertEqual(resultado["total"], Decimal("0"))
        self.assertEqual(resultado["quantidade_plantoes"], 0)


if __name__ == "__main__":
    unittest.main()
