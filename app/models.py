from datetime import UTC, date, datetime, time

from sqlalchemy import Boolean, CheckConstraint, Column, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, Time

# Definição dos modelos de domínio persistidos no banco de dados.
# Cada classe corresponde a uma tabela e usa SQLAlchemy ORM para mapeamento.
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class User(Base):
    """Representa um usuário cadastrado no sistema."""
    __tablename__ = "usuarios"
    __table_args__ = (
        CheckConstraint("plano IN ('free', 'premium')", name="ck_usuarios_plano"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    nome: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    telefone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    senha_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    plano: Mapped[str] = mapped_column(String(20), nullable=False, default="free")
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    pagamento_realizado: Mapped[date | None] = mapped_column(Date, nullable=True)
    email_verificado: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    telefone_verificado: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    avatar: Mapped[str | None] = mapped_column(String(255), nullable=True)
    profissao: Mapped[str | None] = mapped_column(String(255), nullable=True)
    device_token: Mapped[str | None] = mapped_column(String(255), nullable=True)

    locais: Mapped[list["Local"]] = relationship(back_populates="usuario", cascade="all, delete-orphan")


class Local(Base):
    """Representa um local/hospital onde plantões podem ser agendados."""
    __tablename__ = "locais"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False, index=True)
    nome_local: Mapped[str] = mapped_column(String(120), nullable=False)
    data_inicio: Mapped[date] = mapped_column(Date, nullable=False)
    data_final: Mapped[date] = mapped_column(Date, nullable=False)
    dia_pagamento: Mapped[int | None] = mapped_column(Integer, nullable=True)
    util_corrido: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    cor: Mapped[str | None] = mapped_column(String(20), nullable=True)
    endereco_local: Mapped[str | None] = mapped_column(String(255), nullable=True)
    executado: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    usuario: Mapped[User] = relationship(back_populates="locais")
    plantoes: Mapped[list["Plantao"]] = relationship(back_populates="local", cascade="all, delete-orphan")


class Plantao(Base):
    __tablename__ = "plantoes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False, index=True)
    local_id: Mapped[int] = mapped_column(ForeignKey("locais.id", ondelete="CASCADE"), nullable=False, index=True)
    valor: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    data_inicio: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    data_fim: Mapped[date] = mapped_column(Date, nullable=False)
    horario_inicio: Mapped[time] = Column(Time, nullable=False)
    horario_fim: Mapped[time] = Column(Time, nullable=False)
    recorrencia: Mapped[str | None] = mapped_column(String(50), nullable=True)
    notificacao_ativa: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notificacao_antecedencia_minutos: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    notificado: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    executado: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    pagamento_plantao: Mapped[date | None] = mapped_column(Date, nullable=True)
    sobreaviso: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    transferido: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    descricao: Mapped[str | None] = mapped_column(Text, nullable=True)

    local: Mapped[Local] = relationship(back_populates="plantoes")
    transferencia: Mapped["Transferencia | None"] = relationship(
        back_populates="plantao",
        uselist=False,
        cascade="all, delete-orphan",
    )


class Transferencia(Base):
    __tablename__ = "transferencias"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    plantao_id: Mapped[int] = mapped_column(
        ForeignKey("plantoes.id", ondelete="CASCADE"), nullable=False, index=True, unique=True
    )
    remetente_id: Mapped[int] = mapped_column(
        ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Preenchido automaticamente se o telefone do destinatário corresponder a um usuário cadastrado.
    # Permite rastrear transferências entre usuários registrados (base para lista de amigos futura).
    destinatario_usuario_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True, index=True
    )
    destinatario_nome: Mapped[str] = mapped_column(String(120), nullable=False)
    destinatario_telefone: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="enviado")
    canal: Mapped[str] = mapped_column(String(20), nullable=False, default="sms")
    sms_enviado: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    plantao: Mapped[Plantao] = relationship(back_populates="transferencia", foreign_keys=[plantao_id])
    remetente: Mapped[User] = relationship(foreign_keys=[remetente_id])
    destinatario: Mapped[User | None] = relationship(foreign_keys=[destinatario_usuario_id])


class Financeiro(Base):
    __tablename__ = "financeiro"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    local_id: Mapped[int] = mapped_column(ForeignKey("locais.id", ondelete="CASCADE"), nullable=False, index=True)
    valor: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    data: Mapped[date] = mapped_column(Date, nullable=False)

    local: Mapped[Local] = relationship()


class TokenRevogado(Base):
    __tablename__ = "tokens_revogados"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    jti: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, index=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False)
    revogado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expira_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WhatsappSessao(Base):
    __tablename__ = "whatsapp_sessoes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    telefone: Mapped[str] = mapped_column(String(30), unique=True, nullable=False, index=True)
    usuario_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=True)
    estado: Mapped[str] = mapped_column(String(30), nullable=False, default="aguardando_email")
    historico: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    codigo_verificacao: Mapped[str | None] = mapped_column(String(10), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    atualizado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))

    usuario: Mapped["User | None"] = relationship(foreign_keys=[usuario_id])


class VerificacaoTelefone(Base):
    __tablename__ = "verificacoes_telefone"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    codigo: Mapped[str] = mapped_column(String(6), nullable=False)
    expira_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    usuario: Mapped[User] = relationship(foreign_keys=[usuario_id])


class Troca(Base):
    __tablename__ = "trocas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    local_id: Mapped[int] = mapped_column(ForeignKey("locais.id", ondelete="CASCADE"), nullable=False, index=True)
    nome_transferir: Mapped[str] = mapped_column(String(120), nullable=False)
    nome_receber: Mapped[str] = mapped_column(String(120), nullable=False)
    telefone_transferir: Mapped[str] = mapped_column(String(30), nullable=False)
    telefone_receber: Mapped[str] = mapped_column(String(30), nullable=False)

    local: Mapped[Local] = relationship()
