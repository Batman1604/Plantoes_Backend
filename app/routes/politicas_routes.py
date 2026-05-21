from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter(tags=["politicas"])

BASE_DIR = Path(__file__).resolve().parents[2]
POLITICAS_FILE = BASE_DIR / "play_store_policies" / "index.html"


@router.get("/politicas", include_in_schema=False)
def politicas_app() -> FileResponse:
    return FileResponse(POLITICAS_FILE)
