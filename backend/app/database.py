from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .version import APP_VERSION

class Settings(BaseSettings):
    app_version: str = APP_VERSION
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


def create_database_engine(database_url: str) -> Engine:
    is_sqlite = database_url.startswith("sqlite")
    engine_options: dict = {"pool_pre_ping": True}
    if is_sqlite:
        engine_options["connect_args"] = {"check_same_thread": False}
    created_engine = create_engine(database_url, **engine_options)
    if is_sqlite:
        @event.listens_for(created_engine, "connect")
        def configure_sqlite(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()
    return created_engine


engine = create_database_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

class Base(DeclarativeBase):
    pass

def get_db():
    with SessionLocal() as db:
        yield db
