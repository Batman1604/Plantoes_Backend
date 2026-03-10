from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Time,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class User(Base):
    __tablename__ = "usuarios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    nome: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    telefone: Mapped[str] = mapped_column(String(30), nullable=False)
    criado_em: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    plantoes: Mapped[list["Plantao"]] = relationship(back_populates="usuario", cascade="all, delete-orphan")


class Plantao(Base):
    __tablename__ = "plantoes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False)
    titulo: Mapped[str] = mapped_column(String(120), nullable=False)
    local: Mapped[str] = mapped_column(String(120), nullable=False)
    valor: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    data_inicio: Mapped[str] = mapped_column(Date, nullable=False)
    data_fim: Mapped[str] = mapped_column(Date, nullable=False)
    horario_inicio: Mapped[str] = mapped_column(Time, nullable=False)
    horario_fim: Mapped[str] = mapped_column(Time, nullable=False)
    recorrencia: Mapped[str | None] = mapped_column(String(50), nullable=True)
    dia_pagamento: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notificacao_ativa: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notificacao_antecedencia_minutos: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    criado_em: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    usuario: Mapped[User] = relationship(back_populates="plantoes")
