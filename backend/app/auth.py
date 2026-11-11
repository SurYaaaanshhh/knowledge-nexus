from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import secrets
import shutil
import sqlite3
import uuid

from fastapi import Cookie, HTTPException, status

from app import database

SESSION_COOKIE = "knowledge_nexus_session"
SESSION_DURATION = timedelta(days=14)
PASSWORD_ITERATIONS = 310_000
MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_DURATION = timedelta(minutes=15)
_DUMMY_SALT = bytes.fromhex("42" * 16)
_DUMMY_PASSWORD_HASH = hashlib.pbkdf2_hmac(
    "sha256", b"not-a-real-password", _DUMMY_SALT, PASSWORD_ITERATIONS
)


def _auth_connection() -> sqlite3.Connection:
    data_dir = database.DATA_DIR
    data_dir.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(data_dir / "auth.db")
    connection.row_factory = sqlite3.Row
    return connection


def init_auth_db() -> None:
    with _auth_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                failed_login_attempts INTEGER NOT NULL DEFAULT 0,
                locked_until INTEGER,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                expires_at INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )


def _password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PASSWORD_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}"


def _verify_password(password: str, encoded_hash: str | None) -> bool:
    if encoded_hash is None:
        salt = _DUMMY_SALT
        expected = _DUMMY_PASSWORD_HASH
        iterations = PASSWORD_ITERATIONS
    else:
        algorithm, iteration_text, salt_text, digest_text = encoded_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iteration_text)
        salt = bytes.fromhex(salt_text)
        expected = bytes.fromhex(digest_text)
    actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return hmac.compare_digest(actual, expected)


def _create_session(connection: sqlite3.Connection, user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    expires_at = int((datetime.now(timezone.utc) + SESSION_DURATION).timestamp())
    connection.execute(
        "INSERT INTO sessions (token_hash, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
        (
            hashlib.sha256(token.encode()).hexdigest(),
            user_id,
            expires_at,
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ),
    )
    return token


def _public_user(row: sqlite3.Row) -> dict[str, str]:
    return {"id": row["id"], "email": row["email"]}


def register_user(email: str, password: str) -> tuple[dict[str, str], str]:
    normalized_email = email.strip().casefold()
    password_hash = _password_hash(password)
    user_id = uuid.uuid4().hex

    with _auth_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT id FROM users WHERE email = ?", (normalized_email,)
        ).fetchone()
        if existing:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An account with this email already exists.")

        first_user = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        connection.execute(
            "INSERT INTO users (id, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
            (
                user_id,
                normalized_email,
                password_hash,
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        )
        token = _create_session(connection, user_id)
        connection.execute(
            "INSERT OR REPLACE INTO auth_metadata (key, value) VALUES ('legacy_data_migrated', 'true')"
        )

    tenant_dir = database.user_data_directory(user_id)
    tenant_dir.mkdir(parents=True, exist_ok=True)
    tenant_db = tenant_dir / database.DB_PATH.name
    if first_user and database.DB_PATH.exists() and not tenant_db.exists():
        shutil.copy2(database.DB_PATH, tenant_db)

    with database.user_data_scope(user_id):
        database.init_db()

    return {"id": user_id, "email": normalized_email}, token


def authenticate_user(email: str, password: str) -> tuple[dict[str, str], str]:
    normalized_email = email.strip().casefold()
    now = int(datetime.now(timezone.utc).timestamp())
    failure_status: int | None = None
    result: tuple[dict[str, str], str] | None = None

    with _auth_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM users WHERE email = ?", (normalized_email,)
        ).fetchone()
        if row and row["locked_until"] and row["locked_until"] > now:
            failure_status = status.HTTP_429_TOO_MANY_REQUESTS
        elif not _verify_password(password, row["password_hash"] if row else None):
            if row:
                attempts = row["failed_login_attempts"] + 1
                locked_until = (
                    now + int(LOCKOUT_DURATION.total_seconds())
                    if attempts >= MAX_LOGIN_ATTEMPTS
                    else None
                )
                connection.execute(
                    "UPDATE users SET failed_login_attempts = ?, locked_until = ? WHERE id = ?",
                    (0 if locked_until else attempts, locked_until, row["id"]),
                )
            failure_status = status.HTTP_401_UNAUTHORIZED
        else:
            connection.execute(
                "UPDATE users SET failed_login_attempts = 0, locked_until = NULL WHERE id = ?",
                (row["id"],),
            )
            token = _create_session(connection, row["id"])
            result = (_public_user(row), token)

    if failure_status == status.HTTP_429_TOO_MANY_REQUESTS:
        raise HTTPException(
            status_code=failure_status,
            detail="Too many failed sign-in attempts. Try again in 15 minutes.",
        )
    if result is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password.")
    return result


def get_session_user(token: str | None) -> dict[str, str] | None:
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    now = int(datetime.now(timezone.utc).timestamp())
    with _auth_connection() as connection:
        row = connection.execute(
            """
            SELECT users.id, users.email
            FROM sessions JOIN users ON users.id = sessions.user_id
            WHERE sessions.token_hash = ? AND sessions.expires_at > ?
            """,
            (token_hash, now),
        ).fetchone()
    return _public_user(row) if row else None


def revoke_session(token: str | None) -> None:
    if not token:
        return
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with _auth_connection() as connection:
        connection.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))


async def require_current_user(
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
):
    user = get_session_user(session_token)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Please sign in to continue.",
        )

    with database.user_data_scope(user["id"]):
        yield user
