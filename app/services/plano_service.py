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

    # A v1 é publicada SEM monetização, e por isso sem restrição por plano.
    #
    # O motivo é concreto: o aplicativo limitava o plano gratuito a 10 plantões
    # e escondia todos os relatórios financeiros atrás do plano premium,
    # exibindo "Faça upgrade para premium" — mas não existe nenhuma tela de
    # compra. O único caminho de upgrade é POST /api/usuarios/upgrade, um
    # webhook protegido por segredo compartilhado. O usuário chegava a um beco
    # sem saída: ao criar o 11º plantão offline, recebia 403 na sincronização,
    # o item esgotava as tentativas, ia para a fila de erro, e a única saída
    # era descartá-lo — sendo que logout() se recusa a prosseguir enquanto
    # houver erro na fila. Cobrar por fora das lojas também violaria a política
    # de pagamentos do Google Play e da App Store para conteúdo digital.
    #
    # A coluna `plano`, o upgrade por webhook e as funções abaixo continuam
    # existindo: quando houver cobrança de verdade (Play Billing / StoreKit),
    # basta voltar este valor para True. Um interruptor único evita que a
    # decisão fique espalhada por 11 pontos de chamada.
    RESTRICOES_POR_PLANO_ATIVAS = False

    @staticmethod
    def obter_plano(usuario: User) -> str:
        plano = (usuario.plano or PlanoService.PLANO_FREE).strip().lower()
        if plano not in {PlanoService.PLANO_FREE, PlanoService.PLANO_PREMIUM}:
            return PlanoService.PLANO_FREE
        return plano

    @staticmethod
    def exigir_premium(usuario: User) -> None:
        """Levanta erro se o usuário não for premium para recursos exclusivos."""
        if not PlanoService.RESTRICOES_POR_PLANO_ATIVAS:
            return
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
        if not PlanoService.RESTRICOES_POR_PLANO_ATIVAS:
            return
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
        if not PlanoService.RESTRICOES_POR_PLANO_ATIVAS:
            return
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
