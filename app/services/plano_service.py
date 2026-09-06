from datetime import date

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Plantao, User


class PlanoService:
    """Serviço que controla regras de limite e upgrade de plano de usuário."""
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
        """Levanta erro se o usuário não for premium para recursos exclusivos."""
        if PlanoService.obter_plano(usuario) != PlanoService.PLANO_PREMIUM:
            raise HTTPException(
                status_code=403,
                detail="Funcionalidade exclusiva para usuários premium",
            )

    @staticmethod
    def _contar_plantoes_ativos(db: Session, usuario_id: int) -> int:
        # Um plantão transferido "sai imediatamente da lista" de quem transferiu (ver
        # transferir_plantao em plantao_routes.py) — não deve continuar ocupando vaga no
        # limite do plano free de quem o cedeu.
        return db.scalar(
            select(func.count())
            .select_from(Plantao)
            .where(Plantao.usuario_id == usuario_id, Plantao.transferido.is_(False))
        ) or 0

    @staticmethod
    def limitar_plano_free(db: Session, usuario: User) -> None:
        """Verifica o limite de plantões do plano gratuito antes de criar novos plantões."""
        if PlanoService.obter_plano(usuario) != PlanoService.PLANO_FREE:
            return

        total = PlanoService._contar_plantoes_ativos(db, usuario.id)
        if total >= PlanoService.LIMITE_PLANTOES_FREE:
            raise HTTPException(
                status_code=403,
                detail=f"Limite do plano gratuito atingido ({PlanoService.LIMITE_PLANTOES_FREE} plantões)",
            )

    @staticmethod
    def verificar_limite_serie(db: Session, usuario: User, novos: int) -> None:
        """Valida se uma operação que adiciona `novos` plantões ativos excede o limite do
        plano free. `novos` deve refletir o ganho líquido da operação (ex.: numa troca onde o
        usuário também cede um plantão ao mesmo tempo, o ganho líquido pode ser zero)."""
        if novos <= 0:
            return
        if PlanoService.obter_plano(usuario) != PlanoService.PLANO_FREE:
            return
        total = PlanoService._contar_plantoes_ativos(db, usuario.id)
        if total + novos > PlanoService.LIMITE_PLANTOES_FREE:
            raise HTTPException(
                status_code=403,
                detail=f"Operação excederia o limite do plano gratuito ({PlanoService.LIMITE_PLANTOES_FREE} plantões). Faça upgrade para premium.",
            )

    @staticmethod
    def upgrade_para_premium(usuario: User) -> bool:
        """Atualiza o plano do usuário para premium se ainda não for premium."""
        plano_atual = PlanoService.obter_plano(usuario)
        if plano_atual == PlanoService.PLANO_PREMIUM:
            return False

        usuario.plano = PlanoService.PLANO_PREMIUM
        usuario.pagamento_realizado = date.today()
        return True
