from fastapi import FastAPI

from app.core.config import settings
from app.core.database import Base, engine
from app.routes.plantao_routes import router

Base.metadata.create_all(bind=engine)

app = FastAPI(title=settings.app_name)
app.include_router(router)


@app.get("/health", tags=["health"])
def health_check():
    return {"status": "ok"}
