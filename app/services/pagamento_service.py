from calendar import monthrange
from datetime import date, datetime, timedelta


class PagamentoService:
    @staticmethod
    def calcular_dia_util(ano: int, mes: int, dia: int) -> int:
        ultimo_dia = monthrange(ano, mes)[1]
        dia_base = min(dia, ultimo_dia)
        data_pagamento = date(ano, mes, dia_base)

        while data_pagamento.weekday() >= 5:
            data_pagamento += timedelta(days=1)

        return data_pagamento.day

    @staticmethod
    def calcular_data_pagamento_plantao(data_plantao: date, dia_pagamento: int) -> date:
        ano = data_plantao.year
        mes = data_plantao.month

        if data_plantao.day > dia_pagamento:
            if mes == 12:
                mes = 1
                ano += 1
            else:
                mes += 1

        dia_util = PagamentoService.calcular_dia_util(ano, mes, dia_pagamento)
        return date(ano, mes, dia_util)


def ajustar_fim_intervalo(inicio: datetime, fim: datetime) -> datetime:
    if fim <= inicio:
        return fim + timedelta(days=1)
    return fim
