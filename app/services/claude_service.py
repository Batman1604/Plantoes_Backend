import json
import logging
from datetime import datetime

import anthropic
from sqlalchemy.orm import Session

from app.core.config import settings
from app.repositories.plantao_repository import LocalRepository, PlantaoRepository
from app.schemas.plantao_schema import PlantaoCreateWithUsuario
from app.services.pagamento_service import PagamentoService, ajustar_fim_intervalo

logger = logging.getLogger(__name__)

# Prompt de sistema enviado para o modelo Claude. Define o papel do assistente e as regras de negócio de plantões.
SYSTEM_PROMPT = """Você é o assistente do aplicativo Plantões, que ajuda médicos e profissionais de saúde a gerenciar seus plantões de trabalho.

Você pode ajudar o usuário a:
- Listar seus plantões agendados
- Criar novos plantões (local, data, horário de início, horário de fim e valor são obrigatórios)
- Atualizar informações de plantões existentes
- Remover plantões
- Marcar plantões como executados
- Ver os locais/hospitais cadastrados

Regras importantes:
- Antes de criar um plantão, sempre use listar_locais para obter os IDs disponíveis
- Sempre confirme dados importantes antes de criar ou deletar (ex: "Vou criar um plantão no Hospital X no dia 15/06 às 08h-16h por R$500. Confirma?")
- Se o usuário não informar todos os dados obrigatórios, pergunte um de cada vez
- Responda sempre em português, de forma amigável e objetiva
- Não invente IDs — sempre liste os itens primeiro para o usuário escolher
- Ao mostrar datas, use o formato DD/MM/YYYY; ao chamar tools, use YYYY-MM-DD
- Ao mostrar horários, use o formato HH:MM; ao chamar tools, use HH:MM
- Se houver conflito de horário ao criar um plantão, informe o usuário e pergunte se quer manter, ajustar ou cancelar"""

TOOLS = [
    {
        "name": "listar_locais",
        "description": "Lista todos os locais/hospitais cadastrados pelo usuário. Chame isso antes de criar um plantão para descobrir os IDs disponíveis.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "listar_plantoes",
        "description": "Lista todos os plantões agendados do usuário, incluindo ID, data, horário, local e valor.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "criar_plantao",
        "description": "Cria um novo plantão. Requer local_id (obtido via listar_locais), valor, data_inicio e data_fim.",
        "input_schema": {
            "type": "object",
            "required": ["local_id", "valor", "data_inicio", "data_fim"],
            "properties": {
                "local_id": {"type": "integer", "description": "ID do local onde o plantão será realizado"},
                "valor": {"type": "number", "description": "Valor em reais do plantão"},
                "data_inicio": {"type": "string", "description": "Data e hora de início no formato ISO 8601: YYYY-MM-DDTHH:MM:SS"},
                "data_fim": {"type": "string", "description": "Data e hora de fim no formato ISO 8601: YYYY-MM-DDTHH:MM:SS"},
                "sobreaviso": {"type": "boolean", "description": "Se é plantão de sobreaviso"},
                "recorrencia": {
                    "type": "string",
                    "enum": ["Semanal", "Quinzenal", "Unico"],
                    "description": "Recorrência opcional do plantão",
                },
                "acao_conflito": {
                    "type": "string",
                    "enum": ["manter", "ajustar", "cancelar"],
                    "description": "Ação a tomar caso haja conflito de horário com outro plantão",
                },
            },
        },
    },
    {
        "name": "remover_plantao",
        "description": "Remove um plantão pelo ID. Use listar_plantoes primeiro para obter o ID correto.",
        "input_schema": {
            "type": "object",
            "required": ["plantao_id"],
            "properties": {"plantao_id": {"type": "integer", "description": "ID do plantão a ser removido"}},
        },
    },
    {
        "name": "atualizar_plantao",
        "description": "Atualiza dados de um plantão existente. Use listar_plantoes primeiro para obter o ID correto.",
        "input_schema": {
            "type": "object",
            "required": ["plantao_id"],
            "properties": {
                "plantao_id": {"type": "integer"},
                "valor": {"type": "number"},
                "data_inicio": {"type": "string", "description": "Nova data e hora de início no formato ISO 8601: YYYY-MM-DDTHH:MM:SS"},
                "data_fim": {"type": "string", "description": "Nova data e hora de fim no formato ISO 8601: YYYY-MM-DDTHH:MM:SS"},
                "sobreaviso": {"type": "boolean"},
            },
        },
    },
    {
        "name": "marcar_plantao_executado",
        "description": "Marca um plantão como executado ou não executado.",
        "input_schema": {
            "type": "object",
            "required": ["plantao_id", "executado"],
            "properties": {
                "plantao_id": {"type": "integer"},
                "executado": {"type": "boolean", "description": "True para marcar como executado, False para desmarcar"},
            },
        },
    },
]


def _executar_tool(db: Session, usuario_id: int, nome: str, inputs: dict) -> str:
    """Roteia chamadas de ferramenta do Claude para a implementação interna correspondente."""
    try:
        if nome == "listar_locais":
            return _tool_listar_locais(db, usuario_id)
        if nome == "listar_plantoes":
            return _tool_listar_plantoes(db, usuario_id)
        if nome == "criar_plantao":
            return _tool_criar_plantao(db, usuario_id, inputs)
        if nome == "remover_plantao":
            return _tool_remover_plantao(db, usuario_id, inputs)
        if nome == "atualizar_plantao":
            return _tool_atualizar_plantao(db, usuario_id, inputs)
        if nome == "marcar_plantao_executado":
            return _tool_marcar_executado(db, usuario_id, inputs)
        return "Ferramenta desconhecida."
    except Exception:
        logger.exception("Erro ao executar tool '%s'", nome)
        return "Ocorreu um erro ao executar esta operação. Tente novamente."


def _tool_listar_locais(db: Session, usuario_id: int) -> str:
    """Retorna uma lista de locais/hospitais do usuário em formato de texto para o assistente."""
    locais = LocalRepository.list_by_user(db, usuario_id)
    if not locais:
        return "Nenhum local cadastrado. O usuário precisa criar um local no aplicativo antes de agendar plantões."
    linhas = ["Locais cadastrados:"]
    for local in locais:
        linhas.append(
            f"- ID {local.id}: {local.nome_local}"
            + (f" | Endereço: {local.endereco_local}" if local.endereco_local else "")
        )
    return "\n".join(linhas)


def _tool_listar_plantoes(db: Session, usuario_id: int) -> str:
    """Retorna os plantões agendados do usuário em texto legível, incluindo status."""
    plantoes = PlantaoRepository.list_by_user(db, usuario_id)
    if not plantoes:
        return "Nenhum plantão agendado."
    linhas = ["Plantões agendados:"]
    for p in plantoes:
        data_str = p.data_inicio.strftime("%d/%m/%Y")
        inicio_str = p.horario_inicio.strftime("%H:%M")
        fim_str = p.horario_fim.strftime("%H:%M")
        executado = "✓ executado" if p.executado else "pendente"
        linhas.append(
            f"- ID {p.id}: {data_str} {inicio_str}-{fim_str} | "
            f"Local ID {p.local_id} | R${float(p.valor):.2f} | {executado}"
            + (" | sobreaviso" if p.sobreaviso else "")
        )
    return "\n".join(linhas)


def _tool_criar_plantao(db: Session, usuario_id: int, inputs: dict) -> str:
    """Cria um novo plantão a partir dos parâmetros recebidos pela tool do assistente."""
    local = LocalRepository.get_by_id(db, inputs["local_id"])
    if local is None or local.usuario_id != usuario_id:
        return "Local não encontrado ou não pertence ao usuário."

    try:
        inicio_dt = datetime.fromisoformat(inputs["data_inicio"])
        fim_dt = datetime.fromisoformat(inputs["data_fim"])
    except ValueError:
        return "Formato de data/hora inválido. Use ISO 8601: YYYY-MM-DDTHH:MM:SS"

    fim_dt = ajustar_fim_intervalo(inicio_dt, fim_dt)

    if (fim_dt - inicio_dt).total_seconds() > 48 * 3600:
        return "A duração do plantão não pode ser maior que 48 horas."

    conflito = PlantaoRepository.get_conflito(db, usuario_id=usuario_id, inicio=inicio_dt, fim=fim_dt)
    acao = inputs.get("acao_conflito")

    if conflito and acao is None:
        conf_inicio = datetime.combine(conflito.data_inicio, conflito.horario_inicio).strftime("%d/%m/%Y %H:%M")
        conf_fim = conflito.horario_fim.strftime("%H:%M")
        return (
            f"Conflito detectado com o plantão ID {conflito.id} ({conf_inicio}-{conf_fim}). "
            "Informe a ação desejada: 'manter' (criar mesmo assim), 'ajustar' (encurtar o conflitante) ou 'cancelar' (não criar)."
        )

    if acao == "cancelar":
        return "Criação de plantão cancelada conforme solicitado."

    if acao == "ajustar" and conflito:
        PlantaoRepository.update(db, conflito.id, horario_fim=inicio_dt.time())

    recorrencia = inputs.get("recorrencia")
    pagamento_plantao = None
    if local.dia_pagamento is not None:
        pagamento_plantao = PagamentoService.calcular_data_pagamento_plantao(
            data_plantao=inicio_dt.date(),
            indice_dia_pagamento=local.dia_pagamento,
            data_inicio_ciclo=local.data_inicio,
            data_final_ciclo=local.data_final,
            util_corrido=local.util_corrido,
        )

    payload = PlantaoCreateWithUsuario(
        usuario_id=usuario_id,
        local_id=inputs["local_id"],
        valor=inputs["valor"],
        data_inicio=inicio_dt.date(),
        data_fim=fim_dt.date(),
        horario_inicio=inicio_dt.time(),
        horario_fim=fim_dt.time(),
        sobreaviso=inputs.get("sobreaviso", False),
        recorrencia=recorrencia,
        notificacao_ativa=True,
        notificacao_antecedencia_minutos=60,
        executado=False,
        pagamento_plantao=pagamento_plantao,
        transferido=False,
    )

    plantao = PlantaoRepository.create(db, payload)
    data_fmt = plantao.data_inicio.strftime("%d/%m/%Y")
    inicio_fmt = plantao.horario_inicio.strftime("%H:%M")
    fim_fmt = plantao.horario_fim.strftime("%H:%M")
    return f"Plantão criado com sucesso! ID {plantao.id} — {data_fmt} {inicio_fmt}-{fim_fmt} | R${float(plantao.valor):.2f}"


def _tool_remover_plantao(db: Session, usuario_id: int, inputs: dict) -> str:
    """Remove um plantão pelo ID garantindo que pertença ao usuário."""
    deletado = PlantaoRepository.delete_by_user(db, inputs["plantao_id"], usuario_id)
    if not deletado:
        return f"Plantão ID {inputs['plantao_id']} não encontrado."
    return f"Plantão ID {inputs['plantao_id']} removido com sucesso."


def _tool_atualizar_plantao(db: Session, usuario_id: int, inputs: dict) -> str:
    """Atualiza um plantão existente com os dados enviados pelo assistente."""
    plantao_id = inputs.pop("plantao_id")
    kwargs = {}

    if "data_inicio" in inputs or "data_fim" in inputs:
        plantao = PlantaoRepository.get_by_id_and_user(db, plantao_id, usuario_id)
        if plantao is None:
            return f"Plantão ID {plantao_id} não encontrado."

        try:
            if "data_inicio" in inputs:
                inicio_dt = datetime.fromisoformat(inputs["data_inicio"])
                kwargs["data_inicio"] = inicio_dt.date()
                kwargs["horario_inicio"] = inicio_dt.time()
            if "data_fim" in inputs:
                fim_dt = datetime.fromisoformat(inputs["data_fim"])
                inicio_ref = datetime.fromisoformat(inputs["data_inicio"]) if "data_inicio" in inputs else datetime.combine(plantao.data_inicio, plantao.horario_inicio)
                fim_dt = ajustar_fim_intervalo(inicio_ref, fim_dt)
                kwargs["horario_fim"] = fim_dt.time()
        except ValueError:
            return "Formato de data/hora inválido. Use ISO 8601: YYYY-MM-DDTHH:MM:SS"

    if "valor" in inputs:
        kwargs["valor"] = inputs["valor"]
    if "sobreaviso" in inputs:
        kwargs["sobreaviso"] = inputs["sobreaviso"]

    if not kwargs:
        return "Nenhum campo para atualizar."

    atualizado = PlantaoRepository.update_by_user(db, plantao_id, usuario_id, **kwargs)
    if atualizado is None:
        return f"Plantão ID {plantao_id} não encontrado."
    return f"Plantão ID {plantao_id} atualizado com sucesso."


def _tool_marcar_executado(db: Session, usuario_id: int, inputs: dict) -> str:
    """Marca ou desmarca um plantão como executado para o usuário."""
    resultado = PlantaoRepository.update_executado(db, inputs["plantao_id"], usuario_id, inputs["executado"])
    if resultado is None:
        return f"Plantão ID {inputs['plantao_id']} não encontrado."
    status = "executado" if inputs["executado"] else "desmarcado como executado"
    return f"Plantão ID {inputs['plantao_id']} marcado como {status}."


def processar_mensagem(db: Session, usuario_id: int, historico_json: str, mensagem_nova: str) -> tuple[str, str]:
    """
    Processa uma mensagem do usuário via Claude com tool use.
    Retorna uma tupla com a resposta final do assistente e o histórico atualizado em JSON.
    """
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    historico: list[dict] = json.loads(historico_json) if historico_json else []

    # Limita o histórico às últimas 20 mensagens para não exceder o contexto
    if len(historico) > 20:
        historico = historico[-20:]

    messages = historico + [{"role": "user", "content": mensagem_nova}]

    resposta_final = "Desculpe, não consegui processar sua mensagem. Tente novamente."

    for _ in range(5):  # máximo de 5 rodadas de tool use por mensagem
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        if response.stop_reason == "end_turn":
            resposta_final = next(
                (block.text for block in response.content if hasattr(block, "text")),
                "Não entendi. Pode reformular?",
            )
            break

        if response.stop_reason == "tool_use":
            # Adiciona resposta do assistente (com tool_use blocks) ao histórico de contexto
            messages.append({"role": "assistant", "content": response.content})

            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    logger.info("Claude chamou tool '%s' com inputs: %s", block.name, block.input)
                    resultado = _executar_tool(db, usuario_id, block.name, block.input)
                    logger.info("Resultado da tool '%s': %s", block.name, resultado)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": resultado,
                    })

            messages.append({"role": "user", "content": tool_results})
        else:
            break

    # Persiste apenas mensagens de texto no histórico (não os blocos de tool_use)
    historico.append({"role": "user", "content": mensagem_nova})
    historico.append({"role": "assistant", "content": resposta_final})

    return resposta_final, json.dumps(historico, ensure_ascii=False)
