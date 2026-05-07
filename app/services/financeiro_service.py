from decimal import Decimal

from sqlalchemy import extract, func, select
from sqlalchemy.orm import Session

from app.models import Plantao


class FinanceiroService:
    @staticmethod
    def calcular_total_mensal(db: Session, usuario_id: int, mes: int, ano: int) -> dict:
        stmt = select(
            func.coalesce(func.sum(Plantao.valor), 0).label("total"),
            func.coalesce(func.count(Plantao.id), 0).label("quantidade_plantoes"),
        ).where(
            Plantao.usuario_id == usuario_id,
            extract("month", Plantao.data) == mes,
            extract("year", Plantao.data) == ano,
        )
        resultado = db.execute(stmt).one()
        return {
            "total": resultado.total if resultado.total is not None else Decimal("0"),
            "quantidade_plantoes": int(resultado.quantidade_plantoes),
        }

    @staticmethod
    def calcular_total_anual(db: Session, usuario_id: int, ano: int) -> dict:
        stmt = select(
            func.coalesce(func.sum(Plantao.valor), 0).label("total"),
            func.coalesce(func.count(Plantao.id), 0).label("quantidade_plantoes"),
        ).where(
            Plantao.usuario_id == usuario_id,
            extract("year", Plantao.data) == ano,
        )
        resultado = db.execute(stmt).one()
        return {
            "total": resultado.total if resultado.total is not None else Decimal("0"),
            "quantidade_plantoes": int(resultado.quantidade_plantoes),
        }

    @staticmethod
    def calcular_total_mensal_por_local(db: Session, usuario_id: int, local_id: int, mes: int, ano: int) -> dict:
        stmt = select(
            func.coalesce(func.sum(Plantao.valor), 0).label("total"),
            func.coalesce(func.count(Plantao.id), 0).label("quantidade_plantoes"),
        ).where(
            Plantao.usuario_id == usuario_id,
            Plantao.local_id == local_id,
            extract("month", Plantao.data) == mes,
            extract("year", Plantao.data) == ano,
        )
        resultado = db.execute(stmt).one()
        return {
            "total": resultado.total if resultado.total is not None else Decimal("0"),
            "quantidade_plantoes": int(resultado.quantidade_plantoes),
        }
