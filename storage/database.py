"""Short SQLite transactions and versioned, atomic schema initialization."""

from contextlib import contextmanager
from datetime import UTC, datetime
import os
from pathlib import Path
import sqlite3


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = REPOSITORY_ROOT / "data" / "license_manager.sqlite3"


class StorageError(RuntimeError):
    """Safe persistence error; never includes SQL, tokens, or raw exceptions."""


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


class Database:
    def __init__(self, path: Path | str | None = None, *, clock=None):
        configured = path if path is not None else os.getenv("LICENSE_DB_PATH", "").strip()
        selected = Path(configured).expanduser() if configured else DEFAULT_DB_PATH
        self.path = selected if selected.is_absolute() else REPOSITORY_ROOT / selected
        self.clock = clock or (lambda: datetime.now(UTC))

    def now(self):
        return self.clock().astimezone(UTC)

    def timestamp(self):
        return self.now().isoformat(timespec="microseconds").replace("+00:00", "Z")

    @contextmanager
    def connection(self, *, write=False):
        connection = None
        try:
            connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            if write:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            if write:
                connection.commit()
        except (sqlite3.Error, OSError):
            if connection is not None:
                connection.rollback()
            raise StorageError("License storage is unavailable; no unrecorded key may be delivered.") from None
        except BaseException:
            if connection is not None:
                connection.rollback()
            raise
        finally:
            if connection is not None:
                connection.close()

    def transaction(self):
        return self.connection(write=True)

    def initialize(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            raise StorageError("Unable to initialize license storage.") from None
        with self.transaction() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > 2:
                raise StorageError("Database schema is newer than this application supports.")
            for target, name in ((1, "001_initial.sql"), (2, "002_reliable_workflow.sql")):
                if version >= target:
                    continue
                migration = Path(__file__).parent / "migrations" / name
                # execute(), unlike executescript(), preserves our explicit transaction.
                for statement in migration.read_text(encoding="utf-8").split(";"):
                    if statement.strip():
                        connection.execute(statement)
