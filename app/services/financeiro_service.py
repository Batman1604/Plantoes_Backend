from decimal import Decimal

from sqlalchemy import extract, func, select
from sqlalchemy.orm import Session

from app.models import Plantao


class FinanceiroService:
    @staticmethod
    def calcular_total_mensal(db: Session, usuario_id: int, mes: int, ano: int) -> dict:
        """Calcula o total e a quantidade de plantões de um mês específico."""
        stmt = select(
            func.coalesce(func.sum(Plantao.valor), 0).label("total"),
            func.coalesce(func.count(Plantao.id), 0).label("quantidade_plantoes"),
        ).where(
            Plantao.usuario_id == usuario_id,
            Plantao.transferido.is_(False),
            Plantao.recebido_via_transferencia.is_(False),
            extract("month", Plantao.data_inicio) == mes,
            extract("year", Plantao.data_inicio) == ano,
        )
        resultado = db.execute(stmt).one()
        return {
            "total": resultado.total if resultado.total is not None else Decimal("0"),
            "quantidade_plantoes": int(resultado.quantidade_plantoes),
        }

    @staticmethod
    def calcular_total_anual(db: Session, usuario_id: int, ano: int) -> dict:
        """Calcula o total e a quantidade de plantões no ano especificado."""
        stmt = select(
            func.coalesce(func.sum(Plantao.valor), 0).label("total"),
            func.coalesce(func.count(Plantao.id), 0).label("quantidade_plantoes"),
        ).where(
            Plantao.usuario_id == usuario_id,
            Plantao.transferido.is_(False),
            Plantao.recebido_via_transferencia.is_(False),
            extract("year", Plantao.data_inicio) == ano,
        )
        resultado = db.execute(stmt).one()
        return {
            "total": resultado.total if resultado.total is not None else Decimal("0"),
            "quantidade_plantoes": int(resultado.quantidade_plantoes),
        }

    @staticmethod
    def calcular_grafico_anual(db: Session, usuario_id: int, ano: int) -> list[dict]:
        """Retorna o total e quantidade de plantões por mês (jan-dez) do ano especificado."""
        stmt = select(
            extract("month", Plantao.data_inicio).label("mes"),
            func.coalesce(func.sum(Plantao.valor), 0).label("total"),
            func.coalesce(func.count(Plantao.id), 0).label("quantidade_plantoes"),
        ).where(
            Plantao.usuario_id == usuario_id,
            Plantao.transferido.is_(False),
            Plantao.recebido_via_transferencia.is_(False),
            extract("year", Plantao.data_inicio) == ano,
        ).group_by(extract("month", Plantao.data_inicio))

        rows = {int(r.mes): r for r in db.execute(stmt).all()}

        return [
            {
                "mes": mes,
                "total": Decimal(str(rows[mes].total)) if mes in rows else Decimal("0"),
                "quantidade_plantoes": int(rows[mes].quantidade_plantoes) if mes in rows else 0,
            }
            for mes in range(1, 13)
        ]

    @staticmethod
    def calcular_total_mensal_por_local(db: Session, usuario_id: int, local_id: int, mes: int, ano: int) -> dict:
        """Calcula o total e a quantidade de plantões para um local específico em um mês."""
        stmt = select(
            func.coalesce(func.sum(Plantao.valor), 0).label("total"),
            func.coalesce(func.count(Plantao.id), 0).label("quantidade_plantoes"),
        ).where(
            Plantao.usuario_id == usuario_id,
            Plantao.local_id == local_id,
            Plantao.transferido.is_(False),
            Plantao.recebido_via_transferencia.is_(False),
            extract("month", Plantao.data_inicio) == mes,
            extract("year", Plantao.data_inicio) == ano,
        )
        resultado = db.execute(stmt).one()
        return {
            "total": resultado.total if resultado.total is not None else Decimal("0"),
            "quantidade_plantoes": int(resultado.quantidade_plantoes),
        }
