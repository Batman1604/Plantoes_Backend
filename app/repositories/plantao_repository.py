from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from app.core.security import hash_password, verify_password
from app.models import Bonificacao, Financeiro, Local, Plantao, TokenRevogado, Transferencia, Troca, User, VerificacaoTelefone
from app.schemas.plantao_schema import LocalCreate, PlantaoCreate, UserCreate, UserLogin


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
        db.delete(local)
        db.commit()
        return True


class PlantaoRepository:
    @staticmethod
    def create(db: Session, payload: PlantaoCreate) -> Plantao:
        plantao = Plantao(**payload.model_dump(exclude={"acao_conflito"}))
        db.add(plantao)
        db.commit()
        db.refresh(plantao)
        return plantao

    @staticmethod
    def get_conflito(
        db: Session,
        usuario_id: int,
        inicio: datetime,
        fim: datetime,
        excluir_id: int | None = None,
    ) -> Plantao | None:
        """Verifica se há plantão conflitante no mesmo intervalo de tempo."""
        data_min = inicio.date() - timedelta(days=1)
        data_max = fim.date() + timedelta(days=1)
        stmt = select(Plantao).where(
            Plantao.usuario_id == usuario_id,
            Plantao.data_inicio >= data_min,
            Plantao.data_fim <= data_max,
            Plantao.transferido.is_(False),
        )
        if excluir_id is not None:
            stmt = stmt.where(Plantao.id != excluir_id)
        for plantao in db.scalars(stmt).all():
            inicio_existente = datetime.combine(plantao.data_inicio, plantao.horario_inicio)
            fim_existente = datetime.combine(plantao.data_fim, plantao.horario_fim)
            if inicio_existente < fim and fim_existente > inicio:
                return plantao
        return None

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
        """Retorna todos os plantões pertencentes a um usuário, excluindo transferidos."""
        stmt = (
            select(Plantao)
            .where(Plantao.usuario_id == usuario_id, Plantao.transferido.is_(False))
            .options(selectinload(Plantao.bonificacoes))
            .order_by(Plantao.id.desc())
        )
        return db.scalars(stmt).all()

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


class FinanceiroRepository:
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
    def get_by_id(db: Session, bonificacao_id: int) -> Bonificacao | None:
        return db.scalar(select(Bonificacao).where(Bonificacao.id == bonificacao_id))

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
    def get_by_id(db: Session, troca_id: int) -> Troca | None:
        return db.scalar(select(Troca).where(Troca.id == troca_id))

    @staticmethod
    def delete(db: Session, troca_id: int) -> bool:
        troca = db.scalar(select(Troca).where(Troca.id == troca_id))
        if troca is None:
            return False
        db.delete(troca)
        db.commit()
        return True


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
