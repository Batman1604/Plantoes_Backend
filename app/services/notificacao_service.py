from datetime import datetime, timedelta
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Plantao

logger = logging.getLogger(__name__)


class NotificacaoService:
    @staticmethod
    def obter_datetime_inicio(plantao: Plantao) -> datetime:
        return datetime.combine(plantao.data, plantao.horario_inicio)

    @staticmethod
    def calcular_momento_notificacao(plantao: Plantao) -> datetime:
        inicio_plantao = NotificacaoService.obter_datetime_inicio(plantao)
        return inicio_plantao - timedelta(minutes=plantao.notificacao_antecedencia_minutos)

    @staticmethod
    def buscar_plantoes_para_notificar(db: Session) -> list[Plantao]:
        stmt = select(Plantao).where(
            Plantao.notificacao_ativa.is_(True),
            Plantao.notificado.is_(False),
        )
        plantoes = db.scalars(stmt).all()

        agora = datetime.now()
        plantoes_para_notificar = []

        for plantao in plantoes:
            momento_notificacao = NotificacaoService.calcular_momento_notificacao(plantao)
            inicio_plantao = NotificacaoService.obter_datetime_inicio(plantao)
            if momento_notificacao <= agora < inicio_plantao:
                plantoes_para_notificar.append(plantao)

        return plantoes_para_notificar

    @staticmethod
    def enviar_email_notificacao(email: str, plantao: Plantao) -> None:
        mensagem = (
            f"Notificação para usuário {email}: seu plantão está agendado para "
            f"{NotificacaoService.obter_datetime_inicio(plantao).strftime('%Y-%m-%d %H:%M')} "
            f"(valor R${plantao.valor})."
        )
        logger.info("Enviando notificação para plantão %s do usuário %s", plantao.id, email)
        print(mensagem)

    @staticmethod
    def processar_notificacoes(db: Session) -> None:
        plantoes = NotificacaoService.buscar_plantoes_para_notificar(db)
        if not plantoes:
            logger.debug("Nenhum plantão para notificar no momento")
            return

        for plantao in plantoes:
            try:
                if plantao.local is not None and plantao.local.usuario is not None:
                    email = plantao.local.usuario.email
                else:
                    email = "destinatario@desconhecido"

                NotificacaoService.enviar_email_notificacao(email, plantao)
                plantao.notificado = True
                db.add(plantao)
                db.commit()
            except Exception as exc:
                db.rollback()
                logger.exception("Falha ao processar notificação para plantão %s", plantao.id)
