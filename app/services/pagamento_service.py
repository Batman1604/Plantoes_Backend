from calendar import monthrange
from datetime import date, datetime, timedelta


class PagamentoService:
    @staticmethod
    def _ajustar_ano_mes(ano: int, mes: int, deslocamento_meses: int) -> tuple[int, int]:
        """Calcula ano e mês resultantes após deslocamento de meses."""
        indice_mes = (ano * 12 + (mes - 1)) + deslocamento_meses
        novo_ano = indice_mes // 12
        novo_mes = (indice_mes % 12) + 1
        return novo_ano, novo_mes

    @staticmethod
    def _data_com_dia_do_mes(ano: int, mes: int, dia: int) -> date:
        """Retorna a data do dia solicitado no mês especificado, ajustando para o último dia válido."""
        ultimo_dia = monthrange(ano, mes)[1]
        return date(ano, mes, min(dia, ultimo_dia))

    @staticmethod
    def _dia_do_ciclo_no_mes(ano: int, mes: int, dia_referencia: int) -> int:
        """Calcula o dia do ciclo de pagamento dentro do mês, respeitando o último dia do mês."""
        ultimo_dia = monthrange(ano, mes)[1]
        return min(dia_referencia, ultimo_dia)

    @staticmethod
    def calcular_data_dia_util_do_mes(ano: int, mes: int, indice_dia_util: int) -> date:
        """Retorna o N-ésimo dia útil de um mês, ou o último dia útil se não houver dias suficientes."""
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
        util_corrido: bool = True,
    ) -> date:
        """Calcula a data de pagamento do plantão com base no ciclo de fechamento do local."""
        dia_inicio = data_inicio_ciclo.day
        dia_final = data_final_ciclo.day
        ciclo_cruza_mes = dia_inicio > dia_final

        dia_inicio_mes_plantao = PagamentoService._dia_do_ciclo_no_mes(
            data_plantao.year,
            data_plantao.month,
            dia_inicio,
        )
        dia_final_mes_plantao = PagamentoService._dia_do_ciclo_no_mes(
            data_plantao.year,
            data_plantao.month,
            dia_final,
        )

        ano_fechamento = data_plantao.year
        mes_fechamento = data_plantao.month

        if ciclo_cruza_mes:
            if data_plantao.day > dia_final_mes_plantao:
                ano_fechamento, mes_fechamento = PagamentoService._ajustar_ano_mes(ano_fechamento, mes_fechamento, 1)
        else:
            if data_plantao.day > dia_final_mes_plantao:
                ano_fechamento, mes_fechamento = PagamentoService._ajustar_ano_mes(ano_fechamento, mes_fechamento, 1)
            elif data_plantao.day < dia_inicio_mes_plantao:
                ano_fechamento, mes_fechamento = PagamentoService._ajustar_ano_mes(ano_fechamento, mes_fechamento, -1)

        dia_final_mes_fechamento = PagamentoService._dia_do_ciclo_no_mes(ano_fechamento, mes_fechamento, dia_final)
        data_fechamento = PagamentoService._data_com_dia_do_mes(
            ano_fechamento,
            mes_fechamento,
            dia_final_mes_fechamento,
        )

        if util_corrido:
            data_pagamento = PagamentoService.calcular_data_dia_util_do_mes(
                ano_fechamento,
                mes_fechamento,
                indice_dia_pagamento,
            )
        else:
            data_pagamento = PagamentoService._data_com_dia_do_mes(
                ano_fechamento,
                mes_fechamento,
                indice_dia_pagamento,
            )

        if data_pagamento <= data_fechamento:
            ano_pagamento, mes_pagamento = PagamentoService._ajustar_ano_mes(ano_fechamento, mes_fechamento, 1)
            if util_corrido:
                data_pagamento = PagamentoService.calcular_data_dia_util_do_mes(
                    ano_pagamento,
                    mes_pagamento,
                    indice_dia_pagamento,
                )
            else:
                data_pagamento = PagamentoService._data_com_dia_do_mes(
                    ano_pagamento,
                    mes_pagamento,
                    indice_dia_pagamento,
                )

        return data_pagamento

    @staticmethod
    def calcular_data_pagamento_por_dia_mes(data_plantao: date, dia_pagamento: int, util_corrido: bool = True) -> date:
        """Calcula a data de pagamento no mês seguinte ao plantão, ajustando para dia útil quando necessário."""
        if dia_pagamento < 1:
            raise ValueError("O dia de pagamento deve ser maior ou igual a 1")

        ano_pagamento, mes_pagamento = PagamentoService._ajustar_ano_mes(
            data_plantao.year,
            data_plantao.month,
            1,
        )
        data_pagamento = PagamentoService._data_com_dia_do_mes(ano_pagamento, mes_pagamento, dia_pagamento)

        if util_corrido:
            while data_pagamento.weekday() >= 5:
                data_pagamento += timedelta(days=1)

        return data_pagamento


def ajustar_fim_intervalo(inicio: datetime, fim: datetime) -> datetime:
    """Garante que o horário de fim seja posterior ao início, ajustando para o dia seguinte quando necessário."""
    if fim <= inicio:
        return fim + timedelta(days=1)
    return fim
