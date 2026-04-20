from calendar import monthrange
from datetime import date, datetime, timedelta


class PagamentoService:
    @staticmethod
    def _ajustar_ano_mes(ano: int, mes: int, deslocamento_meses: int) -> tuple[int, int]:
        indice_mes = (ano * 12 + (mes - 1)) + deslocamento_meses
        novo_ano = indice_mes // 12
        novo_mes = (indice_mes % 12) + 1
        return novo_ano, novo_mes

    @staticmethod
    def _data_com_dia_do_mes(ano: int, mes: int, dia: int) -> date:
        ultimo_dia = monthrange(ano, mes)[1]
        return date(ano, mes, min(dia, ultimo_dia))

    @staticmethod
    def calcular_data_dia_util_do_mes(ano: int, mes: int, indice_dia_util: int) -> date:
        if indice_dia_util < 1:
            raise ValueError("O índice do dia útil deve ser maior ou igual a 1")

        dia_util_encontrado = 0
        ultimo_dia = monthrange(ano, mes)[1]
        for dia in range(1, ultimo_dia + 1):
            data_corrente = date(ano, mes, dia)
            if data_corrente.weekday() < 5:
                dia_util_encontrado += 1
                if dia_util_encontrado == indice_dia_util:
                    return data_corrente

        # Fallback para meses com menos dias úteis do que o índice solicitado
        for dia in range(ultimo_dia, 0, -1):
            data_corrente = date(ano, mes, dia)
            if data_corrente.weekday() < 5:
                return data_corrente

        raise ValueError("Não foi possível calcular um dia útil para o mês informado")

    @staticmethod
    def calcular_data_pagamento_plantao(
        data_plantao: date,
        indice_dia_pagamento: int,
        data_inicio_ciclo: date,
        data_final_ciclo: date,
    ) -> date:
        dia_inicio = data_inicio_ciclo.day
        dia_final = data_final_ciclo.day
        ciclo_cruza_mes = dia_inicio > dia_final

        ano_fechamento = data_plantao.year
        mes_fechamento = data_plantao.month

        if ciclo_cruza_mes:
            if data_plantao.day > dia_final:
                ano_fechamento, mes_fechamento = PagamentoService._ajustar_ano_mes(ano_fechamento, mes_fechamento, 1)
        else:
            if data_plantao.day > dia_final:
                ano_fechamento, mes_fechamento = PagamentoService._ajustar_ano_mes(ano_fechamento, mes_fechamento, 1)
            elif data_plantao.day < dia_inicio:
                ano_fechamento, mes_fechamento = PagamentoService._ajustar_ano_mes(ano_fechamento, mes_fechamento, -1)

        data_fechamento = PagamentoService._data_com_dia_do_mes(ano_fechamento, mes_fechamento, dia_final)
        data_pagamento = PagamentoService.calcular_data_dia_util_do_mes(
            ano_fechamento,
            mes_fechamento,
            indice_dia_pagamento,
        )

        if data_pagamento <= data_fechamento:
            ano_pagamento, mes_pagamento = PagamentoService._ajustar_ano_mes(ano_fechamento, mes_fechamento, 1)
            data_pagamento = PagamentoService.calcular_data_dia_util_do_mes(
                ano_pagamento,
                mes_pagamento,
                indice_dia_pagamento,
            )

        return data_pagamento


def ajustar_fim_intervalo(inicio: datetime, fim: datetime) -> datetime:
    if fim <= inicio:
        return fim + timedelta(days=1)
    return fim
