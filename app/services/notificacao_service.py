from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import logging

from sqlalchemy import select, update
from sqlalchemy.orm import Session

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

# Teto de notificacao_antecedencia_minutos aceito pelos schemas (48 h). Define
# até onde, à frente de "agora", pode estar o início de um plantão que já
# está na janela de aviso.
_ANTECEDENCIA_MAXIMA = timedelta(minutes=2880)


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
        agora = agora or agora_em_hora_de_parede()
        # AUD-027: a janela é filtrada em SQL. Antes vinham TODOS os plantões
        # não notificados de todos os usuários a cada minuto — e um plantão
        # cuja janela já passou (cadastro retroativo, worker parado) nunca
        # ficava notificado=True, então era recarregado para sempre. Na
        # janela, o início está entre agora e agora + antecedência máxima;
        # o corte exato por plantão continua abaixo.
        stmt = select(Plantao).where(
            Plantao.notificacao_ativa.is_(True),
            Plantao.notificado.is_(False),
            Plantao.data_inicio >= agora.date(),
            Plantao.data_inicio <= (agora + _ANTECEDENCIA_MAXIMA).date(),
        )
        plantoes = db.scalars(stmt).all()

        plantoes_para_notificar = []

        for plantao in plantoes:
            momento_notificacao = NotificacaoService.calcular_momento_notificacao(plantao)
            inicio_plantao = NotificacaoService.obter_datetime_inicio(plantao)
            if momento_notificacao <= agora < inicio_plantao:
                plantoes_para_notificar.append(plantao)

        return plantoes_para_notificar

    @staticmethod
    def registrar_notificacao(plantao: Plantao) -> None:
        # Não há envio real: as notificações que o usuário vê são locais, no
        # app. Antes esta linha de log levava o e-mail e o valor do plantão
        # (A-11/AUD-027); fica só o que identifica o registro.
        logger.info("Plantão %s na janela de aviso (usuário %s)", plantao.id, plantao.usuario_id)

    @staticmethod
    def processar_notificacoes(db: Session) -> None:
        plantoes = NotificacaoService.buscar_plantoes_para_notificar(db)
        if not plantoes:
            logger.debug("Nenhum plantão para notificar no momento")
            return

        for plantao in plantoes:
            try:
                NotificacaoService.registrar_notificacao(plantao)
                # AUD-022 (contrato K6): marcar `notificado` NÃO pode mexer em
                # atualizado_em. Pelo ORM, o onupdate carimbava o plantão com a
                # hora do servidor, e a edição que o médico tinha feito offline
                # antes disso perdia no last-write-wins e era descartada. O
                # UPDATE explícito com atualizado_em = ele mesmo impede o
                # onupdate (que só entra quando a coluna não está no SET).
                db.execute(
                    update(Plantao)
                    .where(Plantao.id == plantao.id)
                    .values(notificado=True, atualizado_em=Plantao.atualizado_em)
                    .execution_options(synchronize_session=False)
                )
                db.commit()
            except Exception:
                db.rollback()
                logger.exception("Falha ao processar notificação para plantão %s", plantao.id)
