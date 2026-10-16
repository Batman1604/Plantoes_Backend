import re
from datetime import UTC, date, datetime, time
from uuid import uuid4

from sqlalchemy import Boolean, CheckConstraint, Column, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, Time, UniqueConstraint

# Definição dos modelos de domínio persistidos no banco de dados.
# Cada classe corresponde a uma tabela e usa SQLAlchemy ORM para mapeamento.
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sqlalchemy import event

from app.core.database import Base


def normalizar_email(email: str | None) -> str | None:
    """AUD-058: e-mail sem espaços nas pontas e em minúsculas. O UNIQUE do
    Postgres diferencia caixa: sem isto, 'Maria@x' e 'maria@x' viravam duas
    contas, e o reset de senha com a outra grafia dava 404."""
    if email is None:
        return None
    return email.strip().lower()


def normalizar_telefone(telefone: str | None) -> str | None:
    """Reduz um telefone a apenas dígitos e remove o DDI 55 quando presente.

    Permite comparar números digitados com ou sem máscara e com ou sem DDI.
    """
    if not telefone:
        return None
    digitos = re.sub(r"\D", "", telefone)
    if len(digitos) > 11 and digitos.startswith("55"):
        digitos = digitos[2:]
    return digitos or None


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
    # Só dígitos, sem DDI — mantido em sincronia com `telefone` pelos eventos
    # logo abaixo, e indexado para que a busca por telefone seja uma consulta
    # indexada em vez de uma varredura da tabela de usuários inteira em memória
    # (ver auditoria A-22).
    telefone_normalizado: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    senha_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Identificadores estáveis ('sub') devolvidos pelo Google/Apple. Nulos para contas
    # email/senha puras; uma conta pode ter os dois preenchidos se o usuário vincular ambos.
    google_id: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True, index=True)
    apple_id: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True, index=True)
    plano: Mapped[str] = mapped_column(String(20), nullable=False, default="free")
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    pagamento_realizado: Mapped[date | None] = mapped_column(Date, nullable=True)
    email_verificado: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    telefone_verificado: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    avatar: Mapped[str | None] = mapped_column(String(255), nullable=True)
    profissao: Mapped[str | None] = mapped_column(String(255), nullable=True)
    theme_mode: Mapped[str] = mapped_column(String(10), nullable=False, default="light")
    # AUD-031: tokens de acesso emitidos ANTES deste instante são recusados.
    # Gravado ao trocar/redefinir a senha (e ao anular a senha no vínculo
    # OAuth do AUD-010). A revogação por jti (tokens_revogados) só alcança o
    # token do próprio aparelho; esta coluna derruba todos de uma vez.
    sessoes_invalidas_antes_de: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

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
    # Identificador gerado no dispositivo (ex.: uuid4), estável mesmo antes deste registro
    # existir no servidor. Permite que o app correlacione o que criou offline com o "id"
    # que o servidor atribui na primeira sincronização, sem risco de duplicar o registro
    # em uma reenvio.
    uuid_cliente: Mapped[str | None] = mapped_column(String(36), unique=True, nullable=True, index=True)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )

    usuario: Mapped[User] = relationship(back_populates="locais")
    # Sem delete-orphan (AUD-006): um plantão RECEBIDO por outro usuário aponta
    # para o local de quem cedeu. Ao excluir o local (ou a conta), esse
    # plantão é desvinculado (local_id NULL) e continua na conta de quem
    # recebeu — ver LocalRepository._desvincular_plantoes_de_terceiros. Com
    # delete-orphan, tirá-lo da coleção o apagaria.
    plantoes: Mapped[list["Plantao"]] = relationship(back_populates="local", cascade="all")


class Plantao(Base):
    __tablename__ = "plantoes"
    __table_args__ = (
        # Índice composto que serve a consulta principal do app: os plantões de
        # um usuário, em ordem de data. Os índices separados de usuario_id e
        # data_inicio não cobrem isso bem.
        #
        # Ele era criado por ensure_indexes() em main.py, sem estar declarado
        # aqui — então autogerar a migração inicial a partir dos modelos o teria
        # perdido em silêncio, e a consulta mais quente do produto passaria a
        # varrer a tabela. Declarado aqui, o modelo volta a ser a única fonte
        # da verdade do schema.
        Index("ix_plantoes_usuario_data_inicio", "usuario_id", "data_inicio"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False, index=True)
    # Nulável e SET NULL (AUD-006, decisão do usuário: desvincular): quando o
    # dono do local o exclui, ou exclui a conta, o plantão que OUTRO usuário
    # recebeu dele continua existindo, sem local. O nome do hospital fica na
    # troca (Troca.local) e volta no `local_nome` da resposta. Os plantões do
    # próprio dono são excluídos pelo ORM, com tombstone.
    local_id: Mapped[int | None] = mapped_column(ForeignKey("locais.id", ondelete="SET NULL"), nullable=True, index=True)
    valor: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    data_inicio: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    data_fim: Mapped[date] = mapped_column(Date, nullable=False)
    horario_inicio: Mapped[time] = Column(Time, nullable=False)
    horario_fim: Mapped[time] = Column(Time, nullable=False)
    recorrencia: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # K8 (AUD-053): série a que o plantão pertence, como o APARELHO a definiu
    # (Shift.seriesId). Sem ele, o servidor adivinhava a série por local +
    # horário + recorrência, e "este e futuros" num aparelho virava "só este"
    # no outro. Nulo em dados antigos e em plantão vindo de cliente antigo —
    # aí a heurística continua valendo (ver list_futuros_da_serie).
    serie_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notificacao_ativa: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notificacao_antecedencia_minutos: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    notificado: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    executado: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    pagamento_plantao: Mapped[date | None] = mapped_column(Date, nullable=True)
    sobreaviso: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    transferido: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    recebido_via_transferencia: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    descricao: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Ver comentário equivalente em Local.uuid_cliente.
    #
    # O `default` cobre o plantão que nasce NO SERVIDOR, sem dispositivo por
    # trás para gerar o identificador: aceitar uma transferência e aceitar uma
    # solicitação de troca criam o plantão do destinatário. Esses caminhos
    # gravavam uuid_cliente nulo e o app, recebendo nulo no pull, inventava um
    # aleatório NOVO a cada ciclo — trocando a identidade da linha e deixando
    # presa na fila qualquer edição feita no plantão recebido. O default
    # dispara inclusive quando o campo é passado explicitamente como None,
    # que é o caso de TrocaService quando o payload não traz o uuid.
    uuid_cliente: Mapped[str | None] = mapped_column(
        String(36), unique=True, nullable=True, index=True, default=lambda: str(uuid4())
    )
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )

    local: Mapped[Local | None] = relationship(back_populates="plantoes")
    usuario: Mapped[User] = relationship(foreign_keys=[usuario_id])
    transferencia: Mapped["Transferencia | None"] = relationship(
        back_populates="plantao",
        uselist=False,
        cascade="all, delete-orphan",
    )
    bonificacoes: Mapped[list["Bonificacao"]] = relationship(
        back_populates="plantao",
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
    em_atraso: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

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
    # Sem expiração o código de autenticação ficava válido para sempre; sem contador de
    # tentativas, dava para tentar adivinhar os 6 dígitos sem limite.
    codigo_expira_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tentativas_codigo: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    atualizado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))

    usuario: Mapped["User | None"] = relationship(foreign_keys=[usuario_id])


class VerificacaoTelefone(Base):
    __tablename__ = "verificacoes_telefone"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    codigo: Mapped[str] = mapped_column(String(6), nullable=False)
    expira_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # AUD-032: erros de digitação deste código; no limite, o código morre.
    tentativas: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    usuario: Mapped[User] = relationship(foreign_keys=[usuario_id])


class CodigoResetSenha(Base):
    __tablename__ = "codigos_reset_senha"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    codigo: Mapped[str] = mapped_column(String(6), nullable=False)
    expira_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # AUD-032: erros de digitação deste código; no limite, o código morre.
    tentativas: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    usuario: Mapped[User] = relationship(foreign_keys=[usuario_id])


class CadastroPendente(Base):
    """Cadastro iniciado mas ainda não confirmado: a conta em `usuarios` só é
    criada depois que o código de verificação enviado por WhatsApp/SMS é
    confirmado (ver /auth/cadastro e /auth/confirmar-cadastro)."""
    __tablename__ = "cadastros_pendentes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    nome: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    telefone: Mapped[str] = mapped_column(String(30), nullable=False)
    senha_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    codigo: Mapped[str] = mapped_column(String(6), nullable=False)
    expira_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))



class Bonificacao(Base):
    __tablename__ = "bonificacoes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    plantao_id: Mapped[int] = mapped_column(ForeignKey("plantoes.id", ondelete="CASCADE"), nullable=False, index=True)
    local_id: Mapped[int | None] = mapped_column(ForeignKey("locais.id", ondelete="SET NULL"), nullable=True, index=True)
    valor: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    descricao: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Ver comentário equivalente em Local.uuid_cliente.
    uuid_cliente: Mapped[str | None] = mapped_column(String(36), unique=True, nullable=True, index=True)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )

    plantao: Mapped["Plantao"] = relationship(back_populates="bonificacoes")


class Troca(Base):
    __tablename__ = "trocas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    usuario_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True, index=True)
    local_id: Mapped[int | None] = mapped_column(ForeignKey("locais.id", ondelete="SET NULL"), nullable=True, index=True)
    plantao_id_transferido: Mapped[int | None] = mapped_column(ForeignKey("plantoes.id", ondelete="SET NULL"), nullable=True, index=True)
    plantao_id_recebido: Mapped[int | None] = mapped_column(ForeignKey("plantoes.id", ondelete="SET NULL"), nullable=True, index=True)
    nome_transferir: Mapped[str | None] = mapped_column(String(120), nullable=True)
    nome_receber: Mapped[str | None] = mapped_column(String(120), nullable=True)
    telefone_transferir: Mapped[str | None] = mapped_column(String(30), nullable=True)
    telefone_receber: Mapped[str | None] = mapped_column(String(30), nullable=True)
    observacao_transferir: Mapped[str | None] = mapped_column(Text, nullable=True)
    observacao_receber: Mapped[str | None] = mapped_column(Text, nullable=True)
    local: Mapped[str | None] = mapped_column(String(120), nullable=True)
    data_inicio: Mapped[date | None] = mapped_column(Date, nullable=True)
    data_fim: Mapped[date | None] = mapped_column(Date, nullable=True)
    horario_inicio: Mapped[time | None] = mapped_column(Time, nullable=True)
    horario_fim: Mapped[time | None] = mapped_column(Time, nullable=True)
    contraparte_usuario_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True, index=True
    )
    contraparte_usuario_id_receber: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True, index=True
    )
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    # Ver comentário equivalente em Local.uuid_cliente.
    uuid_cliente: Mapped[str | None] = mapped_column(String(36), unique=True, nullable=True, index=True)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )

    local_rel: Mapped["Local | None"] = relationship(foreign_keys=[local_id])
    plantao_transferido: Mapped["Plantao | None"] = relationship(foreign_keys=[plantao_id_transferido])
    plantao_recebido: Mapped["Plantao | None"] = relationship(foreign_keys=[plantao_id_recebido])
    contraparte: Mapped["User | None"] = relationship(foreign_keys=[contraparte_usuario_id])
    contraparte_receber: Mapped["User | None"] = relationship(foreign_keys=[contraparte_usuario_id_receber])


class Amizade(Base):
    """Ciclo de vida do convite de amizade entre dois usuários."""
    __tablename__ = "amizades"
    __table_args__ = (
        CheckConstraint("status IN ('pendente', 'aceito', 'rejeitado')", name="ck_amizades_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    solicitante_usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False, index=True)
    destinatario_usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pendente")
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )

    solicitante: Mapped[User] = relationship(foreign_keys=[solicitante_usuario_id])
    destinatario: Mapped[User] = relationship(foreign_keys=[destinatario_usuario_id])


class AmigoConfiguracao(Base):
    """Configuração unidirecional (visibilidade e cor) que um usuário define para um amigo."""
    __tablename__ = "amigo_configuracoes"
    __table_args__ = (
        CheckConstraint("modo_visibilidade IN ('tudo', 'personalizado')", name="ck_amigo_configuracoes_modo"),
        UniqueConstraint("usuario_id", "amigo_usuario_id", name="uq_amigo_configuracoes_usuario_amigo"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False, index=True)
    amigo_usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False, index=True)
    modo_visibilidade: Mapped[str] = mapped_column(String(20), nullable=False, default="tudo")
    mostrar_valores: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    cor: Mapped[str] = mapped_column(String(7), nullable=False, default="#0D9488")
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )

    usuario: Mapped[User] = relationship(foreign_keys=[usuario_id])
    amigo: Mapped[User] = relationship(foreign_keys=[amigo_usuario_id])
    locais_permitidos: Mapped[list["AmigoLocalPermitido"]] = relationship(
        back_populates="configuracao", cascade="all, delete-orphan"
    )


class AmigoLocalPermitido(Base):
    """Locais do dono da configuração visíveis para o amigo quando modo_visibilidade='personalizado'."""
    __tablename__ = "amigo_locais_permitidos"

    amigo_configuracao_id: Mapped[int] = mapped_column(
        ForeignKey("amigo_configuracoes.id", ondelete="CASCADE"), primary_key=True
    )
    local_id: Mapped[int] = mapped_column(ForeignKey("locais.id", ondelete="CASCADE"), primary_key=True)

    configuracao: Mapped[AmigoConfiguracao] = relationship(back_populates="locais_permitidos")


class SolicitacaoTroca(Base):
    """Pedido de troca de plantão entre amigos — fica pendente até o destinatário aceitar ou rejeitar."""
    __tablename__ = "solicitacoes_troca"
    __table_args__ = (
        CheckConstraint("tipo IN ('transferir', 'receber', 'trocar')", name="ck_solicitacoes_troca_tipo"),
        CheckConstraint("status IN ('pendente', 'aceita', 'rejeitada')", name="ck_solicitacoes_troca_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    remetente_usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False, index=True)
    destinatario_usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False, index=True)
    tipo: Mapped[str] = mapped_column(String(20), nullable=False)
    plantao_transferido_id: Mapped[int | None] = mapped_column(
        ForeignKey("plantoes.id", ondelete="CASCADE"), nullable=True, index=True
    )
    plantao_alvo_id: Mapped[int | None] = mapped_column(
        ForeignKey("plantoes.id", ondelete="CASCADE"), nullable=True, index=True
    )
    plantao_recebido_local_id: Mapped[int | None] = mapped_column(
        ForeignKey("locais.id", ondelete="CASCADE"), nullable=True
    )
    plantao_recebido_data_inicio: Mapped[date | None] = mapped_column(Date, nullable=True)
    plantao_recebido_horario_inicio: Mapped[time | None] = mapped_column(Time, nullable=True)
    plantao_recebido_data_fim: Mapped[date | None] = mapped_column(Date, nullable=True)
    plantao_recebido_horario_fim: Mapped[time | None] = mapped_column(Time, nullable=True)
    plantao_recebido_valor: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    plantao_recebido_sobreaviso: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pendente")
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    respondido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    remetente: Mapped[User] = relationship(foreign_keys=[remetente_usuario_id])
    destinatario: Mapped[User] = relationship(foreign_keys=[destinatario_usuario_id])
    plantao_transferido: Mapped["Plantao | None"] = relationship(foreign_keys=[plantao_transferido_id])
    plantao_alvo: Mapped["Plantao | None"] = relationship(foreign_keys=[plantao_alvo_id])
    plantao_recebido_local: Mapped["Local | None"] = relationship(foreign_keys=[plantao_recebido_local_id])


class SyncExclusao(Base):
    """Tombstone de sincronização: registra que uma linha foi excluída, para que outros
    dispositivos do mesmo usuário saibam remover a cópia local ao puxar mudanças (GET
    /api/sync/pull). Sem isso, uma exclusão física normal (DELETE ... WHERE id = ...) não
    deixaria nenhum rastro para um dispositivo que estava offline no momento."""
    __tablename__ = "sync_exclusoes"
    __table_args__ = (
        # A-05: SyncRepository.foi_excluido roda a cada item NOVO de todo push
        # (usuario_id + entidade + uuid_cliente) numa tabela que só cresce —
        # tombstones não são podados. Com os índices separados, o banco filtra
        # por um deles e varre o resto.
        Index("ix_sync_exclusoes_usuario_entidade_uuid", "usuario_id", "entidade", "uuid_cliente"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False, index=True)
    entidade: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    entidade_id: Mapped[int] = mapped_column(Integer, nullable=False)
    uuid_cliente: Mapped[str | None] = mapped_column(String(36), nullable=True)
    deletado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC), index=True
    )


# O telefone normalizado é derivado, nunca preenchido à mão. Um listener em vez
# de atribuição nos pontos de escrita porque existem três lugares que criam
# usuário e mais um que edita o perfil: esquecer um deles não daria erro, só
# faria a pessoa sumir da busca por telefone — falha silenciosa, difícil de
# associar à causa.
@event.listens_for(User, "before_insert")
@event.listens_for(User, "before_update")
def _sincronizar_telefone_normalizado(mapper, connection, alvo: User) -> None:
    alvo.telefone_normalizado = normalizar_telefone(alvo.telefone)


# AUD-058: mesmo motivo do listener acima — todo ponto que grava usuário
# passa a gravar o e-mail normalizado, sem depender de cada chamador lembrar.
@event.listens_for(User, "before_insert")
@event.listens_for(User, "before_update")
def _normalizar_email_do_usuario(mapper, connection, alvo: User) -> None:
    alvo.email = normalizar_email(alvo.email)
