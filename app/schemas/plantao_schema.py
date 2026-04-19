from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Generic, Literal, Optional, TypeVar

from pydantic import BaseModel, EmailStr, Field, model_validator
from pydantic.generics import GenericModel


DataT = TypeVar("DataT")


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

    class Config:
        from_attributes = True


class AuthResponse(BaseModel):
    autenticado: bool
    access_token: str
    token_type: str = "bearer"
    usuario: UserResponse


class AuthStatusResponse(BaseModel):
    autenticado: bool
    usuario: Optional[UserResponse] = None


class LocalBase(BaseModel):
    nome_local: str = Field(min_length=2, max_length=120)
    data_inicio: date
    data_final: date
    dia_pagamento: Optional[int] = Field(default=None, ge=1, le=31)
    exucatado: bool = Field(default=False)


class LocalCreate(LocalBase):
    pass


class LocalCreateWithUsuario(LocalCreate):
    usuario_id: int


class LocalResponse(LocalBase):
    id: int
    usuario_id: int

    class Config:
        from_attributes = True


class PlantaoBase(BaseModel):
    valor: Decimal = Field(
        gt=0,
        example="180.00",
        description="Valor do plantão em reais. Deve ser maior que zero."
    )
    data: date = Field(
        example="2026-04-10",
        description="Data do plantão."
    )
    horario_inicio: time = Field(
        example="08:00:00",
        description="Hora de início do plantão."
    )
    horario_fim: time = Field(
        example="16:00:00",
        description="Hora de fim do plantão."
    )
    recorrencia: Optional[str] = Field(
        default=None,
        max_length=50,
        example="Semanal",
        description="Recorrência opcional do plantão."
    )


class PlantaoCreate(PlantaoBase):
    local_id: int = Field(example=1, description="ID do local ao qual o plantão pertence.")
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

    @model_validator(mode="after")
    def validar_duracao(cls, values):
        inicio = datetime.combine(values.data, values.horario_inicio)
        fim = datetime.combine(values.data, values.horario_fim)

        if fim <= inicio:
            fim += timedelta(days=1)

        max_duration = timedelta(hours=48)
        if fim - inicio > max_duration:
            raise ValueError("A duração do plantão não pode ser maior que 48 horas.")

        return values


class PlantaoCreateWithUsuario(PlantaoCreate):
    usuario_id: int
    executado: bool = False
    pagamento_plantao: Optional[date] = None


class PlantaoResponse(PlantaoBase):
    id: int
    usuario_id: int
    local_id: int
    notificacao_ativa: bool
    notificacao_antecedencia_minutos: int
    notificado: bool
    executado: bool
    pagamento_plantao: Optional[date] = None

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


class FinanceiroResponse(APIResponse[FinanceiroData]):
    pass
