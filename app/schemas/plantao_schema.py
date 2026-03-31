from datetime import date, time
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, EmailStr, Field


class UserBase(BaseModel):
    nome: str = Field(min_length=2, max_length=120)
    email: EmailStr


class UserCreate(UserBase):
    telefone: Optional[str] = Field(default=None, max_length=30)
    senha: str = Field(min_length=8, max_length=128)


class UserLogin(BaseModel):
    email: EmailStr
    senha: str = Field(min_length=8, max_length=128)


class UserResponse(UserBase):
    id: int
    telefone: Optional[str] = None

    class Config:
        from_attributes = True


class AuthResponse(BaseModel):
    autenticado: bool
    usuario: Optional[UserResponse] = None


class LocalBase(BaseModel):
    nome_local: str = Field(min_length=2, max_length=120)
    data_inicio: date
    data_final: date
    dia_pagamento: Optional[int] = Field(default=None, ge=1, le=31)


class LocalCreate(LocalBase):
    usuario_id: int


class LocalResponse(LocalBase):
    id: int
    usuario_id: int

    class Config:
        from_attributes = True


class PlantaoBase(BaseModel):
    titulo: str = Field(min_length=2, max_length=120)
    valor: Decimal = Field(gt=0)
    horario_inicio: time
    horario_fim: time
    recorrencia: Optional[str] = Field(default=None, max_length=50)


class PlantaoCreate(PlantaoBase):
    local_id: int
    notificacao_ativa: bool = True
    notificacao_antecedencia_minutos: int = Field(default=60, ge=0, le=43200)


class PlantaoResponse(PlantaoBase):
    id: int
    local_id: int
    notificacao_ativa: bool
    notificacao_antecedencia_minutos: int

    class Config:
        from_attributes = True
