from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_password, verify_password
from app.models import Local, Plantao, TokenRevogado, User
from app.schemas.plantao_schema import LocalCreate, PlantaoCreate, UserCreate, UserLogin
from app.services.pagamento_service import ajustar_fim_intervalo


class UserRepository:
    @staticmethod
    def create(db: Session, payload: UserCreate) -> User:
        user_data = payload.model_dump(exclude={"senha"})
        user = User(**user_data, senha_hash=hash_password(payload.senha))
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

    @staticmethod
    def authenticate(db: Session, payload: UserLogin) -> User | None:
        user = db.scalar(select(User).where(User.email == payload.email))
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


class LocalRepository:
    @staticmethod
    def create(db: Session, payload: LocalCreate) -> Local:
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


class PlantaoRepository:
    @staticmethod
    def create(db: Session, payload: PlantaoCreate) -> Plantao:
        plantao = Plantao(**payload.model_dump(exclude={"acao_conflito"}))
        db.add(plantao)
        db.commit()
        db.refresh(plantao)
        return plantao

    @staticmethod
    def existe_conflito(db: Session, usuario_id: int, inicio, fim) -> bool:
        plantoes = db.query(Plantao).filter(Plantao.usuario_id == usuario_id).all()
        for plantao in plantoes:
            inicio_existente = datetime.combine(plantao.data, plantao.horario_inicio)
            fim_existente = ajustar_fim_intervalo(inicio_existente, datetime.combine(plantao.data, plantao.horario_fim))
            if inicio_existente <= fim and fim_existente >= inicio:
                return True
        return False

    @staticmethod
    def get_conflito(db: Session, usuario_id: int, inicio, fim) -> Plantao | None:
        """Busca o plantão conflitante com o horário informado."""
        plantoes = db.query(Plantao).filter(Plantao.usuario_id == usuario_id).all()
        for plantao in plantoes:
            inicio_existente = datetime.combine(plantao.data, plantao.horario_inicio)
            fim_existente = ajustar_fim_intervalo(inicio_existente, datetime.combine(plantao.data, plantao.horario_fim))
            if inicio_existente <= fim and fim_existente >= inicio:
                return plantao
        return None

    @staticmethod
    def update(db: Session, plantao_id: int, **kwargs) -> Plantao | None:
        """Atualiza um plantão com os dados fornecidos."""
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
        plantao = db.scalar(select(Plantao).where(Plantao.id == plantao_id, Plantao.usuario_id == usuario_id))
        if plantao is None:
            return None

        plantao.executado = executado
        db.commit()
        db.refresh(plantao)
        return plantao

    @staticmethod
    def list_by_user(db: Session, usuario_id: int) -> list[Plantao]:
        stmt = select(Plantao).where(Plantao.usuario_id == usuario_id).order_by(Plantao.id.desc())
        return db.scalars(stmt).all()

    @staticmethod
    def list_all(db: Session) -> list[Plantao]:
        return db.scalars(select(Plantao).order_by(Plantao.id.desc())).all()


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
