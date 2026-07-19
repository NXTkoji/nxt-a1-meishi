from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import event, text

from app.config import settings


def _ensure_dirs() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.images_path.mkdir(parents=True, exist_ok=True)
    settings.temp_path.mkdir(parents=True, exist_ok=True)


_ensure_dirs()

engine = create_async_engine(
    settings.database_url,
    echo=False,
    connect_args={"check_same_thread": False},
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def enable_wal(conn, _):
    """Enable WAL mode and foreign key enforcement on every new connection."""
    await conn.execute(text("PRAGMA journal_mode=WAL"))
    await conn.execute(text("PRAGMA foreign_keys=ON"))


# Register the startup pragma hook
from sqlalchemy import event as sa_event
sa_event.listen(engine.sync_engine, "connect", lambda conn, _: (
    conn.execute("PRAGMA journal_mode=WAL"),
    conn.execute("PRAGMA foreign_keys=ON"),
    # Background-task auto-sync (contact_sync.py) now opens a DB session per
    # card save, so concurrent writers are more common than before — wait up
    # to 5s for a lock instead of failing immediately.
    conn.execute("PRAGMA busy_timeout=5000"),
))
