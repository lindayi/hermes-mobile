"""Private, transactional authentication storage. Never point at a Hermes database."""
from contextlib import contextmanager
from pathlib import Path
import os
import sqlite3


class AuthStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        os.chmod(self.path, 0o600)
        with self.transaction() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS users(
                    id TEXT PRIMARY KEY, role TEXT NOT NULL, profile TEXT UNIQUE NOT NULL,
                    status TEXT NOT NULL, display_name TEXT NOT NULL, created_at REAL NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS one_owner ON users(role) WHERE role='owner';
                CREATE TABLE IF NOT EXISTS credentials(
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                    public_key BLOB NOT NULL, sign_count INTEGER NOT NULL,
                    label TEXT NOT NULL, created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions(
                    id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL,
                    user_id TEXT NOT NULL REFERENCES users(id), csrf_token TEXT NOT NULL,
                    created_at REAL NOT NULL, last_active_at REAL NOT NULL,
                    last_verified_at REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0,
                    kind TEXT NOT NULL DEFAULT 'full', label TEXT NOT NULL DEFAULT 'Browser');
                CREATE TABLE IF NOT EXISTS invites(
                    id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, label TEXT NOT NULL,
                    created_at REAL NOT NULL, expires_at REAL NOT NULL, used_at REAL,
                    revoked INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS recovery_codes(
                    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), used_at REAL);
                CREATE TABLE IF NOT EXISTS rate_limits(
                    key TEXT PRIMARY KEY, started_at REAL NOT NULL, attempts INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS audit(
                    id INTEGER PRIMARY KEY, event TEXT NOT NULL, user_id TEXT NOT NULL, created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS challenges(
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, challenge BLOB NOT NULL,
                    expires_at REAL NOT NULL, data TEXT NOT NULL);
            ''')

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('BEGIN IMMEDIATE')
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()
