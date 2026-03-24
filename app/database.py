from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from app.config import get_settings

settings = get_settings()

engine_kwargs = {
    "pool_pre_ping": True,
    "pool_size": 5,
    "max_overflow": 10,
}

if settings.database_url.startswith("postgresql"):
    engine_kwargs["connect_args"] = {"options": "-c client_encoding=utf8"}

engine = create_engine(
    settings.database_url,
    **engine_kwargs,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
