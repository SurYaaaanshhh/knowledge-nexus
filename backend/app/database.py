from contextlib import contextmanager
from contextvars import ContextVar, Token
import sqlite3
from collections.abc import Generator

from app.config import DB_PATH, DATA_DIR

_current_user_id: ContextVar[str | None] = ContextVar("current_user_id", default=None)


def current_user_id() -> str | None:
    return _current_user_id.get()


@contextmanager
def user_data_scope(user_id: str) -> Generator[None, None, None]:
    token: Token[str | None] = _current_user_id.set(user_id)
    try:
        yield
    finally:
        _current_user_id.reset(token)


def user_data_directory(user_id: str | None = None):
    scoped_user_id = user_id or current_user_id()
    if scoped_user_id is None:
        return DATA_DIR
    return DATA_DIR / "users" / scoped_user_id


def database_path():
    user_id = current_user_id()
    if user_id is None:
        return DB_PATH
    return user_data_directory(user_id) / DB_PATH.name


def get_connection() -> sqlite3.Connection:
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                storage_name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                uploaded_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'uploaded',
                page_count INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS passages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL,
                page_number INTEGER NOT NULL,
                text TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(document_id) REFERENCES documents(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS passage_embeddings (
                passage_id INTEGER PRIMARY KEY,
                model_name TEXT NOT NULL,
                embedding_json TEXT NOT NULL,
                FOREIGN KEY(passage_id) REFERENCES passages(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL,
                subject TEXT NOT NULL,
                relation TEXT NOT NULL,
                object TEXT NOT NULL,
                topic TEXT NOT NULL,
                page_number INTEGER NOT NULL,
                evidence TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(document_id) REFERENCES documents(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS graph_nodes (
                id TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                node_type TEXT NOT NULL,
                source TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS graph_edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                target TEXT NOT NULL,
                label TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS quizzes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT NOT NULL,
                questions_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS quiz_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                quiz_id INTEGER NOT NULL,
                answers_json TEXT NOT NULL,
                score INTEGER NOT NULL,
                total INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(quiz_id) REFERENCES quizzes(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS flashcards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fact_id INTEGER NOT NULL UNIQUE,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                review_status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                FOREIGN KEY(fact_id) REFERENCES facts(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS revision_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT NOT NULL,
                mode TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS topic_progress (
                topic TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'pending',
                last_studied TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS document_updates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                new_fact_id INTEGER NOT NULL,
                existing_fact_id INTEGER NOT NULL,
                description TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(new_fact_id, existing_fact_id),
                FOREIGN KEY(new_fact_id) REFERENCES facts(id),
                FOREIGN KEY(existing_fact_id) REFERENCES facts(id)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_passages_document ON passages(document_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_facts_topic ON facts(topic)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_quiz_attempts_quiz ON quiz_attempts(quiz_id)"
        )
