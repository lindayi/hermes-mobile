"""Private, bounded photo storage for authenticated mobile runs."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import io
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import stat
import time
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError


MAX_INPUT_BYTES = 10 * 1024 * 1024
MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_IMAGES_PER_RUN = 4
MAX_IMAGE_PIXELS = 40_000_000
RESERVATION_BYTES = MAX_INPUT_BYTES + MAX_IMAGE_BYTES
_IMAGE_DECODE_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix='photo-decode')
ABANDONED_TTL = 24 * 60 * 60
LINKED_RETENTION = 30 * 24 * 60 * 60
TERMINAL_RUNS = ('completed', 'failed', 'cancelled')
UPLOAD_KEY = re.compile(r'[A-Za-z0-9_-]{1,128}\Z')
ATTACHMENT_ID = re.compile(r'[0-9a-f]{32}\Z')
FORMATS = {
    'JPEG': ('image/jpeg', 'jpg'),
    'PNG': ('image/png', 'png'),
    'WEBP': ('image/webp', 'webp'),
}


class AttachmentError(ValueError):
    def __init__(self, status, detail):
        super().__init__(detail)
        self.status = status


def _metadata(row):
    if row is None:
        return None
    return {
        'id': row['id'],
        'content_type': row['content_type'],
        'width': row['width'],
        'height': row['height'],
        'size': row['size'],
        'created_at': row['created_at'],
        'expires_at': row['expires_at'],
        'status': row['state'],
    }


def _normalize_image(path):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(path) as probe:
                image_format = probe.format
                if image_format not in FORMATS or getattr(probe, 'n_frames', 1) != 1:
                    raise AttachmentError(415, 'Choose a single-frame JPEG, PNG, or WebP image.')
                if probe.width * probe.height > MAX_IMAGE_PIXELS:
                    raise AttachmentError(413, 'This image has too many pixels to process safely.')
                probe.verify()
            with Image.open(path) as source:
                if source.format != image_format or getattr(source, 'n_frames', 1) != 1:
                    raise AttachmentError(415, 'Choose a single-frame JPEG, PNG, or WebP image.')
                image = ImageOps.exif_transpose(source)
                image.load()
                mode = 'RGBA' if 'A' in image.getbands() or 'transparency' in image.info else 'RGB'
                image = Image.frombytes(mode, image.size, image.convert(mode).tobytes())
        if image.width * image.height > MAX_IMAGE_PIXELS:
            raise AttachmentError(413, 'This image has too many pixels to process safely.')
        image.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
        content_type, suffix = FORMATS[image_format]
        qualities = {
            'JPEG': (92, 88, 84, 80, 76, 72, 68, 64),
            'WEBP': (96, 92, 88, 84, 80, 76, 72),
        }
        for _ in range(8):
            for quality in qualities.get(image_format, (None,)):
                output = io.BytesIO()
                if image_format == 'JPEG':
                    frame = image.convert('RGB')
                    frame.save(output, format='JPEG', quality=quality, optimize=True, progressive=True)
                elif image_format == 'WEBP':
                    image.save(output, format='WEBP', quality=quality, method=6)
                else:
                    image.save(output, format='PNG', optimize=True)
                value = output.getvalue()
                if len(value) <= MAX_IMAGE_BYTES:
                    return value, content_type, image.width, image.height, suffix
            if max(image.size) <= 256:
                break
            size = tuple(max(1, round(dimension * 0.85)) for dimension in image.size)
            image = image.resize(size, Image.Resampling.LANCZOS)
        raise AttachmentError(413, 'This image cannot be compressed below 2 MiB; resize it and try again.')
    except AttachmentError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as exc:
        raise AttachmentError(415, 'This is not a supported, complete JPEG, PNG, or WebP image.') from exc


class AttachmentStore:
    def __init__(self, database, root, *, user_quota_bytes=128 * 1024 * 1024,
                 global_quota_bytes=512 * 1024 * 1024, min_free_bytes=1024 * 1024 * 1024):
        self.database = Path(database).resolve()
        self.root = Path(root)
        if (self.root.is_symlink()
                or self.root.absolute() != self.database.parent / 'attachments'):
            raise ValueError('Attachment storage must be a direct private state directory')
        self.objects = self.root / 'objects'
        self.staging = self.root / 'staging'
        self.user_quota_bytes = user_quota_bytes
        self.global_quota_bytes = global_quota_bytes
        self.min_free_bytes = min_free_bytes
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        for directory in (self.objects, self.staging):
            directory.mkdir(mode=0o700, exist_ok=True)
            if directory.is_symlink() or not directory.resolve().is_relative_to(self.root.resolve()):
                raise ValueError('Attachment storage directory is unsafe')
            directory.chmod(0o700)
        with self.connection() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS attachments(
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                profile TEXT NOT NULL,
                session_id TEXT NOT NULL,
                upload_key TEXT NOT NULL,
                sha256 TEXT,
                content_type TEXT,
                width INTEGER,
                height INTEGER,
                size INTEGER NOT NULL DEFAULT 0,
                reserved_bytes INTEGER NOT NULL DEFAULT 0,
                stored_name TEXT,
                state TEXT NOT NULL,
                run_id TEXT,
                position INTEGER,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                metadata_expires_at REAL NOT NULL,
                UNIQUE(user_id,profile,session_id,upload_key));
                CREATE INDEX IF NOT EXISTS attachments_owner_session
                    ON attachments(user_id,profile,session_id,state);
                CREATE INDEX IF NOT EXISTS attachments_run ON attachments(run_id);''')
            columns = {row[1] for row in db.execute('PRAGMA table_info(attachments)')}
            if 'position' not in columns:
                db.execute('ALTER TABLE attachments ADD COLUMN position INTEGER')
            db.commit()

    def connect(self):
        db = sqlite3.connect(self.database, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    @contextmanager
    def connection(self):
        db = self.connect()
        try:
            yield db
        finally:
            db.close()

    def _owned_row(self, db, user, session_id, attachment_id):
        if not ATTACHMENT_ID.fullmatch(attachment_id):
            raise AttachmentError(404, 'Photo attachment not found.')
        row = db.execute('''SELECT * FROM attachments WHERE id=? AND user_id=?
            AND profile=? AND session_id=?''',
            (attachment_id, user['id'], user['profile'], session_id)).fetchone()
        if row is None:
            raise AttachmentError(404, 'Photo attachment not found.')
        return row

    def _usage(self, db, user_id=None):
        clause, params = ('', ()) if user_id is None else (' WHERE user_id=?', (user_id,))
        row = db.execute('SELECT COALESCE(SUM(size+reserved_bytes),0) FROM attachments'
                         + clause + " AND state!='expired'" if clause else
                         "SELECT COALESCE(SUM(size+reserved_bytes),0) FROM attachments WHERE state!='expired'",
                         params).fetchone()
        return row[0]

    def begin(self, user, session_id, upload_key):
        if not isinstance(upload_key, str) or not UPLOAD_KEY.fullmatch(upload_key):
            raise AttachmentError(422, 'A valid photo upload idempotency key is required.')
        self.cleanup(limit=16)
        now = time.time()
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('''SELECT * FROM attachments WHERE user_id=? AND profile=?
                AND session_id=? AND upload_key=?''',
                (user['id'], user['profile'], session_id, upload_key)).fetchone()
            if existing:
                if existing['state'] == 'receiving':
                    raise AttachmentError(409, 'This photo upload is still being processed; try again shortly.')
                if existing['state'] == 'expired':
                    raise AttachmentError(410, 'This photo upload expired; select the photo again.')
                return dict(existing), False
            global_used = self._usage(db)
            user_used = self._usage(db, user['id'])
            reserved_pending = db.execute(
                "SELECT COALESCE(SUM(reserved_bytes),0) FROM attachments WHERE state='receiving'"
            ).fetchone()[0]
            try:
                free = shutil.disk_usage(self.root).free
            except OSError as exc:
                raise AttachmentError(503, 'Photo storage is unavailable; try again later.') from exc
            if (global_used + RESERVATION_BYTES > self.global_quota_bytes
                    or user_used + RESERVATION_BYTES > self.user_quota_bytes):
                raise AttachmentError(413, 'Photo storage quota is full; remove unneeded photos or try later.')
            if free - reserved_pending - RESERVATION_BYTES < self.min_free_bytes:
                raise AttachmentError(507, 'The server is preserving required free space; photo upload is unavailable.')
            attachment_id = secrets.token_hex(16)
            db.execute('''INSERT INTO attachments(id,user_id,profile,session_id,upload_key,
                reserved_bytes,state,created_at,expires_at,metadata_expires_at)
                VALUES(?,?,?,?,?,?, 'receiving',?,?,?)''',
                (attachment_id, user['id'], user['profile'], session_id, upload_key,
                 RESERVATION_BYTES, now, now + ABANDONED_TTL,
                 now + ABANDONED_TTL + LINKED_RETENTION))
            db.commit()
            return dict(db.execute('SELECT * FROM attachments WHERE id=?', (attachment_id,)).fetchone()), True

    async def upload(self, user, session_id, upload_key, chunks):
        row, fresh = self.begin(user, session_id, upload_key)
        attachment_id = row['id']
        digest = hashlib.sha256()
        total = 0
        stage = self.staging / (attachment_id + '.part')
        descriptor = None
        try:
            if fresh:
                flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
                flags |= getattr(os, 'O_NOFOLLOW', 0)
                descriptor = os.open(stage, flags, 0o600)
            async for chunk in chunks:
                if not isinstance(chunk, bytes):
                    raise AttachmentError(400, 'The photo upload body is invalid.')
                total += len(chunk)
                if total > MAX_INPUT_BYTES:
                    raise AttachmentError(413, 'Each original photo must be 10 MiB or smaller.')
                digest.update(chunk)
                if descriptor is not None:
                    view = memoryview(chunk)
                    while view:
                        written = os.write(descriptor, view)
                        view = view[written:]
            if not total:
                raise AttachmentError(422, 'Choose a photo before uploading.')
            if descriptor is None:
                if row['sha256'] != digest.hexdigest():
                    raise AttachmentError(409, 'This upload key already belongs to different photo data.')
                if row['state'] not in ('pending', 'bound'):
                    raise AttachmentError(410, 'This photo upload expired; select the photo again.')
                return _metadata(row)
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            normalized, content_type, width, height, suffix = await asyncio.get_running_loop().run_in_executor(
                _IMAGE_DECODE_EXECUTOR, _normalize_image, stage)
            final_name = attachment_id + '.' + suffix
            temporary = self.objects / (attachment_id + '.tmp')
            final_path = self.objects / final_name
            flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
            flags |= getattr(os, 'O_NOFOLLOW', 0)
            out_fd = os.open(temporary, flags, 0o600)
            try:
                view = memoryview(normalized)
                while view:
                    written = os.write(out_fd, view)
                    view = view[written:]
                os.fsync(out_fd)
            finally:
                os.close(out_fd)
            with self.connection() as db:
                db.execute('BEGIN IMMEDIATE')
                current = db.execute('SELECT state FROM attachments WHERE id=? AND user_id=?',
                                     (attachment_id, user['id'])).fetchone()
                if current is None or current['state'] != 'receiving':
                    raise AttachmentError(409, 'This photo upload reservation is no longer active.')
                os.replace(temporary, final_path)
                os.unlink(stage)
                now = time.time()
                db.execute('''UPDATE attachments SET sha256=?,content_type=?,width=?,height=?,
                    size=?,reserved_bytes=0,stored_name=?,state='pending',expires_at=?,
                    metadata_expires_at=? WHERE id=? AND state='receiving' ''',
                    (digest.hexdigest(), content_type, width, height, len(normalized),
                     final_name, now + ABANDONED_TTL,
                     now + ABANDONED_TTL + LINKED_RETENTION, attachment_id))
                db.commit()
                completed = db.execute('SELECT * FROM attachments WHERE id=?',
                                       (attachment_id,)).fetchone()
                return _metadata(completed)
        except BaseException:
            if descriptor is not None:
                os.close(descriptor)
            if fresh:
                self.abort(attachment_id)
            raise

    def abort(self, attachment_id):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT stored_name FROM attachments WHERE id=? AND state='receiving'",
                             (attachment_id,)).fetchone()
            if row is not None:
                self._unlink(self.staging / (attachment_id + '.part'))
                self._unlink(self.objects / (attachment_id + '.tmp'))
                db.execute("DELETE FROM attachments WHERE id=? AND state='receiving'", (attachment_id,))
            db.commit()

    @staticmethod
    def _unlink(path):
        try:
            info = path.lstat()
            if not path.is_symlink() and path.is_file():
                path.unlink()
        except FileNotFoundError:
            pass

    def metadata(self, user, session_id, attachment_id):
        with self.connection() as db:
            row = self._owned_row(db, user, session_id, attachment_id)
            if (row['state'] == 'expired' or not row['stored_name']
                    or (row['expires_at'] <= time.time()
                        and (row['state'] != 'bound' or not self._run_pinned(db, row['run_id'])))):
                raise AttachmentError(410, 'This photo has expired; its message text is still available.')
            return _metadata(row)

    @staticmethod
    def _run_pinned(db, run_id):
        if not run_id:
            return False
        row = db.execute('SELECT status FROM runs WHERE id=?', (run_id,)).fetchone()
        return row is None or row['status'] not in TERMINAL_RUNS

    def open_image(self, user, session_id, attachment_id):
        with self.connection() as db:
            row = self._owned_row(db, user, session_id, attachment_id)
            if (row['state'] == 'expired' or not row['stored_name']
                    or (row['expires_at'] <= time.time()
                        and (row['state'] != 'bound' or not self._run_pinned(db, row['run_id'])))):
                raise AttachmentError(410, 'This photo has expired; its message text is still available.')
            if row['state'] not in ('pending', 'bound'):
                raise AttachmentError(404, 'Photo attachment not found.')
            name = row['stored_name']
            if (not isinstance(name, str) or name not in
                    (attachment_id + '.jpg', attachment_id + '.png', attachment_id + '.webp')):
                raise AttachmentError(404, 'Photo attachment not found.')
            path = self.objects / name
            flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
            try:
                descriptor = os.open(path, flags)
            except OSError as exc:
                raise AttachmentError(410, 'This photo is unavailable; its message text is still available.') from exc
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_size != row['size'] or info.st_size > MAX_IMAGE_BYTES:
                os.close(descriptor)
                raise AttachmentError(410, 'This photo is unavailable; its message text is still available.')
            return descriptor, row['content_type'], row['size']

    def bind(self, db, user_id, profile, session_id, attachment_ids, run_id):
        if (not isinstance(attachment_ids, list) or len(attachment_ids) > MAX_IMAGES_PER_RUN
                or any(not isinstance(value, str) for value in attachment_ids)):
            raise AttachmentError(422, 'A message can include at most four photos.')
        if len(attachment_ids) != len(set(attachment_ids)):
            raise AttachmentError(422, 'The same photo cannot be attached twice.')
        if not attachment_ids:
            return []
        rows = []
        now = time.time()
        for attachment_id in attachment_ids:
            if not isinstance(attachment_id, str) or not ATTACHMENT_ID.fullmatch(attachment_id):
                raise AttachmentError(404, 'Photo attachment not found.')
            row = db.execute('''SELECT * FROM attachments WHERE id=? AND user_id=? AND profile=?
                AND session_id=?''', (attachment_id, user_id, profile, session_id)).fetchone()
            if (row is None or row['state'] not in ('pending', 'bound')
                    or (row['run_id'] is not None and row['run_id'] != run_id)
                    or (row['state'] == 'pending' and row['expires_at'] <= now)):
                raise AttachmentError(409, 'A selected photo is no longer available for this Session.')
            rows.append(row)
        for position, row in enumerate(rows):
            db.execute("UPDATE attachments SET state='bound',run_id=?,position=?,expires_at=?,metadata_expires_at=? WHERE id=?",
                       (run_id, position, row['created_at'] + LINKED_RETENTION,
                        row['created_at'] + 2 * LINKED_RETENTION, row['id']))
        return [row['id'] for row in rows]

    @staticmethod
    def run_attachment_ids(db, run_id):
        return [row[0] for row in db.execute(
            "SELECT id FROM attachments WHERE run_id=? AND state='bound' ORDER BY position,id",
            (run_id,))]

    def run_images(self, user_id, profile, session_id, run_id, attachment_ids):
        images = []
        with self.connection() as db:
            for attachment_id in attachment_ids:
                row = db.execute('''SELECT * FROM attachments WHERE id=? AND user_id=? AND profile=?
                    AND session_id=? AND run_id=? AND state='bound' ''',
                    (attachment_id, user_id, profile, session_id, run_id)).fetchone()
                if row is None or not row['stored_name'] or row['expires_at'] <= time.time():
                    raise AttachmentError(410, 'A photo expired before the native run could use it.')
                path = self.objects / row['stored_name']
                flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
                try:
                    descriptor = os.open(path, flags)
                except OSError as exc:
                    raise AttachmentError(410, 'A selected photo is no longer available.') from exc
                try:
                    info = os.fstat(descriptor)
                    if not stat.S_ISREG(info.st_mode) or info.st_size != row['size'] or info.st_size > MAX_IMAGE_BYTES:
                        raise AttachmentError(410, 'A selected photo is no longer available.')
                    with os.fdopen(descriptor, 'rb', closefd=False) as source:
                        data = source.read(MAX_IMAGE_BYTES + 1)
                finally:
                    os.close(descriptor)
                if len(data) != row['size'] or len(data) > MAX_IMAGE_BYTES:
                    raise AttachmentError(410, 'A selected photo is no longer available.')
                import base64
                encoded = base64.b64encode(data).decode('ascii')
                images.append({'type': 'image_url',
                               'image_url': {'url': f"data:{row['content_type']};base64,{encoded}"}})
        return images

    def cleanup(self, *, limit=64, now=None):
        now = time.time() if now is None else now
        removed = 0
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            rows = db.execute('''SELECT a.* FROM attachments a LEFT JOIN runs r ON r.id=a.run_id
                WHERE (a.state='receiving' AND a.created_at<=?)
                   OR (a.state='pending' AND a.expires_at<=?)
                   OR (a.state='bound' AND a.expires_at<=?
                       AND r.status IN ('completed','failed','cancelled'))
                   OR (a.state='expired' AND a.metadata_expires_at<=?)
                ORDER BY a.created_at LIMIT ?''',
                (now - ABANDONED_TTL, now, now, now, max(1, min(int(limit), 256)))).fetchall()
            for row in rows:
                if row['stored_name']:
                    self._unlink(self.objects / row['stored_name'])
                self._unlink(self.staging / (row['id'] + '.part'))
                self._unlink(self.objects / (row['id'] + '.tmp'))
                if row['state'] == 'expired' or row['state'] == 'receiving':
                    db.execute('DELETE FROM attachments WHERE id=?', (row['id'],))
                else:
                    db.execute('''UPDATE attachments SET state='expired',size=0,reserved_bytes=0,
                        stored_name=NULL WHERE id=?''', (row['id'],))
                removed += 1
            db.commit()
        self._cleanup_orphans(max(1, min(int(limit), 256)))
        return removed

    def _cleanup_orphans(self, limit):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            stored = {row[0] for row in db.execute(
                "SELECT stored_name FROM attachments WHERE stored_name IS NOT NULL")}
            receiving_stages = {row[0] + '.part' for row in db.execute(
                "SELECT id FROM attachments WHERE state='receiving'")}
            receiving_temps = {row[0] + '.tmp' for row in db.execute(
                "SELECT id FROM attachments WHERE state='receiving'")}
            scanned = 0
            for directory, known in ((self.objects, stored | receiving_temps),
                                     (self.staging, receiving_stages)):
                for path in directory.iterdir():
                    scanned += 1
                    if scanned > limit:
                        db.commit()
                        return
                    if path.name not in known:
                        self._unlink(path)
            db.commit()

    def metadata_for_history(self, user, session_id, attachment_ids):
        result = []
        with self.connection() as db:
            for attachment_id in attachment_ids[:MAX_IMAGES_PER_RUN]:
                if not isinstance(attachment_id, str) or not ATTACHMENT_ID.fullmatch(attachment_id):
                    continue
                row = db.execute('''SELECT * FROM attachments WHERE id=? AND user_id=?
                    AND profile=? AND session_id=?''',
                    (attachment_id, user['id'], user['profile'], session_id)).fetchone()
                if (row is None or row['state'] == 'expired'
                        or (row['expires_at'] <= time.time()
                            and (row['state'] != 'bound' or not self._run_pinned(db, row['run_id'])))):
                    result.append({'id': attachment_id, 'status': 'expired'})
                else:
                    result.append(_metadata(row))
        return result

    def release(self, user, session_id, attachment_id):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self._owned_row(db, user, session_id, attachment_id)
            if row['state'] == 'bound':
                raise AttachmentError(409, 'A photo already sent with a message cannot be removed here.')
            if row['state'] == 'pending':
                self._unlink(self.objects / row['stored_name'])
                db.execute('''UPDATE attachments SET state='expired',size=0,reserved_bytes=0,
                    stored_name=NULL,metadata_expires_at=? WHERE id=?''',
                    (time.time() + LINKED_RETENTION, attachment_id))
            db.commit()
