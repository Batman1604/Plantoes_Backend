import unittest
from datetime import date, time
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

    def criar_plantao(self, *, dia: int = 10, **kwargs) -> Plantao:
        """Cria um plantão de um dia em janeiro de 2000.

        O modelo tem data_inicio e data_fim explícitos; os testes usavam um
        campo `data` que não existe mais, e por isso estavam quebrados —
        deixando o cálculo financeiro sem nenhuma cobertura efetiva.
        """
        kwargs.setdefault("usuario_id", 1)
        kwargs.setdefault("local_id", 1)
        kwargs.setdefault("horario_inicio", time(10, 0))
        kwargs.setdefault("horario_fim", time(12, 0))
        plantao = Plantao(
            data_inicio=date(2000, 1, dia),
            data_fim=date(2000, 1, dia),
            **kwargs,
        )
        self.db.add(plantao)
        self.db.commit()
        return plantao

    def test_calcular_total_mensal_retorna_soma_para_usuario(self):
        self.criar_plantao(valor=Decimal("100.50"), dia=10)
        self.criar_plantao(valor=Decimal("200.00"), dia=11)
        self.criar_plantao(valor=Decimal("50.00"), dia=11, usuario_id=2)

        resultado = FinanceiroService.calcular_total_mensal(self.db, usuario_id=1, mes=1, ano=2000)

        self.assertEqual(resultado["total"], Decimal("300.50"))
        self.assertEqual(resultado["quantidade_plantoes"], 2)

    def test_calcular_total_anual_retorna_soma_para_usuario(self):
        self.criar_plantao(valor=Decimal("120.00"), dia=10)
        self.criar_plantao(valor=Decimal("180.00"), dia=11)
        self.criar_plantao(valor=Decimal("75.00"), dia=11, usuario_id=2)

        resultado = FinanceiroService.calcular_total_anual(self.db, usuario_id=1, ano=2000)

        self.assertEqual(resultado["total"], Decimal("300.00"))
        self.assertEqual(resultado["quantidade_plantoes"], 2)

    def test_soma_dos_meses_bate_com_o_total_anual(self):
        """Auditoria A-15: total mensal e total anual usavam filtros diferentes.

        calcular_total_mensal filtrava apenas `transferido`; anual, gráfico e
        mensal-por-local filtravam TAMBÉM `recebido_via_transferencia`. Bastava
        um plantão recebido por transferência para a soma dos doze meses deixar
        de bater com o total do ano — na tela que o usuário usa para conferir
        quanto ganhou.
        """
        self.criar_plantao(valor=Decimal("500.00"), dia=5)
        self.criar_plantao(
            valor=Decimal("300.00"),
            dia=6,
            recebido_via_transferencia=True,
        )

        mensal = FinanceiroService.calcular_total_mensal(self.db, usuario_id=1, mes=1, ano=2000)
        anual = FinanceiroService.calcular_total_anual(self.db, usuario_id=1, ano=2000)

        self.assertEqual(
            mensal["total"],
            anual["total"],
            "janeiro é o único mês com plantão: a soma dos meses tem de bater com o ano",
        )
        self.assertEqual(mensal["quantidade_plantoes"], anual["quantidade_plantoes"])

    def test_grafico_anual_usa_o_mesmo_criterio_do_total_anual(self):
        self.criar_plantao(valor=Decimal("500.00"), dia=5)
        self.criar_plantao(
            valor=Decimal("300.00"),
            dia=6,
            recebido_via_transferencia=True,
        )

        anual = FinanceiroService.calcular_total_anual(self.db, usuario_id=1, ano=2000)
        grafico = FinanceiroService.calcular_grafico_anual(self.db, usuario_id=1, ano=2000)

        self.assertEqual(sum(m["total"] for m in grafico), anual["total"])

    def test_recebidos_sao_um_recorte_do_total_nunca_uma_parcela_a_somar(self):
        """Plantão recebido conta na receita E aparece separado.

        A distinção importa para a interface: `total_recebidos` é a PARTE de
        `total` que veio de transferência. Somar os dois contaria o mesmo
        dinheiro duas vezes.
        """
        self.criar_plantao(valor=Decimal("500.00"), dia=5)
        self.criar_plantao(
            valor=Decimal("300.00"),
            dia=6,
            recebido_via_transferencia=True,
        )

        mensal = FinanceiroService.calcular_total_mensal(self.db, usuario_id=1, mes=1, ano=2000)

        self.assertEqual(mensal["total"], Decimal("800.00"), "o total inclui o recebido")
        self.assertEqual(mensal["total_recebidos"], Decimal("300.00"))
        self.assertEqual(mensal["quantidade_plantoes"], 2)
        self.assertEqual(mensal["quantidade_recebidos"], 1)

    def test_sem_recebidos_a_separacao_fica_zerada(self):
        self.criar_plantao(valor=Decimal("500.00"), dia=5)

        mensal = FinanceiroService.calcular_total_mensal(self.db, usuario_id=1, mes=1, ano=2000)

        self.assertEqual(mensal["total_recebidos"], Decimal("0"))
        self.assertEqual(mensal["quantidade_recebidos"], 0)

    def test_plantao_cedido_nao_entra_em_lugar_nenhum(self):
        # Quem cedeu não trabalha nem recebe — sai da receita e não é "recebido".
        self.criar_plantao(valor=Decimal("500.00"), dia=5)
        self.criar_plantao(valor=Decimal("400.00"), dia=6, transferido=True)

        mensal = FinanceiroService.calcular_total_mensal(self.db, usuario_id=1, mes=1, ano=2000)

        self.assertEqual(mensal["total"], Decimal("500.00"))
        self.assertEqual(mensal["total_recebidos"], Decimal("0"))

    def test_anual_traz_a_mesma_separacao_do_mensal(self):
        self.criar_plantao(valor=Decimal("500.00"), dia=5)
        self.criar_plantao(
            valor=Decimal("300.00"),
            dia=6,
            recebido_via_transferencia=True,
        )

        anual = FinanceiroService.calcular_total_anual(self.db, usuario_id=1, ano=2000)

        self.assertEqual(anual["total"], Decimal("800.00"))
        self.assertEqual(anual["total_recebidos"], Decimal("300.00"))

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
