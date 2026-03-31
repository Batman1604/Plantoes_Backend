from sqlalchemy import Boolean, Date, ForeignKey, Integer, Numeric, String, Time
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class User(Base):
    __tablename__ = "usuarios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    nome: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    telefone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    senha_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)

    locais: Mapped[list["Local"]] = relationship(back_populates="usuario", cascade="all, delete-orphan")


class Local(Base):
    __tablename__ = "locais"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False)
    nome_local: Mapped[str] = mapped_column(String(120), nullable=False)
    data_inicio: Mapped[str] = mapped_column(Date, nullable=False)
    data_final: Mapped[str] = mapped_column(Date, nullable=False)
    dia_pagamento: Mapped[int | None] = mapped_column(Integer, nullable=True)

    usuario: Mapped[User] = relationship(back_populates="locais")
    plantoes: Mapped[list["Plantao"]] = relationship(back_populates="local", cascade="all, delete-orphan")


class Plantao(Base):
    __tablename__ = "plantoes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    local_id: Mapped[int] = mapped_column(ForeignKey("locais.id", ondelete="CASCADE"), nullable=False)
    titulo: Mapped[str] = mapped_column(String(120), nullable=False)
    valor: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    horario_inicio: Mapped[str] = mapped_column(Time, nullable=False)
    horario_fim: Mapped[str] = mapped_column(Time, nullable=False)
    recorrencia: Mapped[str | None] = mapped_column(String(50), nullable=True)
    notificacao_ativa: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notificacao_antecedencia_minutos: Mapped[int] = mapped_column(Integer, nullable=False, default=60)

    local: Mapped[Local] = relationship(back_populates="plantoes")
