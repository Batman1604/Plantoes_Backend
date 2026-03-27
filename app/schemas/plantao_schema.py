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


class PlantaoBase(BaseModel):
    titulo: str = Field(min_length=2, max_length=120)
    local: str = Field(min_length=2, max_length=120)
    valor: Decimal = Field(gt=0)
    data_inicio: date
    data_fim: date
    horario_inicio: time
    horario_fim: time
    recorrencia: Optional[str] = Field(default=None, max_length=50)
    dia_pagamento: Optional[int] = Field(default=None, ge=1, le=31)


class PlantaoCreate(PlantaoBase):
    usuario_id: int
    notificacao_ativa: bool = True
    notificacao_antecedencia_minutos: int = Field(default=60, ge=0, le=43200)


class PlantaoResponse(PlantaoBase):
    id: int
    usuario_id: int
    notificacao_ativa: bool
    notificacao_antecedencia_minutos: int

    class Config:
        from_attributes = True
