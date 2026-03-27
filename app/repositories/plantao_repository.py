from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_password, verify_password
from app.models import Plantao, User
from app.schemas.plantao_schema import PlantaoCreate, UserCreate, UserLogin


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
        stmt = select(Plantao).where(Plantao.usuario_id == usuario_id).order_by(Plantao.data_inicio.desc())
        return db.scalars(stmt).all()

    @staticmethod
    def list_all(db: Session) -> list[Plantao]:
        return db.scalars(select(Plantao).order_by(Plantao.data_inicio.desc())).all()
