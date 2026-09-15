from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Generic, Literal, Optional, TypeVar

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator
from pydantic.generics import GenericModel


DataT = TypeVar("DataT")


def validar_formato_data(value: date | datetime | str | None):
    """Valida que a data fornecida está no formato de data sem horário ou timezone."""
    if isinstance(value, datetime):
        raise ValueError("Envie datas no formato YYYY-MM-DD, sem horário/fuso.")

    if isinstance(value, str) and "T" in value:
        raise ValueError("Envie datas no formato YYYY-MM-DD, sem horário/fuso.")

    return value


class APIResponse(GenericModel, Generic[DataT]):
    success: bool
    message: str
    data: DataT | None = None


class UserBase(BaseModel):
    nome: str = Field(min_length=2, max_length=120, example="João Silva")
    email: EmailStr = Field(example="joao@example.com")


class UserCreate(UserBase):
    telefone: Optional[str] = Field(default=None, max_length=30, example="11999999999")
    senha: str = Field(min_length=8, max_length=128, example="SenhaForte123")


class UserLogin(BaseModel):
    email: EmailStr = Field(example="joao@example.com")
    senha: str = Field(min_length=8, max_length=128, example="SenhaForte123")


class UserResponse(UserBase):
    id: int
    telefone: Optional[str] = None
    plano: Literal["free", "premium"] = "free"
    criado_em: datetime
    pagamento_realizado: Optional[date] = None
    email_verificado: bool = False
    telefone_verificado: bool = False
    avatar: Optional[str] = None
    profissao: Optional[str] = None
    theme_mode: Literal["light", "dark"] = "light"

    class Config:
        from_attributes = True


class PerfilUpdate(BaseModel):
    avatar: Optional[str] = Field(default=None, max_length=255)
    profissao: Optional[str] = Field(default=None, max_length=255)
    theme_mode: Optional[Literal["light", "dark"]] = None


class AuthResponse(BaseModel):
    autenticado: bool
    access_token: str
    token_type: str = "bearer"
    usuario: UserResponse


class AuthStatusResponse(BaseModel):
    autenticado: bool
    usuario: Optional[UserResponse] = None


class GoogleAuthRequest(BaseModel):
    id_token: str = Field(min_length=1, description="ID Token JWT emitido pelo Google Sign-In para este app.")


class AppleAuthRequest(BaseModel):
    identity_token: str = Field(min_length=1, description="Identity Token JWT emitido pelo Sign in with Apple.")
    nome: Optional[str] = Field(
        default=None,
        max_length=120,
        description=(
            "Nome completo do usuário. A Apple só envia o nome na primeira autorização "
            "(fora do identity_token, no retorno nativo do SDK) — envie aqui nessa primeira "
            "chamada; em logins seguintes pode ser omitido."
        ),
    )


class VerificarTelefoneRequest(BaseModel):
    codigo: str = Field(min_length=6, max_length=6, description="Código de 6 dígitos recebido via WhatsApp.")


class IniciarCadastroRequest(UserBase):
    telefone: str = Field(max_length=30, example="11999999999")
    senha: str = Field(min_length=8, max_length=128, example="SenhaForte123")


class ConfirmarCadastroRequest(BaseModel):
    email: EmailStr = Field(example="joao@example.com")
    codigo: str = Field(min_length=6, max_length=6, description="Código de 6 dígitos recebido via WhatsApp.")


class ReenviarCadastroRequest(BaseModel):
    email: EmailStr = Field(example="joao@example.com")


class SenhaResetRequest(BaseModel):
    email: EmailStr = Field(example="joao@example.com")


class VerificarCodigoSenhaRequest(BaseModel):
    email: EmailStr = Field(example="joao@example.com")
    code: str = Field(min_length=6, max_length=6, description="Código de 6 dígitos recebido por email.")


class SenhaResetConfirmar(BaseModel):
    email: EmailStr = Field(example="joao@example.com")
    codigo: str = Field(min_length=6, max_length=6, description="Código de 6 dígitos recebido por email.")
    nova_senha: str = Field(min_length=8, max_length=128, example="NovaSenhaForte123")


class ResetPasswordRequest(BaseModel):
    email: EmailStr = Field(example="joao@example.com")
    code: str = Field(min_length=6, max_length=6, description="Código de 6 dígitos recebido por email.")
    newPassword: str = Field(min_length=8, max_length=128, example="NovaSenhaForte123")


class AlterarSenhaRequest(BaseModel):
    senha_atual: str = Field(min_length=8, max_length=128)
    nova_senha: str = Field(min_length=8, max_length=128)


class ExcluirContaRequest(BaseModel):
    # Opcional: contas criadas só via Google/Apple não têm senha (senha_hash nulo — ver
    # User.senha_hash). Para contas email/senha, o endpoint exige e valida este campo antes
    # de excluir, para que um token roubado sozinho não baste para apagar a conta.
    senha_atual: str | None = Field(default=None, min_length=8, max_length=128)


class FeedbackCreate(BaseModel):
    mensagem: str = Field(min_length=5, max_length=2000, example="Sugestão: adicionar filtro por local na tela de plantões.")


class LocalBase(BaseModel):
    nome_local: str = Field(min_length=2, max_length=120)
    data_inicio: Optional[date] = Field(default=None, description="Data de abertura do ciclo. Calculada automaticamente se não informada.")
    data_final: date
    dia_pagamento: Optional[int] = Field(default=None, ge=1, le=31)
    util_corrido: bool = Field(default=True, description="True = dia útil, False = dia corrido")
    cor: Optional[str] = Field(default=None, max_length=20, example="#4CAF50")
    endereco_local: Optional[str] = Field(default=None, max_length=255)
    uuid_cliente: Optional[str] = Field(
        default=None, max_length=36, description="Identificador gerado no dispositivo, usado para sincronização offline."
    )

    @field_validator("data_inicio", "data_final", mode="before")
    @classmethod
    def validar_campos_data(cls, value):
        if value is None:
            return value
        return validar_formato_data(value)

    @model_validator(mode="after")
    def validar_intervalo_datas(self):
        from calendar import monthrange
        if self.data_inicio is None and self.data_final is not None:
            # Calcula abertura como: mesmo dia do fechamento, no mês anterior + 1 dia
            # Ex.: fechamento dia 5 → abertura dia 6 do mês anterior
            month = self.data_final.month - 1 or 12
            year = self.data_final.year if self.data_final.month > 1 else self.data_final.year - 1
            day = min(self.data_final.day, monthrange(year, month)[1])
            self.data_inicio = date(year, month, day) + timedelta(days=1)
        if self.data_inicio is not None and self.data_final is not None:
            if self.data_inicio > self.data_final:
                raise ValueError("data_inicio deve ser menor ou igual a data_final")
        return self


class LocalCreate(LocalBase):
    pass


class LocalUpdate(BaseModel):
    nome_local: str | None = Field(default=None, min_length=2, max_length=120)
    data_inicio: date | None = None
    data_final: date | None = None
    dia_pagamento: Optional[int] = Field(default=None, ge=1, le=31)
    util_corrido: Optional[bool] = Field(default=None, description="True = dia útil, False = dia corrido")
    cor: Optional[str] = Field(default=None, max_length=20, example="#4CAF50")
    endereco_local: Optional[str] = Field(default=None, max_length=255)

    @field_validator("data_inicio", "data_final", mode="before")
    @classmethod
    def validar_campos_data(cls, value):
        return validar_formato_data(value)


class LocalCreateWithUsuario(LocalCreate):
    usuario_id: int


class LocalResponse(LocalBase):
    id: int
    usuario_id: int
    atualizado_em: datetime

    class Config:
        from_attributes = True


_RECORRENCIA_MAP = {
    "weekly": "Semanal",
    "biweekly": "Quinzenal",
    "fortnightly": "Quinzenal",
    "single": "Unico",
    "once": "Unico",
    "semanal": "Semanal",
    "quinzenal": "Quinzenal",
    "unico": "Unico",
}


def _normalizar_recorrencia(value: str | None) -> str | None:
    """Normaliza valores de recorrência para os rótulos internos aceitos pelo sistema."""
    if value is None:
        return None
    mapped = _RECORRENCIA_MAP.get(value.lower())
    if mapped is None:
        raise ValueError(
            f"Recorrência inválida: '{value}'. Use Semanal/weekly, Quinzenal/biweekly ou Unico/single."
        )
    return mapped


def _combinar_data_horarios(values: dict) -> dict:
    """Suporta formato alternativo com campos separados.

    Aceita qualquer combinação:
      - data_inicio/data_fim como datetime completo (formato novo, preferido)
      - data_inicio/data_fim como string só-data + horario_inicio/horario_fim como "HH:MM"
      - data (string YYYY-MM-DD) + horario_inicio/horario_fim como "HH:MM"
    """
    data_val = values.get("data")
    h_ini = values.get("horario_inicio")
    h_fim = values.get("horario_fim")
    data_inicio = values.get("data_inicio")
    data_fim = values.get("data_fim")

    def _ref_date_str(dt_val) -> str | None:
        if dt_val is not None:
            s = str(dt_val)[:10]
            return s if len(s) == 10 else None
        return None

    ref_date = _ref_date_str(data_val) or (
        _ref_date_str(data_inicio)
        if isinstance(data_inicio, str) and "T" not in data_inicio
        else None
    )

    if h_ini and isinstance(h_ini, str) and ref_date:
        t = h_ini.strip()[:5]
        if data_inicio is None or (isinstance(data_inicio, str) and "T" not in data_inicio):
            values["data_inicio"] = f"{ref_date}T{t}:00"

    if h_fim and isinstance(h_fim, str):
        t = h_fim.strip()[:5]
        fim_ref = ref_date or (
            _ref_date_str(values.get("data_inicio"))
            if isinstance(values.get("data_inicio"), str) and "T" in values.get("data_inicio", "")
            else None
        )
        if fim_ref and (data_fim is None or (isinstance(data_fim, str) and "T" not in data_fim)):
            values["data_fim"] = f"{fim_ref}T{t}:00"

    return values


class PlantaoBase(BaseModel):
    valor: Optional[Decimal] = Field(
        default=None,
        ge=0,
        example="180.00",
        description="Valor do plantão em reais. Deixe em branco para definir depois.",
    )
    data_inicio: datetime = Field(
        example="2026-04-10T08:00:00",
        description="Data e hora de início do plantão."
    )
    data_fim: datetime = Field(
        example="2026-04-10T16:00:00",
        description="Data e hora de fim do plantão."
    )
    recorrencia: Literal["Semanal", "Quinzenal", "Unico"] | None = Field(
        default=None,
        example="Semanal",
        description="Recorrência opcional do plantão. Aceita português ou inglês (weekly, biweekly, single)."
    )
    sobreaviso: bool = Field(default=False, example=False, description="Indica se o plantão é de sobreaviso.")
    descricao: Optional[str] = Field(default=None, max_length=2000, description="Descrição ou anotações livres sobre o plantão.")
    uuid_cliente: Optional[str] = Field(
        default=None, max_length=36, description="Identificador gerado no dispositivo, usado para sincronização offline."
    )

    @field_validator("data_inicio", "data_fim")
    @classmethod
    def recusar_fuso(cls, value: datetime) -> datetime:
        """Recusa datetime com fuso em data_inicio/data_fim.

        Estes campos são HORA DE PAREDE: "das 07:00 às 19:00 do dia 10/04".
        Não descrevem um instante no tempo, e por isso são gravados em colunas
        Date e Time separadas, com os componentes tomados como vieram
        (ver criar_plantao: data_inicio.date() e .time()).

        Aceitar um valor com fuso silenciosamente gravava o horário deslocado:
        um cliente que enviasse 10:00Z para um plantão das 07:00 tinha 10:00
        gravado. Misturar valores com e sem fuso também provocava
        `TypeError: can't compare offset-naive and offset-aware datetimes` em
        atualizar_plantao, que virava um 500 opaco.

        Existia um teste guardando exatamente isto — test_rejeita_data_com_
        horario_ou_fuso —, mas ele apontava para o campo `data`, que deixou de
        existir quando virou data_inicio/data_fim. O teste passou a falhar por
        outro motivo e a proteção sumiu sem ninguém notar.
        """
        if value.tzinfo is not None:
            raise ValueError(
                "data_inicio e data_fim não aceitam fuso horário: envie a hora local "
                "do plantão (ex.: 2026-04-10T07:00:00), sem sufixo Z nem offset."
            )
        return value

    @field_validator("recorrencia", mode="before")
    @classmethod
    def normalizar_recorrencia(cls, value):
        return _normalizar_recorrencia(value)


class PlantaoCreate(PlantaoBase):
    local_id: int = Field(example=1, description="ID do local ao qual o plantão pertence.")
    pagamento_plantao: Optional[date] = Field(default=None, description="Data de pagamento manual. Se não informada, calculada automaticamente pelo local.")
    notificacao_ativa: bool = Field(default=True, example=True)
    notificacao_antecedencia_minutos: int = Field(
        default=60,
        ge=0,
        le=2880,
        example=60,
        description="Minutos antes do início para enviar a notificação."
    )

    @model_validator(mode="before")
    @classmethod
    def aceitar_formato_alternativo(cls, values):
        if isinstance(values, dict):
            _combinar_data_horarios(values)
        return values

    @model_validator(mode="after")
    def validar_duracao(cls, values):
        inicio = values.data_inicio
        fim = values.data_fim

        if fim <= inicio:
            fim += timedelta(days=1)
            values.data_fim = fim

        if fim - inicio > timedelta(hours=48):
            raise ValueError("A duração do plantão não pode ser maior que 48 horas.")

        return values


class PlantaoCreateWithUsuario(BaseModel):
    usuario_id: int
    local_id: int
    valor: Decimal = Decimal("0")
    data_inicio: date
    data_fim: date
    horario_inicio: time
    horario_fim: time
    recorrencia: Optional[str] = None
    sobreaviso: bool = False
    descricao: Optional[str] = None
    notificacao_ativa: bool = True
    notificacao_antecedencia_minutos: int = 60
    executado: bool = False
    pagamento_plantao: Optional[date] = None
    transferido: bool = False
    uuid_cliente: Optional[str] = None


class PlantaoUpdate(BaseModel):
    valor: Decimal | None = Field(default=None, ge=0)
    data_inicio: datetime | None = None
    data_fim: datetime | None = None
    recorrencia: Literal["Semanal", "Quinzenal", "Unico"] | None = Field(default=None)
    sobreaviso: bool | None = None
    descricao: Optional[str] = Field(default=None, max_length=2000)
    local_id: int | None = None
    notificacao_ativa: bool | None = None
    notificacao_antecedencia_minutos: int | None = Field(default=None, ge=0, le=2880)
    pagamento_plantao: Optional[date] = Field(default=None, description="Sobrescreve a data de pagamento calculada automaticamente.")
    aplicar_em: Literal["somente_este", "este_e_futuros"] = Field(
        default="somente_este",
        description="Define se a alteração será aplicada somente neste plantão ou no plantão selecionado e nos futuros recorrentes.",
    )

    @model_validator(mode="before")
    @classmethod
    def aceitar_formato_alternativo(cls, values):
        if isinstance(values, dict):
            _combinar_data_horarios(values)
        return values

    @field_validator("recorrencia", mode="before")
    @classmethod
    def normalizar_recorrencia(cls, value):
        return _normalizar_recorrencia(value)


class PlantaoResponse(BaseModel):
    id: int
    usuario_id: int
    local_id: int
    local_nome: str = ""
    local_cor: Optional[str] = None
    valor: Decimal = Decimal("0")
    data_inicio: date
    data_fim: date
    horario_inicio: time
    horario_fim: time
    recorrencia: Optional[str] = None
    sobreaviso: bool
    notificacao_ativa: bool
    notificacao_antecedencia_minutos: int
    notificado: bool
    executado: bool
    pagamento_plantao: Optional[date] = None
    transferido: bool = False
    recebido_via_transferencia: bool = False
    descricao: Optional[str] = None
    tipo_troca: Literal["transferido", "recebido", "trocado"] | None = None
    troca: "PlantaoTrocaResponse | None" = None
    bonificacoes: list["BonificacaoResponse"] = []
    uuid_cliente: Optional[str] = None
    atualizado_em: datetime

    class Config:
        from_attributes = True

    @model_validator(mode="before")
    @classmethod
    def from_orm_model(cls, obj):
        if hasattr(obj, "horario_inicio"):
            bons = getattr(obj, "bonificacoes", None) or []
            return {
                "id": obj.id,
                "usuario_id": obj.usuario_id,
                "local_id": obj.local_id,
                # Resolvido aqui porque um plantão recebido via troca/transferência aponta
                # para um local que pertence a quem cedeu, não a quem está vendo a resposta —
                # esse local nunca aparece no GET /locais de quem recebeu.
                "local_nome": obj.local.nome_local if getattr(obj, "local", None) else "",
                "local_cor": obj.local.cor if getattr(obj, "local", None) else None,
                "valor": obj.valor,
                "data_inicio": obj.data_inicio,
                "data_fim": obj.data_fim,
                "horario_inicio": obj.horario_inicio,
                "horario_fim": obj.horario_fim,
                "recorrencia": obj.recorrencia,
                "sobreaviso": obj.sobreaviso,
                "notificacao_ativa": obj.notificacao_ativa,
                "notificacao_antecedencia_minutos": obj.notificacao_antecedencia_minutos,
                "notificado": obj.notificado,
                "executado": obj.executado,
                "pagamento_plantao": obj.pagamento_plantao,
                "transferido": obj.transferido,
                "recebido_via_transferencia": getattr(obj, "recebido_via_transferencia", False),
                "descricao": obj.descricao,
                "tipo_troca": None,
                "troca": None,
                "uuid_cliente": getattr(obj, "uuid_cliente", None),
                "atualizado_em": obj.atualizado_em,
                "bonificacoes": [
                    {
                        "id": b.id,
                        "plantao_id": b.plantao_id,
                        "local_id": b.local_id,
                        "valor": b.valor,
                        "descricao": b.descricao,
                        "uuid_cliente": getattr(b, "uuid_cliente", None),
                        "atualizado_em": getattr(b, "atualizado_em", None),
                    }
                    for b in bons
                ],
            }
        return obj


class PlantaoTrocaResponse(BaseModel):
    id: int
    papel: Literal["transferido", "recebido"]
    contraparte_nome: str
    contraparte_telefone: str
    contraparte_observacao: Optional[str] = None
    contraparte_usuario_id: Optional[int] = Field(
        default=None, description="usuario_id da outra pessoa da troca quando for conta real; null se contraparte manual (texto livre)."
    )
    plantao_relacionado_id: Optional[int] = None


class PessoaTrocaCreate(BaseModel):
    nome: str = Field(min_length=1, max_length=120)
    telefone: str = Field(min_length=1, max_length=30)
    observacao: Optional[str] = Field(default=None, max_length=2000)

    @field_validator("nome", "telefone")
    @classmethod
    def nao_permitir_vazio(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Campo obrigatório.")
        return value


class PlantaoRecebidoTrocaCreate(BaseModel):
    local_id: int
    data_inicio: date
    horario_inicio: time
    data_fim: date
    horario_fim: time
    valor: Decimal = Field(ge=0)
    sobreaviso: bool = False
    descricao: Optional[str] = Field(default=None, max_length=2000)
    recebido_de: PessoaTrocaCreate
    uuid_cliente: Optional[str] = Field(
        default=None, max_length=36, description="Identificador estável gerado no dispositivo para o plantão recebido."
    )

    @model_validator(mode="after")
    def validar_intervalo(self):
        inicio = datetime.combine(self.data_inicio, self.horario_inicio)
        fim = datetime.combine(self.data_fim, self.horario_fim)
        if fim <= inicio:
            raise ValueError("data_fim e horario_fim devem ser posteriores ao início do plantão.")
        if fim - inicio > timedelta(hours=48):
            raise ValueError("A duração do plantão não pode ser maior que 48 horas.")
        return self


class TrocaOperacaoCreate(BaseModel):
    plantao_transferido_id: int
    transferido_para: PessoaTrocaCreate
    plantao_recebido: Optional[PlantaoRecebidoTrocaCreate] = None
    uuid_cliente: Optional[str] = Field(
        default=None, max_length=36, description="Identificador estável gerado no dispositivo para esta troca."
    )


class TrocaOperacaoData(BaseModel):
    troca_id: int
    plantao_transferido: PlantaoResponse
    plantao_recebido: Optional[PlantaoResponse] = None


class SolicitacaoTrocaPlantaoRecebido(BaseModel):
    local_id: int
    data_inicio: date
    horario_inicio: time
    data_fim: date
    horario_fim: time
    valor: Decimal = Field(ge=0)
    sobreaviso: bool = False

    @field_validator("data_inicio", "data_fim", mode="before")
    @classmethod
    def validar_campos_data(cls, value):
        return validar_formato_data(value)

    @model_validator(mode="after")
    def validar_intervalo(self):
        inicio = datetime.combine(self.data_inicio, self.horario_inicio)
        fim = datetime.combine(self.data_fim, self.horario_fim)
        if fim <= inicio:
            raise ValueError("data_fim e horario_fim devem ser posteriores ao início do plantão.")
        if fim - inicio > timedelta(hours=48):
            raise ValueError("A duração do plantão não pode ser maior que 48 horas.")
        return self


class SolicitacaoTrocaCreate(BaseModel):
    destinatario_usuario_id: int
    tipo: Literal["transferir", "receber", "trocar"]
    plantao_transferido_id: Optional[int] = None
    plantao_recebido: Optional[SolicitacaoTrocaPlantaoRecebido] = None
    plantao_alvo_id: Optional[int] = Field(
        default=None,
        description="Plantão já existente do destinatário a ser recebido (ownership transfer). "
        "Alternativa a plantao_recebido — informe só um dos dois.",
    )

    @model_validator(mode="after")
    def validar_campos_por_tipo(self):
        recebendo = self.plantao_recebido is not None or self.plantao_alvo_id is not None
        recebendo_ambiguo = self.plantao_recebido is not None and self.plantao_alvo_id is not None

        if self.tipo == "transferir":
            if self.plantao_transferido_id is None or recebendo:
                raise ValueError("Para tipo='transferir', informe apenas plantao_transferido_id.")
        elif self.tipo == "receber":
            if not recebendo or recebendo_ambiguo or self.plantao_transferido_id is not None:
                raise ValueError("Para tipo='receber', informe plantao_recebido OU plantao_alvo_id (não ambos).")
        elif self.tipo == "trocar":
            if self.plantao_transferido_id is None or not recebendo or recebendo_ambiguo:
                raise ValueError(
                    "Para tipo='trocar', informe plantao_transferido_id e (plantao_recebido OU plantao_alvo_id)."
                )
        return self


class SolicitacaoTrocaResponse(BaseModel):
    id: int
    remetente_usuario_id: int
    destinatario_usuario_id: int
    tipo: Literal["transferir", "receber", "trocar"]
    status: str
    criado_em: datetime
    respondido_em: Optional[datetime] = None

    class Config:
        from_attributes = True


class PessoaTrocaResumo(BaseModel):
    id: int
    nome: str

    class Config:
        from_attributes = True


class PlantaoTrocaResumo(BaseModel):
    id: Optional[int] = Field(default=None, description="Null quando o plantão ainda não existe (proposta de plantao_recebido pendente).")
    local_nome: str
    data_inicio: date
    horario_inicio: time
    data_fim: date
    horario_fim: time
    valor: Decimal


class SolicitacaoTrocaListItem(BaseModel):
    id: int
    tipo: Literal["transferir", "receber", "trocar"]
    status: str
    papel: Literal["remetente", "destinatario"]
    remetente: PessoaTrocaResumo
    destinatario: PessoaTrocaResumo
    plantao_transferido: Optional[PlantaoTrocaResumo] = None
    plantao_recebido: Optional[PlantaoTrocaResumo] = None
    criado_em: datetime


class TransferenciaCreate(BaseModel):
    destinatario_nome: str = Field(min_length=2, max_length=120, example="Maria Santos")
    destinatario_telefone: str = Field(
        min_length=8,
        max_length=30,
        example="+5511999999999",
        description="Telefone do destinatário com DDI e DDD. Ex: +5511999999999",
    )
    canal: Literal["sms", "whatsapp"] = Field(default="whatsapp", description="Canal de envio da notificação.")


class TransferenciaAceitar(BaseModel):
    local_id: int = Field(description="ID do local na conta do destinatário ao qual o plantão será vinculado.")


class TransferenciaResponse(BaseModel):
    id: int
    plantao_id: int
    remetente_id: int
    destinatario_usuario_id: Optional[int] = None
    destinatario_nome: str
    destinatario_telefone: str
    status: str
    canal: str
    sms_enviado: bool
    criado_em: datetime

    class Config:
        from_attributes = True


class FinanceiroCreate(BaseModel):
    local_id: int
    valor: Decimal = Field(gt=0, description="Valor total do pagamento do mês.")
    data: date = Field(description="Data de pagamento referente ao local.")
    em_atraso: bool = False


class FinanceiroEmAtrasoUpdate(BaseModel):
    em_atraso: bool


class FinanceiroRecordResponse(BaseModel):
    id: int
    local_id: int
    valor: Decimal
    data: date
    em_atraso: bool

    class Config:
        from_attributes = True


class BonificacaoCreate(BaseModel):
    plantao_id: int = Field(description="ID do plantão ao qual a bonificação pertence.")
    local_id: Optional[int] = Field(default=None, description="ID do local (informativo, armazenado para facilitar listagem).")
    valor: Decimal = Field(gt=0, description="Valor da bonificação em reais.")
    descricao: Optional[str] = Field(default=None, max_length=500, description="Motivo ou descrição da bonificação.")
    uuid_cliente: Optional[str] = Field(
        default=None, max_length=36, description="Identificador estável gerado no dispositivo."
    )


class BonificacaoUpdate(BaseModel):
    valor: Optional[Decimal] = Field(default=None, gt=0, description="Novo valor da bonificação.")
    descricao: Optional[str] = Field(default=None, max_length=500, description="Nova descrição da bonificação.")


class BonificacaoResponse(BaseModel):
    id: int
    plantao_id: int
    local_id: Optional[int] = None
    valor: Decimal
    descricao: Optional[str] = None
    uuid_cliente: Optional[str] = None
    atualizado_em: Optional[datetime] = None

    class Config:
        from_attributes = True


class TrocaCreate(BaseModel):
    usuario_id: Optional[int] = Field(default=None, description="ID do usuário dono da troca.")
    local_id: Optional[int] = Field(default=None, description="ID do local cadastrado (opcional).")
    plantao_id_transferido: Optional[int] = Field(default=None, description="ID do plantão que está sendo transferido.")
    plantao_id_recebido: Optional[int] = Field(default=None, description="ID do plantão que está sendo recebido.")
    nome_transferir: Optional[str] = Field(default=None, max_length=120)
    nome_receber: Optional[str] = Field(default=None, max_length=120)
    telefone_transferir: Optional[str] = Field(default=None, max_length=30)
    telefone_receber: Optional[str] = Field(default=None, max_length=30)
    local: Optional[str] = Field(default=None, max_length=120, description="Nome do local (texto livre).")
    data_inicio: Optional[date] = Field(default=None)
    data_fim: Optional[date] = Field(default=None)
    horario_inicio: Optional[time] = Field(default=None)
    horario_fim: Optional[time] = Field(default=None)


class TrocaResponse(BaseModel):
    id: int
    usuario_id: Optional[int] = None
    local_id: Optional[int] = None
    plantao_id_transferido: Optional[int] = None
    plantao_id_recebido: Optional[int] = None
    nome_transferir: Optional[str] = None
    nome_receber: Optional[str] = None
    telefone_transferir: Optional[str] = None
    telefone_receber: Optional[str] = None
    local: Optional[str] = None
    data_inicio: Optional[date] = None
    data_fim: Optional[date] = None
    horario_inicio: Optional[time] = None
    horario_fim: Optional[time] = None
    uuid_cliente: Optional[str] = None
    atualizado_em: Optional[datetime] = None

    class Config:
        from_attributes = True


class PlantaoExecutadoUpdate(BaseModel):
    executado: bool = Field(
        ...,
        description="Indica se o plantão foi executado.",
        example=True,
    )


class FinanceiroData(BaseModel):
    total: Decimal
    quantidade_plantoes: int
    total_bonificacoes: Decimal = Decimal("0")
    total_com_bonificacoes: Decimal = Decimal("0")
    # Parcela de `total` que veio de plantões recebidos de outra pessoa por
    # transferência. É um RECORTE, não um valor a somar: `total` já os inclui.
    # Serve para o relatório mostrar "deste valor, X veio de plantões recebidos".
    total_recebidos: Decimal = Decimal("0")
    quantidade_recebidos: int = 0


class FinanceiroResponse(APIResponse[FinanceiroData]):
    pass


class GraficoMensalItem(BaseModel):
    mes: int
    total: Decimal
    quantidade_plantoes: int
    total_bonificacoes: Decimal = Decimal("0")
    total_com_bonificacoes: Decimal = Decimal("0")


class UpgradeWebhookRequest(BaseModel):
    usuario_id: int


class UsuarioResumo(BaseModel):
    id: int
    nome: str
    email: EmailStr
    avatar: Optional[str] = None

    class Config:
        from_attributes = True


class AmizadeCreate(BaseModel):
    destinatario_usuario_id: int


class AmizadeResponse(BaseModel):
    id: int
    solicitante_usuario_id: int
    destinatario_usuario_id: int
    status: str
    criado_em: datetime
    atualizado_em: datetime

    class Config:
        from_attributes = True


class AmizadeAmigoResumo(UsuarioResumo):
    """Dados do outro usuário numa amizade ou solicitação.

    `telefone` só é preenchido quando a amizade está ACEITA — ver listar_amizades.
    A versão anterior o incluía também em solicitações meramente pendentes, o que
    abria uma varredura da base inteira: POST /api/amizades aceita qualquer
    destinatario_usuario_id, então bastava criar uma solicitação para cada id e
    ler a listagem para colher nome, email, telefone e avatar de todos os
    usuários (ver auditoria C-09)."""
    telefone: Optional[str] = None


class AmizadeListItem(BaseModel):
    id: int
    status: str
    papel: Literal["solicitante", "destinatario"]
    amigo: AmizadeAmigoResumo
    configuracao_id: Optional[int] = Field(
        default=None, description="ID da linha de amigo_configuracoes do usuário autenticado (para PATCH /api/amigo-configuracoes/:id). Presente apenas quando status='aceito'."
    )
    modo_visibilidade: Optional[Literal["tudo", "personalizado"]] = None
    mostrar_valores: Optional[bool] = None
    cor: Optional[str] = None
    locais_permitidos: list[int] = Field(default_factory=list)
    criado_em: datetime


class AmigoConfiguracaoUpdate(BaseModel):
    modo_visibilidade: Optional[Literal["tudo", "personalizado"]] = None
    mostrar_valores: Optional[bool] = None
    locais_permitidos_ids: Optional[list[int]] = None
    cor: Optional[str] = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$", example="#0D9488")


class AmigoConfiguracaoResponse(BaseModel):
    id: int
    usuario_id: int
    amigo_usuario_id: int
    modo_visibilidade: str
    mostrar_valores: bool
    cor: str
    locais_permitidos: list[int] = Field(default_factory=list)
    atualizado_em: Optional[datetime] = None


class AmigoPlantaoItem(BaseModel):
    amigo_usuario_id: int
    amigo_nome: str
    amigo_avatar: Optional[str] = None
    cor: str = Field(description="Cor escolhida pelo usuário autenticado para pintar este amigo no próprio calendário.")
    local_id: int
    local_nome: str
    local_endereco: Optional[str] = None
    local_dia_pagamento: Optional[int] = None
    plantao: PlantaoResponse


# ---------------------------------------------------------------------------
# Sincronização offline (GET /api/sync/pull, POST /api/sync/push)
# ---------------------------------------------------------------------------
# Os itens de push carregam o estado local COMPLETO do registro (não um diff/patch) — é
# assim que o app deve tratar cada linha "suja" no banco local: reenviar tudo, não só os
# campos que mudaram. O servidor decide criar vs. atualizar comparando uuid_cliente/id com
# o que já existe, e usa "last-write-wins" comparando atualizado_em contra o valor salvo no
# servidor (ver SyncService para a regra completa).


class SyncLocalItem(BaseModel):
    uuid_cliente: str = Field(min_length=1, max_length=36, description="Identificador estável gerado no dispositivo.")
    id: Optional[int] = Field(default=None, description="ID no servidor, se este local já foi sincronizado antes.")
    nome_local: str = Field(min_length=2, max_length=120)
    data_inicio: Optional[date] = None
    data_final: date
    dia_pagamento: Optional[int] = Field(default=None, ge=1, le=31)
    util_corrido: bool = True
    cor: Optional[str] = Field(default=None, max_length=20)
    endereco_local: Optional[str] = Field(default=None, max_length=255)
    atualizado_em: datetime = Field(description="Hora da última alteração feita no dispositivo (relógio local).")
    deletado: bool = Field(default=False, description="True se o registro foi excluído localmente.")


class SyncPlantaoItem(BaseModel):
    uuid_cliente: str = Field(min_length=1, max_length=36, description="Identificador estável gerado no dispositivo.")
    id: Optional[int] = Field(default=None, description="ID no servidor, se este plantão já foi sincronizado antes.")
    local_id: Optional[int] = Field(default=None, description="ID do local no servidor. Informe local_id OU local_uuid_cliente.")
    local_uuid_cliente: Optional[str] = Field(
        default=None, max_length=36, description="uuid_cliente de um local enviado no mesmo push (ainda sem id de servidor)."
    )
    valor: Optional[Decimal] = Field(default=None, ge=0)
    data_inicio: datetime
    data_fim: datetime
    recorrencia: Literal["Semanal", "Quinzenal", "Unico"] | None = None
    sobreaviso: bool = False
    descricao: Optional[str] = Field(default=None, max_length=2000)
    notificacao_ativa: bool = True
    notificacao_antecedencia_minutos: int = Field(default=60, ge=0, le=2880)
    pagamento_plantao: Optional[date] = None
    atualizado_em: datetime = Field(description="Hora da última alteração feita no dispositivo (relógio local).")
    deletado: bool = Field(default=False, description="True se o registro foi excluído localmente.")

    @field_validator("recorrencia", mode="before")
    @classmethod
    def normalizar_recorrencia(cls, value):
        return _normalizar_recorrencia(value)

    @model_validator(mode="after")
    def validar_referencia_local(self):
        if not self.deletado and self.local_id is None and self.local_uuid_cliente is None:
            raise ValueError("Informe local_id ou local_uuid_cliente.")
        return self


class SyncPlantaoRecebidoTrocaItem(BaseModel):
    """Plantão recebido criado como efeito colateral de uma troca manual — ver
    PlantaoRecebidoTrocaCreate (contrato equivalente do endpoint /api/trocas). Carrega seu
    próprio uuid_cliente porque é uma linha de `plantoes` nova, independente da troca."""
    uuid_cliente: str = Field(min_length=1, max_length=36)
    local_id: Optional[int] = Field(default=None, description="Informe local_id ou local_uuid_cliente.")
    local_uuid_cliente: Optional[str] = Field(default=None, max_length=36)
    data_inicio: date
    horario_inicio: time
    data_fim: date
    horario_fim: time
    valor: Decimal = Field(ge=0)
    sobreaviso: bool = False
    descricao: Optional[str] = Field(default=None, max_length=2000)
    recebido_de: PessoaTrocaCreate

    @model_validator(mode="after")
    def validar_local(self):
        if self.local_id is None and self.local_uuid_cliente is None:
            raise ValueError("Informe local_id ou local_uuid_cliente.")
        return self


class SyncTrocaItem(BaseModel):
    """Troca manual (transferir/receber por contato avulso, tabela `trocas`) — não cobre
    solicitações de troca entre amigos (`solicitacoes_troca`), que continuam exigindo
    conexão. Não existe rota de edição: um reenvio do mesmo uuid_cliente é tratado como
    idempotente (o servidor não duplica nem reexecuta o efeito colateral de transferência),
    não como uma atualização de campos."""
    uuid_cliente: str = Field(min_length=1, max_length=36, description="Identificador estável gerado no dispositivo.")
    id: Optional[int] = Field(default=None, description="ID no servidor, se esta troca já foi sincronizada antes.")
    plantao_transferido_id: Optional[int] = Field(default=None, description="Informe plantao_transferido_id ou plantao_transferido_uuid_cliente.")
    plantao_transferido_uuid_cliente: Optional[str] = Field(
        default=None, max_length=36, description="uuid_cliente de um plantão enviado no mesmo push (ainda sem id de servidor)."
    )
    transferido_para: PessoaTrocaCreate
    plantao_recebido: Optional[SyncPlantaoRecebidoTrocaItem] = None
    atualizado_em: datetime = Field(description="Hora da criação/exclusão feita no dispositivo (relógio local).")
    deletado: bool = Field(default=False, description="True se o registro foi excluído localmente.")

    @model_validator(mode="after")
    def validar_referencia_plantao(self):
        if not self.deletado and self.plantao_transferido_id is None and self.plantao_transferido_uuid_cliente is None:
            raise ValueError("Informe plantao_transferido_id ou plantao_transferido_uuid_cliente.")
        return self


class SyncBonificacaoItem(BaseModel):
    uuid_cliente: str = Field(min_length=1, max_length=36, description="Identificador estável gerado no dispositivo.")
    id: Optional[int] = Field(default=None, description="ID no servidor, se esta bonificação já foi sincronizada antes.")
    plantao_id: Optional[int] = Field(default=None, description="Informe plantao_id ou plantao_uuid_cliente.")
    plantao_uuid_cliente: Optional[str] = Field(
        default=None, max_length=36, description="uuid_cliente de um plantão enviado no mesmo push (ainda sem id de servidor)."
    )
    local_id: Optional[int] = None
    valor: Decimal = Field(gt=0)
    descricao: Optional[str] = Field(default=None, max_length=500)
    atualizado_em: datetime = Field(description="Hora da última alteração feita no dispositivo (relógio local).")
    deletado: bool = Field(default=False, description="True se o registro foi excluído localmente.")

    @model_validator(mode="after")
    def validar_referencia_plantao(self):
        if not self.deletado and self.plantao_id is None and self.plantao_uuid_cliente is None:
            raise ValueError("Informe plantao_id ou plantao_uuid_cliente.")
        return self


class SyncPushRequest(BaseModel):
    locais: list[SyncLocalItem] = Field(default_factory=list, max_length=500)
    plantoes: list[SyncPlantaoItem] = Field(default_factory=list, max_length=500)
    trocas: list[SyncTrocaItem] = Field(default_factory=list, max_length=500)
    bonificacoes: list[SyncBonificacaoItem] = Field(default_factory=list, max_length=500)


class SyncItemResultado(BaseModel):
    uuid_cliente: str
    id: int


class SyncConflito(BaseModel):
    entidade: Literal["local", "plantao", "troca", "bonificacao"]
    uuid_cliente: str
    id: Optional[int] = None
    motivo: str
    servidor_atualizado_em: datetime


class SyncErro(BaseModel):
    entidade: Literal["local", "plantao", "troca", "bonificacao"]
    uuid_cliente: str
    detalhe: str
    status_code: int


class SyncPushResponse(BaseModel):
    locais_criados: list[SyncItemResultado] = Field(default_factory=list)
    locais_atualizados: list[SyncItemResultado] = Field(default_factory=list)
    locais_deletados: list[SyncItemResultado] = Field(default_factory=list)
    plantoes_criados: list[SyncItemResultado] = Field(default_factory=list)
    plantoes_atualizados: list[SyncItemResultado] = Field(default_factory=list)
    plantoes_deletados: list[SyncItemResultado] = Field(default_factory=list)
    trocas_criadas: list[SyncItemResultado] = Field(
        default_factory=list,
        description="Inclui reenvios idempotentes do mesmo uuid_cliente — não há trocas_atualizadas (sem rota de edição).",
    )
    trocas_deletadas: list[SyncItemResultado] = Field(default_factory=list)
    bonificacoes_criadas: list[SyncItemResultado] = Field(default_factory=list)
    bonificacoes_atualizadas: list[SyncItemResultado] = Field(default_factory=list)
    bonificacoes_deletadas: list[SyncItemResultado] = Field(default_factory=list)
    conflitos: list[SyncConflito] = Field(default_factory=list)
    erros: list[SyncErro] = Field(default_factory=list)
    servidor_timestamp: datetime = Field(
        description="Timestamp do servidor no momento da resposta — use como parâmetro `since` na próxima chamada."
    )


class SyncExclusaoItem(BaseModel):
    entidade: str
    id: int
    uuid_cliente: Optional[str] = None
    deletado_em: datetime


class SyncPullResponse(BaseModel):
    servidor_timestamp: datetime = Field(
        description="Timestamp do servidor no momento da resposta — use como parâmetro `since` na próxima chamada."
    )
    locais: list[LocalResponse] = Field(default_factory=list)
    plantoes: list[PlantaoResponse] = Field(default_factory=list)
    amizades: list[AmizadeListItem] = Field(default_factory=list)
    amigo_configuracoes: list[AmigoConfiguracaoResponse] = Field(default_factory=list)
    solicitacoes_troca: list[SolicitacaoTrocaListItem] = Field(default_factory=list)
    trocas: list[TrocaResponse] = Field(default_factory=list)
    bonificacoes: list[BonificacaoResponse] = Field(
        default_factory=list,
        description="Entrada própria (independente do plantão pai) filtrada pelo atualizado_em da própria bonificação.",
    )
    exclusoes: list[SyncExclusaoItem] = Field(
        default_factory=list,
        description="Registros excluídos desde `since` — o app deve remover essas linhas do banco local.",
    )
