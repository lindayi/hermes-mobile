"""Durable photo admission policy shared by the bridge and its private operator."""
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import stat


class PhotoPolicyError(ValueError):
    pass


class PhotosDisabled(PhotoPolicyError):
    pass


class PhotoPolicy:
    TABLE = 'photo_admission_policy'

    def __init__(self, database):
        self.database = Path(database).absolute()

    def connect(self, *, read_only=False):
        if self.database.is_symlink() or not self.database.is_file():
            raise PhotoPolicyError('Photo policy database is unavailable or unsafe.')
        try:
            if read_only:
                return sqlite3.connect(
                    self.database.as_uri() + '?mode=ro', uri=True, timeout=10)
            return sqlite3.connect(self.database, timeout=10)
        except sqlite3.Error as exc:
            raise PhotoPolicyError('Photo policy database is unavailable.') from exc

    @classmethod
    def _enabled(cls, db):
        try:
            columns = [row[1] for row in db.execute(
                f'PRAGMA table_info({cls.TABLE})')]
            rows = db.execute(
                f'SELECT singleton,enabled FROM {cls.TABLE}').fetchall()
        except sqlite3.Error as exc:
            raise PhotoPolicyError('Photo policy is missing or malformed.') from exc
        if (columns != ['singleton', 'enabled'] or len(rows) != 1
                or type(rows[0][0]) is not int or rows[0][0] != 1
                or type(rows[0][1]) is not int or rows[0][1] not in (0, 1)):
            raise PhotoPolicyError('Photo policy is missing or malformed.')
        return rows[0][1] == 1

    def initialize(self, enabled):
        if type(enabled) is not bool:
            raise PhotoPolicyError('Photo feature setting must be boolean.')
        if self.database.is_symlink():
            raise PhotoPolicyError('Photo policy database must not be a symlink.')
        with closing(sqlite3.connect(self.database, timeout=10)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            exists = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (self.TABLE,)).fetchone()
            if exists is None:
                db.execute(f'''CREATE TABLE {self.TABLE}(
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    enabled INTEGER NOT NULL CHECK(enabled IN (0,1)))''')
                db.execute(f'INSERT INTO {self.TABLE} VALUES(1,?)', (int(enabled),))
            self._enabled(db)
        os.chmod(self.database, stat.S_IRUSR | stat.S_IWUSR)

    def enabled(self):
        with closing(self.connect(read_only=True)) as db:
            return self._enabled(db)

    def require_enabled(self, db):
        if not self._enabled(db):
            raise PhotosDisabled(
                'New photo-bearing runs are disabled; retry existing runs or send text only.')

    def set_enabled(self, enabled):
        if type(enabled) is not bool:
            raise PhotoPolicyError('Photo policy must be a boolean.')
        try:
            with closing(self.connect()) as db, db:
                db.execute('BEGIN IMMEDIATE')
                self._enabled(db)
                updated = db.execute(
                    f'UPDATE {self.TABLE} SET enabled=? WHERE singleton=1',
                    (int(enabled),)).rowcount
                if updated != 1:
                    raise PhotoPolicyError('Photo policy update was not applied.')
                db.commit()
        except Exception as exc:
            try:
                observed = self.enabled()
            except Exception as read_error:
                raise PhotoPolicyError(
                    'Photo policy write outcome is uncertain; inspect status before retrying.') from read_error
            if observed is enabled:
                return observed
            raise PhotoPolicyError(
                'Photo policy change was not confirmed; inspect status before retrying.') from exc
        observed = self.enabled()
        if observed is not enabled:
            raise PhotoPolicyError(
                'Photo policy readback did not match; inspect status before retrying.')
        return observed

    def status(self):
        enabled = self.enabled()
        return {'photos_enabled': enabled, 'persisted': enabled, 'live': enabled}
