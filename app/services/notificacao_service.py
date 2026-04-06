from datetime import datetime, timedelta

from app.models import Plantao


class NotificacaoService:
    @staticmethod
    def calcular_momento_notificacao(plantao: Plantao) -> datetime:
        inicio = datetime.combine(plantao.data, plantao.horario_inicio)
        return inicio - timedelta(minutes=plantao.notificacao_antecedencia_minutos)
