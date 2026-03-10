from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.repositories.plantao_repository import PlantaoRepository, UserRepository
from app.schemas.plantao_schema import (
    PlantaoCreate,
    PlantaoResponse,
    UserCreate,
    UserResponse,
)

router = APIRouter(prefix="/api", tags=["plantoes"])


@router.post("/usuarios", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def criar_usuario(payload: UserCreate, db: Session = Depends(get_db)):
    try:
        return UserRepository.create(db, payload)
    except IntegrityError as exc:
        db.rollback()
        error_message = str(exc.orig).lower() if exc.orig else str(exc).lower()

        if "email" in error_message and ("unique" in error_message or "duplicate" in error_message):
            raise HTTPException(status_code=400, detail="Email já cadastrado") from exc

        raise HTTPException(status_code=400, detail="Dados do usuário inválidos") from exc


@router.get("/usuarios", response_model=list[UserResponse])
def listar_usuarios(db: Session = Depends(get_db)):
    return UserRepository.list_all(db)


@router.post("/plantoes", response_model=PlantaoResponse, status_code=status.HTTP_201_CREATED)
def criar_plantao(payload: PlantaoCreate, db: Session = Depends(get_db)):
    usuarios = {u.id for u in UserRepository.list_all(db)}
    if payload.usuario_id not in usuarios:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")

    return PlantaoRepository.create(db, payload)


@router.get("/plantoes", response_model=list[PlantaoResponse])
def listar_plantoes(usuario_id: int | None = None, db: Session = Depends(get_db)):
    if usuario_id is not None:
        return PlantaoRepository.list_by_user(db, usuario_id)
    return PlantaoRepository.list_all(db)
