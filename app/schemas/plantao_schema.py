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

    class Config:
        from_attributes = True


class PerfilUpdate(BaseModel):
    avatar: Optional[str] = Field(default=None, max_length=255)
    profissao: Optional[str] = Field(default=None, max_length=255)


class AuthResponse(BaseModel):
    autenticado: bool
    access_token: str
    token_type: str = "bearer"
    usuario: UserResponse


class AuthStatusResponse(BaseModel):
    autenticado: bool
    usuario: Optional[UserResponse] = None


class VerificarTelefoneRequest(BaseModel):
    codigo: str = Field(min_length=6, max_length=6, description="Código de 6 dígitos recebido via WhatsApp.")


class SenhaResetRequest(BaseModel):
    email: EmailStr = Field(example="joao@example.com")


class SenhaResetConfirmar(BaseModel):
    token: str = Field(min_length=1)
    nova_senha: str = Field(min_length=8, max_length=128, example="NovaSenhaForte123")


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=1)
    newPassword: str = Field(min_length=8, max_length=128, example="NovaSenhaForte123")


class AlterarSenhaRequest(BaseModel):
    senha_atual: str = Field(min_length=8, max_length=128)
    nova_senha: str = Field(min_length=8, max_length=128)


class DeviceTokenUpdate(BaseModel):
    device_token: str = Field(min_length=1, max_length=255)


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
    acao_conflito: Literal["manter", "ajustar", "cancelar"] | None = Field(
        default=None,
        description="Ação a tomar quando houver conflito de horário: manter (não fazer nada), ajustar (ajustar horários) ou cancelar (cancelar plantão)."
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
    bonificacoes: list["BonificacaoResponse"] = []

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
                "bonificacoes": [
                    {"id": b.id, "plantao_id": b.plantao_id, "local_id": b.local_id, "valor": b.valor, "descricao": b.descricao}
                    for b in bons
                ],
            }
        return obj


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


class PagamentoStatusUpdate(BaseModel):
    mes: int = Field(ge=1, le=12)
    ano: int = Field(ge=2000)
    em_atraso: bool


class PagamentoStatusResponse(BaseModel):
    em_atraso: bool

    class Config:
        from_attributes = True


class BonificacaoCreate(BaseModel):
    plantao_id: int = Field(description="ID do plantão ao qual a bonificação pertence.")
    local_id: Optional[int] = Field(default=None, description="ID do local (informativo, armazenado para facilitar listagem).")
    valor: Decimal = Field(gt=0, description="Valor da bonificação em reais.")
    descricao: Optional[str] = Field(default=None, max_length=500, description="Motivo ou descrição da bonificação.")


class BonificacaoUpdate(BaseModel):
    valor: Optional[Decimal] = Field(default=None, gt=0, description="Novo valor da bonificação.")
    descricao: Optional[str] = Field(default=None, max_length=500, description="Nova descrição da bonificação.")


class BonificacaoResponse(BaseModel):
    id: int
    plantao_id: int
    local_id: Optional[int] = None
    valor: Decimal
    descricao: Optional[str] = None

    class Config:
        from_attributes = True


class TrocaCreate(BaseModel):
    local_id: Optional[int] = Field(default=None, description="ID do local cadastrado (opcional).")
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
    local_id: Optional[int] = None
    nome_transferir: Optional[str] = None
    nome_receber: Optional[str] = None
    telefone_transferir: Optional[str] = None
    telefone_receber: Optional[str] = None
    local: Optional[str] = None
    data_inicio: Optional[date] = None
    data_fim: Optional[date] = None
    horario_inicio: Optional[time] = None
    horario_fim: Optional[time] = None

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
