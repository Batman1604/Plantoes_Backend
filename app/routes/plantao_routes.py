from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import create_access_token, decode_access_token
from app.repositories.plantao_repository import (
    LocalRepository,
    PlantaoRepository,
    TokenRevogadoRepository,
    UserRepository,
)
from app.schemas.plantao_schema import (
    AuthResponse,
    AuthStatusResponse,
    LocalCreate,
    LocalResponse,
    PlantaoCreate,
    PlantaoResponse,
    UserCreate,
    UserLogin,
    UserResponse,
)

router = APIRouter(prefix="/api", tags=["plantoes"])
security = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: Session = Depends(get_db),
):
    if credentials is None:
        raise HTTPException(status_code=401, detail="Token de acesso ausente")

    token_payload = decode_access_token(credentials.credentials)
    if token_payload is None:
        raise HTTPException(status_code=401, detail="Token inválido ou expirado")

    user_id = int(token_payload["user_id"])
    token_id = str(token_payload["jti"])
    if TokenRevogadoRepository.is_revoked(db, token_id):
        raise HTTPException(status_code=401, detail="Token revogado")

    user = UserRepository.get_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Usuário do token não encontrado")

    return user


@router.post("/usuarios", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def criar_usuario(payload: UserCreate, db: Session = Depends(get_db)):
    try:
        return UserRepository.create(db, payload)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail="Email já cadastrado") from exc


@router.post("/auth/login", response_model=AuthResponse)
def autenticar_usuario(payload: UserLogin, db: Session = Depends(get_db)):
    user = UserRepository.authenticate(db, payload)
    if user is None:
        raise HTTPException(status_code=401, detail="Credenciais inválidas")

    access_token = create_access_token(user.id)
    return AuthResponse(autenticado=True, access_token=access_token, usuario=UserResponse.model_validate(user))


@router.get("/auth/status", response_model=AuthStatusResponse)
def status_autenticacao(current_user=Depends(get_current_user)):
    return AuthStatusResponse(autenticado=True, usuario=UserResponse.model_validate(current_user))


@router.post("/auth/logout", response_model=AuthStatusResponse)
def logout_usuario(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: Session = Depends(get_db),
):
    if credentials is None:
        raise HTTPException(status_code=401, detail="Token de acesso ausente")

    token_payload = decode_access_token(credentials.credentials)
    if token_payload is None:
        raise HTTPException(status_code=401, detail="Token inválido ou expirado")

    try:
        TokenRevogadoRepository.revoke(
            db,
            jti=str(token_payload["jti"]),
            usuario_id=int(token_payload["user_id"]),
            expira_em=token_payload["exp"] if isinstance(token_payload["exp"], datetime) else datetime.now(UTC),
        )
    except IntegrityError:
        db.rollback()
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="Falha ao processar logout") from exc

    return AuthStatusResponse(autenticado=False, usuario=None)


@router.get("/usuarios", response_model=list[UserResponse])
def listar_usuarios(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    return UserRepository.list_all(db)


@router.post("/locais", response_model=LocalResponse, status_code=status.HTTP_201_CREATED)
def criar_local(payload: LocalCreate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    if payload.usuario_id != current_user.id:
        raise HTTPException(status_code=403, detail="Não é permitido criar local para outro usuário")

    return LocalRepository.create(db, payload)


@router.get("/locais", response_model=list[LocalResponse])
def listar_locais(
    usuario_id: int | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    target_user_id = usuario_id if usuario_id is not None else current_user.id
    if target_user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Não é permitido consultar locais de outro usuário")
    return LocalRepository.list_by_user(db, target_user_id)


@router.post("/plantoes", response_model=PlantaoResponse, status_code=status.HTTP_201_CREATED)
def criar_plantao(payload: PlantaoCreate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    if payload.usuario_id != current_user.id:
        raise HTTPException(status_code=403, detail="Não é permitido criar plantão para outro usuário")

    local = LocalRepository.get_by_id(db, payload.local_id)
    if local is None:
        raise HTTPException(status_code=404, detail="Local não encontrado")
    if local.usuario_id != current_user.id:
        raise HTTPException(status_code=400, detail="Local não pertence ao usuário autenticado")

    return PlantaoRepository.create(db, payload)


@router.get("/plantoes", response_model=list[PlantaoResponse])
def listar_plantoes(
    usuario_id: int | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    target_user_id = usuario_id if usuario_id is not None else current_user.id
    if target_user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Não é permitido consultar plantões de outro usuário")
    return PlantaoRepository.list_by_user(db, target_user_id)
