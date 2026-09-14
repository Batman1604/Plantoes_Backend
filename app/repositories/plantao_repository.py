import re
from datetime import UTC, date, datetime

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.security import hash_password, verify_password
from app.models import (
    Amizade,
    AmigoConfiguracao,
    AmigoLocalPermitido,
    Bonificacao,
    CadastroPendente,
    CodigoResetSenha,
    Financeiro,
    Local,
    Plantao,
    SolicitacaoTroca,
    SyncExclusao,
    TokenRevogado,
    Transferencia,
    Troca,
    User,
    VerificacaoTelefone,
)
from app.schemas.plantao_schema import LocalCreate, PlantaoCreate, UserCreate, UserLogin


def _normalizar_telefone(telefone: str) -> str:
    """Reduz um telefone a apenas dígitos e remove o DDI 55 quando presente, permitindo
    comparar números digitados com ou sem máscara/DDI."""
    digitos = re.sub(r"\D", "", telefone or "")
    if len(digitos) > 11 and digitos.startswith("55"):
        digitos = digitos[2:]
    return digitos


def _ids_excluidos_busca_amigo(db: Session, usuario_id: int) -> set[int]:
    """IDs a excluir de uma busca de usuários para adicionar amigo: o próprio usuário e
    qualquer um com quem ele já tenha amizade pendente ou aceita (nas duas direções)."""
    relacoes = db.execute(
        select(Amizade.solicitante_usuario_id, Amizade.destinatario_usuario_id).where(
            Amizade.status.in_(("pendente", "aceito")),
            or_(
                Amizade.solicitante_usuario_id == usuario_id,
                Amizade.destinatario_usuario_id == usuario_id,
            ),
        )
    ).all()

    ids_excluir = {usuario_id}
    for solicitante_id, destinatario_id in relacoes:
        ids_excluir.add(solicitante_id)
        ids_excluir.add(destinatario_id)
    return ids_excluir


class UserRepository:
    @staticmethod
    def create(db: Session, payload: UserCreate) -> User:
        """Cria um novo usuário e armazena a senha com hash seguro."""
        user_data = payload.model_dump(exclude={"senha"})
        user = User(**user_data, senha_hash=hash_password(payload.senha))
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

    @staticmethod
    def create_a_partir_de_pendente(db: Session, pendente: CadastroPendente) -> User:
        """Cria a conta definitiva a partir de um cadastro pendente já confirmado.
        Reaproveita o senha_hash já calculado (evita re-hash de senha em texto
        plano) e marca o telefone como verificado, já que o código foi confirmado."""
        user = User(
            nome=pendente.nome,
            email=pendente.email,
            telefone=pendente.telefone,
            senha_hash=pendente.senha_hash,
            telefone_verificado=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

    @staticmethod
    def authenticate(db: Session, payload: UserLogin) -> User | None:
        """Autentica usuário por email e senha, retornando o objeto de usuário quando válido."""
        user = db.scalar(select(User).where(func.lower(User.email) == payload.email.lower()))
        if user is None or not user.senha_hash:
            return None
        if not verify_password(payload.senha, user.senha_hash):
            return None
        return user

    @staticmethod
    def list_all(db: Session) -> list[User]:
        return db.scalars(select(User).order_by(User.id.desc())).all()

    @staticmethod
    def get_by_id(db: Session, user_id: int) -> User | None:
        return db.scalar(select(User).where(User.id == user_id))

    @staticmethod
    def get_by_email(db: Session, email: str) -> User | None:
        return db.scalar(select(User).where(User.email == email.lower()))

    @staticmethod
    def get_by_phone(db: Session, telefone: str) -> User | None:
        return db.scalar(select(User).where(User.telefone == telefone))

    @staticmethod
    def buscar_por_email_prefixo(db: Session, prefixo: str, excluir_usuario_id: int) -> list[User]:
        """Busca usuários cujo email começa com o prefixo, excluindo o próprio usuário e
        qualquer usuário que já tenha amizade pendente ou aceita com ele."""
        ids_excluir = _ids_excluidos_busca_amigo(db, excluir_usuario_id)

        stmt = (
            select(User)
            .where(User.email.ilike(f"{prefixo}%"), User.id.notin_(ids_excluir))
            .order_by(User.email)
            .limit(5)
        )
        return list(db.scalars(stmt).all())

    @staticmethod
    def buscar_por_telefone(db: Session, telefone: str, excluir_usuario_id: int) -> list[User]:
        """Busca usuários cujo telefone, depois de normalizado (apenas dígitos, sem DDI 55),
        bate com o telefone informado (também normalizado). Exclui o próprio usuário e
        qualquer usuário que já tenha amizade pendente ou aceita com ele. O telefone não é
        armazenado de forma normalizada, então a comparação é feita em Python após carregar
        os usuários com telefone cadastrado."""
        alvo = _normalizar_telefone(telefone)
        if not alvo:
            return []

        ids_excluir = _ids_excluidos_busca_amigo(db, excluir_usuario_id)

        stmt = select(User).where(User.telefone.isnot(None), User.id.notin_(ids_excluir)).order_by(User.email)
        candidatos = db.scalars(stmt).all()
        return [u for u in candidatos if _normalizar_telefone(u.telefone) == alvo][:5]

    @staticmethod
    def _find_or_create_oauth(
        db: Session,
        *,
        provider_column,
        provider_id: str,
        email: str,
        nome: str,
        avatar: str | None,
        email_verificado: bool,
    ) -> tuple[User, bool]:
        """Localiza um usuário pelo id do provedor (google_id/apple_id); se não achar, tenta
        vincular a uma conta existente com o mesmo email — só quando o provedor confirma que
        o email é verificado, para não permitir que alguém sequestre uma conta alheia só por
        conhecer o email dela. Se não houver conta nenhuma, cria uma nova sem senha.

        Retorna (usuario, criado). Levanta ValueError("email_nao_verificado") quando existe
        conta com o email mas o provedor não confirma a posse dele.
        """
        usuario = db.scalar(select(User).where(provider_column == provider_id))
        if usuario is not None:
            return usuario, False

        usuario = db.scalar(select(User).where(func.lower(User.email) == email.lower()))
        if usuario is not None:
            if not email_verificado:
                raise ValueError("email_nao_verificado")
            setattr(usuario, provider_column.key, provider_id)
            if not usuario.avatar and avatar:
                usuario.avatar = avatar
            if not usuario.email_verificado:
                usuario.email_verificado = True
            db.commit()
            db.refresh(usuario)
            return usuario, False

        usuario = User(
            nome=nome,
            email=email.lower(),
            senha_hash=None,
            avatar=avatar,
            email_verificado=email_verificado,
        )
        setattr(usuario, provider_column.key, provider_id)
        db.add(usuario)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            usuario = db.scalar(select(User).where(provider_column == provider_id))
            if usuario is not None:
                return usuario, False
            raise
        db.refresh(usuario)
        return usuario, True

    @staticmethod
    def find_or_create_google(db: Session, *, google_id: str, email: str, nome: str, avatar: str | None, email_verificado: bool) -> tuple[User, bool]:
        return UserRepository._find_or_create_oauth(
            db,
            provider_column=User.google_id,
            provider_id=google_id,
            email=email,
            nome=nome,
            avatar=avatar,
            email_verificado=email_verificado,
        )

    @staticmethod
    def find_or_create_apple(db: Session, *, apple_id: str, email: str, nome: str, email_verificado: bool) -> tuple[User, bool]:
        return UserRepository._find_or_create_oauth(
            db,
            provider_column=User.apple_id,
            provider_id=apple_id,
            email=email,
            nome=nome,
            avatar=None,
            email_verificado=email_verificado,
        )

    @staticmethod
    def delete(db: Session, user_id: int) -> bool:
        user = db.scalar(select(User).where(User.id == user_id))
        if user is None:
            return False
        db.delete(user)
        db.commit()
        return True

    @staticmethod
    def update_senha(db: Session, user_id: int, nova_senha: str) -> None:
        from app.core.security import hash_password
        user = db.scalar(select(User).where(User.id == user_id))
        if user:
            user.senha_hash = hash_password(nova_senha)
            db.commit()

    @staticmethod
    def marcar_email_verificado(db: Session, user_id: int) -> None:
        user = db.scalar(select(User).where(User.id == user_id))
        if user:
            user.email_verificado = True
            db.commit()

    @staticmethod
    def update_device_token(db: Session, user_id: int, device_token: str) -> None:
        user = db.scalar(select(User).where(User.id == user_id))
        if user:
            user.device_token = device_token
            db.commit()

    @staticmethod
    def update_perfil(db: Session, user_id: int, **kwargs) -> User | None:
        user = db.scalar(select(User).where(User.id == user_id))
        if user is None:
            return None
        for key, value in kwargs.items():
            if hasattr(user, key):
                setattr(user, key, value)
        db.commit()
        db.refresh(user)
        return user


class LocalRepository:
    @staticmethod
    def create(db: Session, payload: LocalCreate) -> Local:
        """Cria um local/hospital no banco de dados para um usuário."""
        local = Local(**payload.model_dump())
        db.add(local)
        db.commit()
        db.refresh(local)
        return local

    @staticmethod
    def get_by_id(db: Session, local_id: int) -> Local | None:
        return db.scalar(select(Local).where(Local.id == local_id))

    @staticmethod
    def list_by_user(db: Session, usuario_id: int) -> list[Local]:
        stmt = select(Local).where(Local.usuario_id == usuario_id).order_by(Local.data_inicio.desc())
        return db.scalars(stmt).all()

    @staticmethod
    def list_all(db: Session) -> list[Local]:
        return db.scalars(select(Local).order_by(Local.data_inicio.desc())).all()

    @staticmethod
    def get_by_uuid_cliente(db: Session, uuid_cliente: str, usuario_id: int) -> Local | None:
        return db.scalar(
            select(Local).where(Local.uuid_cliente == uuid_cliente, Local.usuario_id == usuario_id)
        )

    @staticmethod
    def update_by_user(db: Session, local_id: int, usuario_id: int, **kwargs) -> Local | None:
        local = db.scalar(select(Local).where(Local.id == local_id, Local.usuario_id == usuario_id))
        if local is None:
            return None
        for key, value in kwargs.items():
            if hasattr(local, key):
                setattr(local, key, value)
        db.commit()
        db.refresh(local)
        return local

    @staticmethod
    def delete_by_user(db: Session, local_id: int, usuario_id: int) -> bool:
        local = db.scalar(select(Local).where(Local.id == local_id, Local.usuario_id == usuario_id))
        if local is None:
            return False
        # O cascade="all, delete-orphan" de Local.plantoes apaga os plantões no
        # banco junto com o local, mas só na sessão do ORM — nunca passa por
        # PlantaoRepository.delete_by_user, que é quem grava o tombstone de
        # sync. Sem isso aqui, o /sync/pull incremental de outro dispositivo
        # nunca fica sabendo que esses plantões sumiram (não aparecem nem como
        # alterados, já que não existem mais, nem em "exclusões"), e ficam
        # presos no calendário/cards de pagamento do cliente até um resync
        # completo (novo login).
        for plantao in local.plantoes:
            SyncRepository.registrar_exclusao(db, usuario_id, "plantao", plantao.id, plantao.uuid_cliente)
        SyncRepository.registrar_exclusao(db, usuario_id, "local", local.id, local.uuid_cliente)
        db.delete(local)
        db.commit()
        return True


class PlantaoRepository:
    @staticmethod
    def create(db: Session, payload: PlantaoCreate) -> Plantao:
        plantao = Plantao(**payload.model_dump())
        db.add(plantao)
        db.commit()
        db.refresh(plantao)
        return plantao

    @staticmethod
    def update(db: Session, plantao_id: int, **kwargs) -> Plantao | None:
        plantao = db.scalar(select(Plantao).where(Plantao.id == plantao_id))
        if plantao is None:
            return None
        for key, value in kwargs.items():
            if hasattr(plantao, key):
                setattr(plantao, key, value)
        db.commit()
        db.refresh(plantao)
        return plantao

    @staticmethod
    def update_executado(db: Session, plantao_id: int, usuario_id: int, executado: bool) -> Plantao | None:
        plantao = db.scalar(
            select(Plantao).where(
                Plantao.id == plantao_id,
                Plantao.usuario_id == usuario_id,
                Plantao.transferido.is_(False),
            )
        )
        if plantao is None:
            return None

        plantao.executado = executado
        db.commit()
        db.refresh(plantao)
        return plantao

    @staticmethod
    def list_by_user(db: Session, usuario_id: int) -> list[Plantao]:
        """Retorna todos os plantões do usuário, incluindo os transferidos para histórico."""
        stmt = (
            select(Plantao)
            .where(Plantao.usuario_id == usuario_id)
            .options(selectinload(Plantao.bonificacoes), selectinload(Plantao.local))
            .order_by(Plantao.id.desc())
        )
        return db.scalars(stmt).all()

    @staticmethod
    def list_by_user_e_locais(db: Session, usuario_id: int, local_ids: list[int]) -> list[Plantao]:
        """Retorna os plantões ativos (não transferidos) do usuário restritos aos locais informados.

        Usado para exibir a agenda de um amigo, já filtrada pelos locais que ele permite mostrar.
        """
        if not local_ids:
            return []
        stmt = (
            select(Plantao)
            .where(
                Plantao.usuario_id == usuario_id,
                Plantao.local_id.in_(local_ids),
                Plantao.transferido.is_(False),
            )
            .options(selectinload(Plantao.bonificacoes), selectinload(Plantao.local))
            .order_by(Plantao.data_inicio.desc())
        )
        return list(db.scalars(stmt).all())

    @staticmethod
    def list_all(db: Session) -> list[Plantao]:
        return db.scalars(select(Plantao).order_by(Plantao.id.desc())).all()

    @staticmethod
    def get_by_id_and_user(db: Session, plantao_id: int, usuario_id: int) -> Plantao | None:
        return db.scalar(
            select(Plantao).where(
                Plantao.id == plantao_id,
                Plantao.usuario_id == usuario_id,
                Plantao.transferido.is_(False),
            )
        )

    @staticmethod
    def get_by_id_and_user_incluindo_transferidos(db: Session, plantao_id: int, usuario_id: int) -> Plantao | None:
        """Como get_by_id_and_user, mas também retorna plantões já transferidos.

        Usado onde é preciso distinguir 'não existe/não é seu' (404) de
        'existe, é seu, mas já foi transferido' (409) — ex.: PATCH/DELETE de /plantoes/:id.
        """
        return db.scalar(
            select(Plantao).where(
                Plantao.id == plantao_id,
                Plantao.usuario_id == usuario_id,
            )
        )

    @staticmethod
    def get_by_uuid_cliente(db: Session, uuid_cliente: str, usuario_id: int) -> Plantao | None:
        return db.scalar(
            select(Plantao).where(Plantao.uuid_cliente == uuid_cliente, Plantao.usuario_id == usuario_id)
        )

    @staticmethod
    def update_by_user(db: Session, plantao_id: int, usuario_id: int, **kwargs) -> Plantao | None:
        plantao = db.scalar(
            select(Plantao).where(
                Plantao.id == plantao_id,
                Plantao.usuario_id == usuario_id,
                Plantao.transferido.is_(False),
            )
        )
        if plantao is None:
            return None
        for key, value in kwargs.items():
            if hasattr(plantao, key):
                setattr(plantao, key, value)
        db.commit()
        db.refresh(plantao)
        return plantao

    @staticmethod
    def list_futuros_da_serie(db: Session, plantao_referencia: Plantao) -> list[Plantao]:
        stmt = (
            select(Plantao)
            .where(
                Plantao.usuario_id == plantao_referencia.usuario_id,
                Plantao.local_id == plantao_referencia.local_id,
                Plantao.horario_inicio == plantao_referencia.horario_inicio,
                Plantao.horario_fim == plantao_referencia.horario_fim,
                Plantao.data_inicio >= plantao_referencia.data_inicio,
                Plantao.transferido.is_(False),
            )
            .order_by(Plantao.data_inicio.asc(), Plantao.id.asc())
        )

        if plantao_referencia.recorrencia is None:
            stmt = stmt.where(Plantao.recorrencia.is_(None))
        else:
            stmt = stmt.where(Plantao.recorrencia == plantao_referencia.recorrencia)

        return db.scalars(stmt).all()

    @staticmethod
    def delete_by_user(db: Session, plantao_id: int, usuario_id: int) -> bool:
        plantao = db.scalar(
            select(Plantao).where(
                Plantao.id == plantao_id,
                Plantao.usuario_id == usuario_id,
                Plantao.transferido.is_(False),
            )
        )
        if plantao is None:
            return False
        SyncRepository.registrar_exclusao(db, usuario_id, "plantao", plantao.id, plantao.uuid_cliente)
        db.delete(plantao)
        db.commit()
        return True

    @staticmethod
    def list_ids_e_datas_por_local(db: Session, local_id: int) -> list[tuple[int, date]]:
        stmt = select(Plantao.id, Plantao.data_inicio).where(Plantao.local_id == local_id)
        return db.execute(stmt).all()


class TransferenciaRepository:
    @staticmethod
    def create(
        db: Session,
        *,
        plantao_id: int,
        remetente_id: int,
        destinatario_usuario_id: int | None,
        destinatario_nome: str,
        destinatario_telefone: str,
        status: str = "enviado",
        canal: str = "whatsapp",
        sms_enviado: bool = False,
    ) -> Transferencia:
        transferencia = Transferencia(
            plantao_id=plantao_id,
            remetente_id=remetente_id,
            destinatario_usuario_id=destinatario_usuario_id,
            destinatario_nome=destinatario_nome,
            destinatario_telefone=destinatario_telefone,
            status=status,
            canal=canal,
            sms_enviado=sms_enviado,
        )
        db.add(transferencia)
        db.commit()
        db.refresh(transferencia)
        return transferencia

    @staticmethod
    def get_by_plantao(db: Session, plantao_id: int) -> Transferencia | None:
        return db.scalar(select(Transferencia).where(Transferencia.plantao_id == plantao_id))

    @staticmethod
    def get_by_id_and_destinatario(db: Session, transferencia_id: int, destinatario_usuario_id: int) -> Transferencia | None:
        return db.scalar(
            select(Transferencia).where(
                Transferencia.id == transferencia_id,
                Transferencia.destinatario_usuario_id == destinatario_usuario_id,
                Transferencia.status == "pendente",
            )
        )

    @staticmethod
    def list_recebidas_por_usuario(db: Session, usuario_id: int) -> list[Transferencia]:
        stmt = (
            select(Transferencia)
            .where(
                Transferencia.destinatario_usuario_id == usuario_id,
                Transferencia.status == "pendente",
            )
            .order_by(Transferencia.criado_em.desc())
        )
        return db.scalars(stmt).all()

    @staticmethod
    def list_enviadas_por_usuario(db: Session, usuario_id: int) -> list[Transferencia]:
        stmt = (
            select(Transferencia)
            .where(Transferencia.remetente_id == usuario_id)
            .order_by(Transferencia.criado_em.desc())
        )
        return db.scalars(stmt).all()

    @staticmethod
    def atualizar_status(db: Session, transferencia_id: int, novo_status: str) -> None:
        transferencia = db.scalar(select(Transferencia).where(Transferencia.id == transferencia_id))
        if transferencia:
            transferencia.status = novo_status
            db.commit()

    @staticmethod
    def marcar_sms_enviado(db: Session, transferencia_id: int) -> None:
        transferencia = db.scalar(select(Transferencia).where(Transferencia.id == transferencia_id))
        if transferencia:
            transferencia.sms_enviado = True
            db.commit()


class VerificacaoTelefoneRepository:
    @staticmethod
    def upsert(db: Session, usuario_id: int, codigo: str, expira_em: datetime) -> VerificacaoTelefone:
        existente = db.scalar(select(VerificacaoTelefone).where(VerificacaoTelefone.usuario_id == usuario_id))
        if existente:
            existente.codigo = codigo
            existente.expira_em = expira_em
            db.commit()
            db.refresh(existente)
            return existente
        verificacao = VerificacaoTelefone(usuario_id=usuario_id, codigo=codigo, expira_em=expira_em)
        db.add(verificacao)
        db.commit()
        db.refresh(verificacao)
        return verificacao

    @staticmethod
    def get_valido(db: Session, usuario_id: int, codigo: str) -> VerificacaoTelefone | None:
        return db.scalar(
            select(VerificacaoTelefone).where(
                VerificacaoTelefone.usuario_id == usuario_id,
                VerificacaoTelefone.codigo == codigo,
                VerificacaoTelefone.expira_em > datetime.now(UTC),
            )
        )

    @staticmethod
    def delete_by_user(db: Session, usuario_id: int) -> None:
        db.execute(delete(VerificacaoTelefone).where(VerificacaoTelefone.usuario_id == usuario_id))
        db.commit()


class CodigoResetSenhaRepository:
    @staticmethod
    def upsert(db: Session, usuario_id: int, codigo: str, expira_em: datetime) -> CodigoResetSenha:
        existente = db.scalar(select(CodigoResetSenha).where(CodigoResetSenha.usuario_id == usuario_id))
        if existente:
            existente.codigo = codigo
            existente.expira_em = expira_em
            db.commit()
            db.refresh(existente)
            return existente
        codigo_reset = CodigoResetSenha(usuario_id=usuario_id, codigo=codigo, expira_em=expira_em)
        db.add(codigo_reset)
        db.commit()
        db.refresh(codigo_reset)
        return codigo_reset

    @staticmethod
    def get_valido(db: Session, usuario_id: int, codigo: str) -> CodigoResetSenha | None:
        return db.scalar(
            select(CodigoResetSenha).where(
                CodigoResetSenha.usuario_id == usuario_id,
                CodigoResetSenha.codigo == codigo,
                CodigoResetSenha.expira_em > datetime.now(UTC),
            )
        )

    @staticmethod
    def delete_by_user(db: Session, usuario_id: int) -> None:
        db.execute(delete(CodigoResetSenha).where(CodigoResetSenha.usuario_id == usuario_id))
        db.commit()


class CadastroPendenteRepository:
    @staticmethod
    def upsert(
        db: Session,
        *,
        nome: str,
        email: str,
        telefone: str,
        senha_hash: str,
        codigo: str,
        expira_em: datetime,
    ) -> CadastroPendente:
        email = email.lower()
        existente = db.scalar(select(CadastroPendente).where(CadastroPendente.email == email))
        if existente:
            existente.nome = nome
            existente.telefone = telefone
            existente.senha_hash = senha_hash
            existente.codigo = codigo
            existente.expira_em = expira_em
            db.commit()
            db.refresh(existente)
            return existente
        pendente = CadastroPendente(
            nome=nome, email=email, telefone=telefone, senha_hash=senha_hash, codigo=codigo, expira_em=expira_em
        )
        db.add(pendente)
        db.commit()
        db.refresh(pendente)
        return pendente

    @staticmethod
    def get_by_email(db: Session, email: str) -> CadastroPendente | None:
        return db.scalar(select(CadastroPendente).where(CadastroPendente.email == email.lower()))

    @staticmethod
    def delete_by_email(db: Session, email: str) -> None:
        db.execute(delete(CadastroPendente).where(CadastroPendente.email == email.lower()))
        db.commit()


class FinanceiroRepository:
    @staticmethod
    def sync_mes(db: Session, local_id: int, mes: int, ano: int) -> None:
        """Recalcula e persiste o total financeiro de um local/mês a partir dos plantões e bonificações."""
        total_plantoes = db.scalar(
            select(func.coalesce(func.sum(Plantao.valor), 0))
            .where(
                Plantao.local_id == local_id,
                func.extract("month", Plantao.data_inicio) == mes,
                func.extract("year", Plantao.data_inicio) == ano,
            )
        ) or 0

        total_bonificacoes = db.scalar(
            select(func.coalesce(func.sum(Bonificacao.valor), 0))
            .join(Plantao, Plantao.id == Bonificacao.plantao_id)
            .where(
                Plantao.local_id == local_id,
                func.extract("month", Plantao.data_inicio) == mes,
                func.extract("year", Plantao.data_inicio) == ano,
            )
        ) or 0

        total = float(total_plantoes) + float(total_bonificacoes)

        existente = db.scalar(
            select(Financeiro).where(
                Financeiro.local_id == local_id,
                func.extract("month", Financeiro.data) == mes,
                func.extract("year", Financeiro.data) == ano,
            )
        )

        if total == 0:
            if existente:
                db.delete(existente)
                db.commit()
            return

        if existente:
            existente.valor = total
            db.commit()
        else:
            db.add(Financeiro(local_id=local_id, valor=total, data=date(ano, mes, 1), em_atraso=False))
            db.commit()

    @staticmethod
    def upsert(db: Session, local_id: int, valor: float, data: date, em_atraso: bool) -> Financeiro:
        mes, ano = data.month, data.year
        existente = db.scalar(
            select(Financeiro).where(
                Financeiro.local_id == local_id,
                func.extract("month", Financeiro.data) == mes,
                func.extract("year", Financeiro.data) == ano,
            )
        )
        if existente:
            existente.valor = valor
            existente.data = data
            existente.em_atraso = em_atraso
            db.commit()
            db.refresh(existente)
            return existente
        novo = Financeiro(local_id=local_id, valor=valor, data=data, em_atraso=em_atraso)
        db.add(novo)
        db.commit()
        db.refresh(novo)
        return novo

    @staticmethod
    def get_by_id(db: Session, financeiro_id: int) -> Financeiro | None:
        return db.scalar(select(Financeiro).where(Financeiro.id == financeiro_id))

    @staticmethod
    def update_em_atraso(db: Session, record: Financeiro, em_atraso: bool) -> Financeiro:
        record.em_atraso = em_atraso
        db.commit()
        db.refresh(record)
        return record

    @staticmethod
    def list_by_local(db: Session, local_id: int) -> list[Financeiro]:
        return list(db.scalars(
            select(Financeiro).where(Financeiro.local_id == local_id).order_by(Financeiro.data.desc())
        ))


class BonificacaoRepository:
    @staticmethod
    def create(db: Session, payload) -> Bonificacao:
        bonificacao = Bonificacao(**payload.model_dump())
        db.add(bonificacao)
        db.commit()
        db.refresh(bonificacao)
        return bonificacao

    @staticmethod
    def list_by_plantao(db: Session, plantao_id: int) -> list[Bonificacao]:
        return list(db.scalars(select(Bonificacao).where(Bonificacao.plantao_id == plantao_id)).all())

    @staticmethod
    def list_by_usuario(db: Session, usuario_id: int) -> list[Bonificacao]:
        return list(db.scalars(
            select(Bonificacao)
            .join(Plantao, Bonificacao.plantao_id == Plantao.id)
            .where(Plantao.usuario_id == usuario_id)
        ).all())

    @staticmethod
    def get_by_id(db: Session, bonificacao_id: int) -> Bonificacao | None:
        return db.scalar(select(Bonificacao).where(Bonificacao.id == bonificacao_id))

    @staticmethod
    def get_by_uuid_cliente(db: Session, uuid_cliente: str, usuario_id: int) -> Bonificacao | None:
        return db.scalar(
            select(Bonificacao)
            .join(Plantao, Bonificacao.plantao_id == Plantao.id)
            .where(Bonificacao.uuid_cliente == uuid_cliente, Plantao.usuario_id == usuario_id)
        )

    @staticmethod
    def update(db: Session, bonificacao_id: int, **kwargs) -> Bonificacao | None:
        bonificacao = db.scalar(select(Bonificacao).where(Bonificacao.id == bonificacao_id))
        if bonificacao is None:
            return None
        for key, value in kwargs.items():
            if hasattr(bonificacao, key):
                setattr(bonificacao, key, value)
        db.commit()
        db.refresh(bonificacao)
        return bonificacao

    @staticmethod
    def delete(db: Session, bonificacao_id: int) -> bool:
        bonificacao = db.scalar(select(Bonificacao).where(Bonificacao.id == bonificacao_id))
        if bonificacao is None:
            return False
        db.delete(bonificacao)
        db.commit()
        return True


class TrocaRepository:
    @staticmethod
    def create(db: Session, payload) -> Troca:
        troca = Troca(**payload.model_dump())
        db.add(troca)
        db.commit()
        db.refresh(troca)
        return troca

    @staticmethod
    def list_by_local(db: Session, local_id: int) -> list[Troca]:
        return list(db.scalars(select(Troca).where(Troca.local_id == local_id)).all())

    @staticmethod
    def list_by_usuario(db: Session, usuario_id: int) -> list[Troca]:
        return list(db.scalars(
            select(Troca)
            .where(Troca.usuario_id == usuario_id)
            .order_by(Troca.id.desc())
        ).all())

    @staticmethod
    def get_by_id(db: Session, troca_id: int) -> Troca | None:
        return db.scalar(select(Troca).where(Troca.id == troca_id))

    @staticmethod
    def get_by_uuid_cliente(db: Session, uuid_cliente: str, usuario_id: int) -> Troca | None:
        return db.scalar(
            select(Troca).where(Troca.uuid_cliente == uuid_cliente, Troca.usuario_id == usuario_id)
        )

    @staticmethod
    def delete(db: Session, troca_id: int) -> bool:
        troca = db.scalar(select(Troca).where(Troca.id == troca_id))
        if troca is None:
            return False
        if troca.usuario_id is not None:
            SyncRepository.registrar_exclusao(db, troca.usuario_id, "troca", troca.id, troca.uuid_cliente)
        db.delete(troca)
        db.commit()
        return True


class AmizadeRepository:
    @staticmethod
    def get_relacao_existente(db: Session, usuario_a_id: int, usuario_b_id: int) -> Amizade | None:
        """Retorna amizade pendente ou aceita entre os dois usuários, em qualquer direção."""
        return db.scalar(
            select(Amizade).where(
                Amizade.status.in_(("pendente", "aceito")),
                or_(
                    and_(
                        Amizade.solicitante_usuario_id == usuario_a_id,
                        Amizade.destinatario_usuario_id == usuario_b_id,
                    ),
                    and_(
                        Amizade.solicitante_usuario_id == usuario_b_id,
                        Amizade.destinatario_usuario_id == usuario_a_id,
                    ),
                ),
            )
        )

    @staticmethod
    def get_aceita_entre(db: Session, usuario_a_id: int, usuario_b_id: int) -> Amizade | None:
        """Retorna a amizade ACEITA entre os dois usuários, em qualquer direção (ou None)."""
        return db.scalar(
            select(Amizade).where(
                Amizade.status == "aceito",
                or_(
                    and_(
                        Amizade.solicitante_usuario_id == usuario_a_id,
                        Amizade.destinatario_usuario_id == usuario_b_id,
                    ),
                    and_(
                        Amizade.solicitante_usuario_id == usuario_b_id,
                        Amizade.destinatario_usuario_id == usuario_a_id,
                    ),
                ),
            )
        )

    @staticmethod
    def create(db: Session, solicitante_usuario_id: int, destinatario_usuario_id: int) -> Amizade:
        amizade = Amizade(
            solicitante_usuario_id=solicitante_usuario_id,
            destinatario_usuario_id=destinatario_usuario_id,
            status="pendente",
        )
        db.add(amizade)
        db.commit()
        db.refresh(amizade)
        return amizade

    @staticmethod
    def get_by_id(db: Session, amizade_id: int) -> Amizade | None:
        return db.scalar(
            select(Amizade)
            .options(selectinload(Amizade.solicitante), selectinload(Amizade.destinatario))
            .where(Amizade.id == amizade_id)
        )

    @staticmethod
    def list_by_usuario(db: Session, usuario_id: int) -> list[Amizade]:
        stmt = (
            select(Amizade)
            .options(selectinload(Amizade.solicitante), selectinload(Amizade.destinatario))
            .where(
                or_(
                    Amizade.solicitante_usuario_id == usuario_id,
                    Amizade.destinatario_usuario_id == usuario_id,
                )
            )
            .order_by(Amizade.id.desc())
        )
        return list(db.scalars(stmt).all())

    @staticmethod
    def aceitar(db: Session, amizade: Amizade) -> tuple[Amizade, AmigoConfiguracao, AmigoConfiguracao]:
        """Marca a amizade como aceita e cria as duas linhas de configuração (uma por direção)."""
        amizade.status = "aceito"
        amizade.atualizado_em = datetime.now(UTC)

        config_solicitante = AmigoConfiguracao(
            usuario_id=amizade.solicitante_usuario_id,
            amigo_usuario_id=amizade.destinatario_usuario_id,
        )
        config_destinatario = AmigoConfiguracao(
            usuario_id=amizade.destinatario_usuario_id,
            amigo_usuario_id=amizade.solicitante_usuario_id,
        )
        db.add_all([amizade, config_solicitante, config_destinatario])
        db.commit()
        db.refresh(amizade)
        db.refresh(config_solicitante)
        db.refresh(config_destinatario)
        return amizade, config_solicitante, config_destinatario

    @staticmethod
    def _registrar_exclusao_dos_dois_lados(db: Session, amizade: Amizade) -> None:
        """Amizade envolve dois usuários — cada um precisa do tombstone no próprio pull."""
        for usuario_id in (amizade.solicitante_usuario_id, amizade.destinatario_usuario_id):
            SyncRepository.registrar_exclusao(db, usuario_id, "amizade", amizade.id)

    @staticmethod
    def rejeitar(db: Session, amizade: Amizade) -> None:
        AmizadeRepository._registrar_exclusao_dos_dois_lados(db, amizade)
        db.delete(amizade)
        db.commit()

    @staticmethod
    def cancelar_pendente(db: Session, amizade: Amizade) -> None:
        """Cancela uma solicitação ainda pendente, a pedido de quem a enviou."""
        AmizadeRepository._registrar_exclusao_dos_dois_lados(db, amizade)
        db.delete(amizade)
        db.commit()

    @staticmethod
    def deletar_aceita(db: Session, amizade: Amizade) -> None:
        """Remove a amizade aceita e as duas linhas de configuração associadas."""
        configs = db.scalars(
            select(AmigoConfiguracao).where(
                or_(
                    and_(
                        AmigoConfiguracao.usuario_id == amizade.solicitante_usuario_id,
                        AmigoConfiguracao.amigo_usuario_id == amizade.destinatario_usuario_id,
                    ),
                    and_(
                        AmigoConfiguracao.usuario_id == amizade.destinatario_usuario_id,
                        AmigoConfiguracao.amigo_usuario_id == amizade.solicitante_usuario_id,
                    ),
                )
            )
        ).all()
        for config in configs:
            SyncRepository.registrar_exclusao(db, config.usuario_id, "amigo_configuracao", config.id)
        AmizadeRepository._registrar_exclusao_dos_dois_lados(db, amizade)

        db.execute(
            delete(AmigoConfiguracao).where(
                or_(
                    and_(
                        AmigoConfiguracao.usuario_id == amizade.solicitante_usuario_id,
                        AmigoConfiguracao.amigo_usuario_id == amizade.destinatario_usuario_id,
                    ),
                    and_(
                        AmigoConfiguracao.usuario_id == amizade.destinatario_usuario_id,
                        AmigoConfiguracao.amigo_usuario_id == amizade.solicitante_usuario_id,
                    ),
                )
            )
        )
        db.delete(amizade)
        db.commit()


class AmigoConfiguracaoRepository:
    @staticmethod
    def get_by_id(db: Session, config_id: int) -> AmigoConfiguracao | None:
        return db.scalar(
            select(AmigoConfiguracao)
            .options(selectinload(AmigoConfiguracao.locais_permitidos))
            .where(AmigoConfiguracao.id == config_id)
        )

    @staticmethod
    def list_by_usuario(db: Session, usuario_id: int) -> list[AmigoConfiguracao]:
        stmt = (
            select(AmigoConfiguracao)
            .options(selectinload(AmigoConfiguracao.locais_permitidos))
            .where(AmigoConfiguracao.usuario_id == usuario_id)
        )
        return list(db.scalars(stmt).all())

    @staticmethod
    def list_visibilidade_para_usuario(
        db: Session, visualizador_usuario_id: int, donos_ids: list[int]
    ) -> list[AmigoConfiguracao]:
        """Configs que os `donos_ids` definiram para `visualizador_usuario_id` — ou seja, o que
        cada um deles decidiu mostrar para este usuário (direção oposta à de list_by_usuario)."""
        if not donos_ids:
            return []
        stmt = (
            select(AmigoConfiguracao)
            .options(selectinload(AmigoConfiguracao.locais_permitidos))
            .where(
                AmigoConfiguracao.amigo_usuario_id == visualizador_usuario_id,
                AmigoConfiguracao.usuario_id.in_(donos_ids),
            )
        )
        return list(db.scalars(stmt).all())

    @staticmethod
    def update(
        db: Session,
        config: AmigoConfiguracao,
        *,
        modo_visibilidade: str | None = None,
        mostrar_valores: bool | None = None,
        cor: str | None = None,
        locais_permitidos_ids: list[int] | None = None,
    ) -> AmigoConfiguracao:
        if modo_visibilidade is not None:
            config.modo_visibilidade = modo_visibilidade
        if mostrar_valores is not None:
            config.mostrar_valores = mostrar_valores
        if cor is not None:
            config.cor = cor
        if locais_permitidos_ids is not None:
            db.execute(
                delete(AmigoLocalPermitido).where(AmigoLocalPermitido.amigo_configuracao_id == config.id)
            )
            for local_id in locais_permitidos_ids:
                db.add(AmigoLocalPermitido(amigo_configuracao_id=config.id, local_id=local_id))
            # locais_permitidos_ids mexe só na tabela filha — sem tocar em nenhuma coluna
            # da própria config, o onupdate de atualizado_em não dispara sozinho, e essa
            # mudança ficaria invisível para GET /api/sync/pull.
            config.atualizado_em = datetime.now(UTC)
        db.commit()
        # Recarrega via get_by_id para garantir que locais_permitidos reflita as mudanças acima.
        return AmigoConfiguracaoRepository.get_by_id(db, config.id)


class SolicitacaoTrocaRepository:
    @staticmethod
    def create(db: Session, **kwargs) -> SolicitacaoTroca:
        solicitacao = SolicitacaoTroca(**kwargs)
        db.add(solicitacao)
        db.commit()
        db.refresh(solicitacao)
        return solicitacao

    @staticmethod
    def get_by_id(db: Session, solicitacao_id: int) -> SolicitacaoTroca | None:
        return db.scalar(
            select(SolicitacaoTroca)
            .options(
                selectinload(SolicitacaoTroca.remetente),
                selectinload(SolicitacaoTroca.destinatario),
                selectinload(SolicitacaoTroca.plantao_transferido),
                selectinload(SolicitacaoTroca.plantao_alvo),
                selectinload(SolicitacaoTroca.plantao_recebido_local),
            )
            .where(SolicitacaoTroca.id == solicitacao_id)
        )

    @staticmethod
    def list_by_usuario(db: Session, usuario_id: int) -> list[SolicitacaoTroca]:
        stmt = (
            select(SolicitacaoTroca)
            .options(
                selectinload(SolicitacaoTroca.remetente),
                selectinload(SolicitacaoTroca.destinatario),
                selectinload(SolicitacaoTroca.plantao_transferido).selectinload(Plantao.local),
                selectinload(SolicitacaoTroca.plantao_alvo).selectinload(Plantao.local),
                selectinload(SolicitacaoTroca.plantao_recebido_local),
            )
            .where(
                or_(
                    SolicitacaoTroca.remetente_usuario_id == usuario_id,
                    SolicitacaoTroca.destinatario_usuario_id == usuario_id,
                )
            )
            .order_by(SolicitacaoTroca.id.desc())
        )
        return list(db.scalars(stmt).all())

    @staticmethod
    def marcar_respondida(db: Session, solicitacao: SolicitacaoTroca, status: str) -> SolicitacaoTroca:
        solicitacao.status = status
        solicitacao.respondido_em = datetime.now(UTC)
        db.commit()
        db.refresh(solicitacao)
        return solicitacao

    @staticmethod
    def cancelar_pendente(db: Session, solicitacao: SolicitacaoTroca) -> None:
        """Cancela uma solicitação de troca ainda pendente, a pedido de quem a enviou. Mesmo
        tratamento de AmizadeRepository.cancelar_pendente: exclusão física (não há status
        'cancelada' no CHECK constraint da tabela) com tombstone de sync para os dois lados —
        uma solicitação de troca sempre envolve remetente e destinatário."""
        for usuario_id in (solicitacao.remetente_usuario_id, solicitacao.destinatario_usuario_id):
            SyncRepository.registrar_exclusao(db, usuario_id, "solicitacao_troca", solicitacao.id)
        db.delete(solicitacao)
        db.commit()


class TokenRevogadoRepository:
    @staticmethod
    def revoke(db: Session, *, jti: str, usuario_id: int, expira_em: datetime) -> TokenRevogado:
        token = TokenRevogado(jti=jti, usuario_id=usuario_id, revogado_em=datetime.now(UTC), expira_em=expira_em)
        db.add(token)
        db.commit()
        db.refresh(token)
        return token

    @staticmethod
    def is_revoked(db: Session, jti: str) -> bool:
        return db.scalar(select(TokenRevogado.id).where(TokenRevogado.jti == jti)) is not None

    @staticmethod
    def cleanup_expired(db: Session) -> int:
        result = db.execute(delete(TokenRevogado).where(TokenRevogado.expira_em < datetime.now(UTC)))
        db.commit()
        return result.rowcount


class SyncRepository:
    """Tombstones de sincronização (ver SyncExclusao em app.models) e consultas de apoio ao
    GET /api/sync/pull. Não faz commit sozinho em registrar_exclusao — quem chama já está no
    meio de uma transação de exclusão e deve confirmar tudo junto."""

    @staticmethod
    def registrar_exclusao(
        db: Session, usuario_id: int, entidade: str, entidade_id: int, uuid_cliente: str | None = None
    ) -> None:
        db.add(
            SyncExclusao(
                usuario_id=usuario_id,
                entidade=entidade,
                entidade_id=entidade_id,
                uuid_cliente=uuid_cliente,
            )
        )

    @staticmethod
    def list_exclusoes(db: Session, usuario_id: int) -> list[SyncExclusao]:
        """Retorna todos os tombstones do usuário — o filtro por `since` é feito em
        SyncService.pull (em Python, normalizado para UTC; ver _as_utc lá)."""
        stmt = select(SyncExclusao).where(SyncExclusao.usuario_id == usuario_id)
        return list(db.scalars(stmt.order_by(SyncExclusao.deletado_em.asc())).all())

    @staticmethod
    def foi_excluido(db: Session, usuario_id: int, entidade: str, uuid_cliente: str | None) -> bool:
        """True se já existe um tombstone para este uuid_cliente nesta entidade — usado no
        push para impedir que um dispositivo ainda com uma cópia local desatualizada
        "ressuscite" (recrie) um registro que o usuário já excluiu deliberadamente em outro
        lugar (ver SyncService._push_local/_push_plantao/_push_troca/_push_bonificacao)."""
        if not uuid_cliente:
            return False
        stmt = select(SyncExclusao).where(
            SyncExclusao.usuario_id == usuario_id,
            SyncExclusao.entidade == entidade,
            SyncExclusao.uuid_cliente == uuid_cliente,
        )
        return db.scalar(stmt) is not None
