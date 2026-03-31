from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_password, verify_password
from app.models import Local, Plantao, User
from app.schemas.plantao_schema import LocalCreate, PlantaoCreate, UserCreate, UserLogin


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
        plantao = Plantao(**payload.model_dump())
        db.add(plantao)
        db.commit()
        db.refresh(plantao)
        return plantao

    @staticmethod
    def list_by_user(db: Session, usuario_id: int) -> list[Plantao]:
        stmt = (
            select(Plantao)
            .join(Local, Plantao.local_id == Local.id)
            .where(Local.usuario_id == usuario_id)
            .order_by(Local.data_inicio.desc())
        )
        return db.scalars(stmt).all()

    @staticmethod
    def list_all(db: Session) -> list[Plantao]:
        return db.scalars(select(Plantao).order_by(Plantao.id.desc())).all()
