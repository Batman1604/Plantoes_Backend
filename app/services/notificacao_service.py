from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import Plantao

logger = logging.getLogger(__name__)

# data_inicio + horario_inicio descrevem HORA DE PAREDE: "das 07:00 às 19:00 do
# dia 10/04" é o que o profissional combinou, e vale igual em qualquer fuso (ver
# FUSO-01). Para decidir se já é hora de avisar, o "agora" precisa estar na
# MESMA régua — hora de parede no fuso de quem trabalha.
#
# Antes aqui havia datetime.now(), que devolve a hora de parede do SERVIDOR.
# Hospedagem roda em UTC por padrão, então um plantão das 07:00 era comparado
# com um "agora" três horas à frente e a notificação saía às 04:00 (ver
# auditoria A-16). Era o único datetime.now() sem fuso no backend, e estava
# justamente onde a hora de parede importa.
FUSO_DO_PLANTAO = ZoneInfo("America/Sao_Paulo")


def agora_em_hora_de_parede() -> datetime:
    """"Agora" na régua de data_inicio/horario_inicio: sem fuso, hora local."""
    return datetime.now(FUSO_DO_PLANTAO).replace(tzinfo=None)


class NotificacaoService:
    @staticmethod
    def obter_datetime_inicio(plantao: Plantao) -> datetime:
        return datetime.combine(plantao.data_inicio, plantao.horario_inicio)

    @staticmethod
    def calcular_momento_notificacao(plantao: Plantao) -> datetime:
        inicio_plantao = NotificacaoService.obter_datetime_inicio(plantao)
        return inicio_plantao - timedelta(minutes=plantao.notificacao_antecedencia_minutos)

    @staticmethod
    def buscar_plantoes_para_notificar(
        db: Session,
        agora: datetime | None = None,
    ) -> list[Plantao]:
        """`agora` é injetável para teste; em produção vem de
        agora_em_hora_de_parede()."""
        stmt = (
            select(Plantao)
            .where(
                Plantao.notificacao_ativa.is_(True),
                Plantao.notificado.is_(False),
            )
            .options(joinedload(Plantao.usuario))
        )
        plantoes = db.scalars(stmt).unique().all()

        agora = agora or agora_em_hora_de_parede()
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
        logger.info(mensagem)

    @staticmethod
    def processar_notificacoes(db: Session) -> None:
        plantoes = NotificacaoService.buscar_plantoes_para_notificar(db)
        if not plantoes:
            logger.debug("Nenhum plantão para notificar no momento")
            return

        for plantao in plantoes:
            try:
                if plantao.usuario is not None:
                    email = plantao.usuario.email
                else:
                    email = "destinatario@desconhecido"

                NotificacaoService.enviar_email_notificacao(email, plantao)
                plantao.notificado = True
                db.add(plantao)
                db.commit()
            except Exception:
                db.rollback()
                logger.exception("Falha ao processar notificação para plantão %s", plantao.id)
