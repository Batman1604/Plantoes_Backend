from datetime import date

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Plantao, User


class PlanoService:
    PLANO_FREE = "free"
    PLANO_PREMIUM = "premium"
    LIMITE_PLANTOES_FREE = 10

    @staticmethod
    def obter_plano(usuario: User) -> str:
        plano = (usuario.plano or PlanoService.PLANO_FREE).strip().lower()
        if plano not in {PlanoService.PLANO_FREE, PlanoService.PLANO_PREMIUM}:
            return PlanoService.PLANO_FREE
        return plano

    @staticmethod
    def exigir_premium(usuario: User) -> None:
        if PlanoService.obter_plano(usuario) != PlanoService.PLANO_PREMIUM:
            raise HTTPException(
                status_code=403,
                detail="Funcionalidade exclusiva para usuários premium",
            )

    @staticmethod
    def limitar_plano_free(db: Session, usuario: User) -> None:
        if PlanoService.obter_plano(usuario) != PlanoService.PLANO_FREE:
            return

        total = db.scalar(select(func.count()).select_from(Plantao).where(Plantao.usuario_id == usuario.id)) or 0
        if total >= PlanoService.LIMITE_PLANTOES_FREE:
            raise HTTPException(
                status_code=403,
                detail=f"Limite do plano gratuito atingido ({PlanoService.LIMITE_PLANTOES_FREE} plantões)",
            )

    @staticmethod
    def verificar_limite_serie(db: Session, usuario: User, novos: int) -> None:
        if PlanoService.obter_plano(usuario) != PlanoService.PLANO_FREE:
            return
        total = db.scalar(select(func.count()).select_from(Plantao).where(Plantao.usuario_id == usuario.id)) or 0
        if total + novos > PlanoService.LIMITE_PLANTOES_FREE:
            raise HTTPException(
                status_code=403,
                detail=f"Operação excederia o limite do plano gratuito ({PlanoService.LIMITE_PLANTOES_FREE} plantões). Faça upgrade para premium.",
            )

    @staticmethod
    def upgrade_para_premium(usuario: User) -> bool:
        plano_atual = PlanoService.obter_plano(usuario)
        if plano_atual == PlanoService.PLANO_PREMIUM:
            return False

        usuario.plano = PlanoService.PLANO_PREMIUM
        usuario.pagamento_realizado = date.today()
        return True
