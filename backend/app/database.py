from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://encar:encar@localhost:5432/encar"
    storage_root: str = "/storage"
    worker_poll_seconds: float = 2
    playwright_headless: bool = True
    auth_cookie_name: str = "encar_session"
    auth_csrf_cookie_name: str = "encar_csrf"
    auth_cookie_secure: bool = False
    auth_session_days: int = 7
    auth_hash_secret: str = "localhost-only-change-me"
    registration_enabled: bool = False
    allowed_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

class Base(DeclarativeBase):
    pass

def get_db():
    with SessionLocal() as db:
        yield db
