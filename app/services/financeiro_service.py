from decimal import Decimal

from sqlalchemy import extract, func, select
from sqlalchemy.orm import Session

from app.models import Bonificacao, Plantao


def _bon_subquery():
    """Subquery que soma bonificações por plantão."""
    return (
        select(
            Bonificacao.plantao_id,
            func.coalesce(func.sum(Bonificacao.valor), 0).label("total_bon"),
        )
        .group_by(Bonificacao.plantao_id)
        .subquery()
    )


def _to_decimal(value) -> Decimal:
    return Decimal(str(value)) if value is not None else Decimal("0")


def _plantoes_do_usuario(usuario_id: int):
    """Predicado único de "plantões que entram na receita deste usuário".

    Existe para impedir a divergência que a auditoria A-15 encontrou: o total
    MENSAL filtrava apenas `transferido`, enquanto anual, gráfico e
    mensal-por-local filtravam TAMBÉM `recebido_via_transferencia`. Bastava um
    plantão recebido de um colega para a soma dos doze meses deixar de bater com
    o total do ano — na tela em que o usuário confere quanto ganhou.

    A regra, agora única: plantão cedido a outra pessoa sai da receita
    (`transferido`); plantão recebido de outra pessoa ENTRA, porque quem recebeu
    vai trabalhá-lo e ser pago por ele. Quanto dessa receita veio de recebidos é
    informado à parte (ver `_soma_recebidos`), nunca somado duas vezes.
    """
    return (
        Plantao.usuario_id == usuario_id,
        Plantao.transferido.is_(False),
    )


def _soma_recebidos(db: Session, *filtros_extras, usuario_id: int) -> tuple[Decimal, int]:
    """Parcela da receita que veio de plantões recebidos de outra pessoa.

    É um RECORTE do total, não uma parcela a somar: o total já os inclui (ver
    `_plantoes_do_usuario`). Serve para o relatório exibir "deste valor, X veio
    de plantões recebidos" sem que a interface some duas vezes.
    """
    stmt = select(
        func.coalesce(func.sum(Plantao.valor), 0),
        func.coalesce(func.count(Plantao.id), 0),
    ).where(
        *_plantoes_do_usuario(usuario_id),
        Plantao.recebido_via_transferencia.is_(True),
        *filtros_extras,
    )
    valor, quantidade = db.execute(stmt).one()
    return _to_decimal(valor), int(quantidade)


class FinanceiroService:
    @staticmethod
    def calcular_total_mensal(db: Session, usuario_id: int, mes: int, ano: int) -> dict:
        bon = _bon_subquery()
        stmt = (
            select(
                func.coalesce(func.sum(Plantao.valor), 0).label("total"),
                func.coalesce(func.sum(bon.c.total_bon), 0).label("total_bonificacoes"),
                func.coalesce(func.count(Plantao.id), 0).label("quantidade_plantoes"),
            )
            .outerjoin(bon, bon.c.plantao_id == Plantao.id)
            .where(
                *_plantoes_do_usuario(usuario_id),
                extract("month", Plantao.data_inicio) == mes,
                extract("year", Plantao.data_inicio) == ano,
            )
        )
        r = db.execute(stmt).one()
        total = _to_decimal(r.total)
        total_bon = _to_decimal(r.total_bonificacoes)
        recebidos, qtd_recebidos = _soma_recebidos(
            db,
            extract("month", Plantao.data_inicio) == mes,
            extract("year", Plantao.data_inicio) == ano,
            usuario_id=usuario_id,
        )
        return {
            "total": total,
            "total_bonificacoes": total_bon,
            "total_com_bonificacoes": total + total_bon,
            "quantidade_plantoes": int(r.quantidade_plantoes),
            "total_recebidos": recebidos,
            "quantidade_recebidos": qtd_recebidos,
        }

    @staticmethod
    def calcular_total_anual(db: Session, usuario_id: int, ano: int) -> dict:
        bon = _bon_subquery()
        stmt = (
            select(
                func.coalesce(func.sum(Plantao.valor), 0).label("total"),
                func.coalesce(func.sum(bon.c.total_bon), 0).label("total_bonificacoes"),
                func.coalesce(func.count(Plantao.id), 0).label("quantidade_plantoes"),
            )
            .outerjoin(bon, bon.c.plantao_id == Plantao.id)
            .where(
                *_plantoes_do_usuario(usuario_id),
                extract("year", Plantao.data_inicio) == ano,
            )
        )
        r = db.execute(stmt).one()
        total = _to_decimal(r.total)
        total_bon = _to_decimal(r.total_bonificacoes)
        recebidos, qtd_recebidos = _soma_recebidos(
            db,
            extract("year", Plantao.data_inicio) == ano,
            usuario_id=usuario_id,
        )
        return {
            "total": total,
            "total_bonificacoes": total_bon,
            "total_com_bonificacoes": total + total_bon,
            "quantidade_plantoes": int(r.quantidade_plantoes),
            "total_recebidos": recebidos,
            "quantidade_recebidos": qtd_recebidos,
        }

    @staticmethod
    def calcular_grafico_anual(db: Session, usuario_id: int, ano: int) -> list[dict]:
        bon = _bon_subquery()
        stmt = (
            select(
                extract("month", Plantao.data_inicio).label("mes"),
                func.coalesce(func.sum(Plantao.valor), 0).label("total"),
                func.coalesce(func.sum(bon.c.total_bon), 0).label("total_bonificacoes"),
                func.coalesce(func.count(Plantao.id), 0).label("quantidade_plantoes"),
            )
            .outerjoin(bon, bon.c.plantao_id == Plantao.id)
            .where(
                *_plantoes_do_usuario(usuario_id),
                extract("year", Plantao.data_inicio) == ano,
            )
            .group_by(extract("month", Plantao.data_inicio))
        )

        rows = {int(r.mes): r for r in db.execute(stmt).all()}

        result = []
        for mes in range(1, 13):
            if mes in rows:
                r = rows[mes]
                total = _to_decimal(r.total)
                total_bon = _to_decimal(r.total_bonificacoes)
                result.append({
                    "mes": mes,
                    "total": total,
                    "total_bonificacoes": total_bon,
                    "total_com_bonificacoes": total + total_bon,
                    "quantidade_plantoes": int(r.quantidade_plantoes),
                })
            else:
                result.append({
                    "mes": mes,
                    "total": Decimal("0"),
                    "total_bonificacoes": Decimal("0"),
                    "total_com_bonificacoes": Decimal("0"),
                    "quantidade_plantoes": 0,
                })
        return result

    @staticmethod
    def calcular_total_mensal_por_local(db: Session, usuario_id: int, local_id: int, mes: int, ano: int) -> dict:
        bon = _bon_subquery()
        stmt = (
            select(
                func.coalesce(func.sum(Plantao.valor), 0).label("total"),
                func.coalesce(func.sum(bon.c.total_bon), 0).label("total_bonificacoes"),
                func.coalesce(func.count(Plantao.id), 0).label("quantidade_plantoes"),
            )
            .outerjoin(bon, bon.c.plantao_id == Plantao.id)
            .where(
                *_plantoes_do_usuario(usuario_id),
                Plantao.local_id == local_id,
                extract("month", Plantao.data_inicio) == mes,
                extract("year", Plantao.data_inicio) == ano,
            )
        )
        r = db.execute(stmt).one()
        total = _to_decimal(r.total)
        total_bon = _to_decimal(r.total_bonificacoes)
        recebidos, qtd_recebidos = _soma_recebidos(
            db,
            Plantao.local_id == local_id,
            extract("month", Plantao.data_inicio) == mes,
            extract("year", Plantao.data_inicio) == ano,
            usuario_id=usuario_id,
        )
        return {
            "total": total,
            "total_bonificacoes": total_bon,
            "total_com_bonificacoes": total + total_bon,
            "quantidade_plantoes": int(r.quantidade_plantoes),
            "total_recebidos": recebidos,
            "quantidade_recebidos": qtd_recebidos,
        }
