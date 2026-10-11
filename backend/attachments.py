"""Private, bounded photo storage for authenticated mobile runs."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import errno
import fcntl
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

import anyio
from PIL import Image, ImageOps, UnidentifiedImageError


MAX_INPUT_BYTES = 10 * 1024 * 1024
MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_IMAGES_PER_RUN = 4
MAX_IMAGE_PIXELS = 40_000_000
RESERVATION_BYTES = MAX_INPUT_BYTES + MAX_IMAGE_BYTES
METADATA_ROW_BYTES = 4096
UPLOAD_IO_WORKERS = 4
_IMAGE_DECODE_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix='photo-decode')
_UPLOAD_IO_EXECUTOR = ThreadPoolExecutor(max_workers=UPLOAD_IO_WORKERS, thread_name_prefix='photo-storage')
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


def _write_all(descriptor, value):
    view = memoryview(value)
    while view:
        written = os.write(descriptor, view)
        view = view[written:]


def _sync_and_close(descriptor):
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


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
    except OSError as exc:
        if exc.errno in (errno.ENOSPC, errno.EDQUOT):
            raise AttachmentError(507, 'Photo storage is full; free space or try again later.') from exc
        raise AttachmentError(415, 'This is not a supported, complete JPEG, PNG, or WebP image.') from exc
    except (UnidentifiedImageError, ValueError, SyntaxError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as exc:
        raise AttachmentError(415, 'This is not a supported, complete JPEG, PNG, or WebP image.') from exc


class AttachmentStore:
    def __init__(self, database, root, *, user_quota_bytes=128 * 1024 * 1024,
                 global_quota_bytes=512 * 1024 * 1024, min_free_bytes=1024 * 1024 * 1024,
                 user_metadata_rows=None, global_metadata_rows=None):
        if any(type(value) is not int or not 1 <= value <= 2**63 - 1
               for value in (user_quota_bytes, global_quota_bytes)):
            raise ValueError('Photo byte quotas must be positive signed-64-bit integers')
        if type(min_free_bytes) is not int or not 0 <= min_free_bytes <= 2**63 - 1:
            raise ValueError('Photo free-space reserve must be a nonnegative signed-64-bit integer')
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
        self.user_metadata_rows = (max(1, user_quota_bytes // METADATA_ROW_BYTES)
                                   if user_metadata_rows is None else user_metadata_rows)
        self.global_metadata_rows = (max(1, global_quota_bytes // METADATA_ROW_BYTES)
                                     if global_metadata_rows is None else global_metadata_rows)
        if any(type(value) is not int or value < 1
               for value in (self.user_metadata_rows, self.global_metadata_rows)):
            raise ValueError('Photo metadata row limits must be positive integers')
        self._upload_leases = {}
        self._upload_io_slots = asyncio.Semaphore(UPLOAD_IO_WORKERS)
        self._decode_slots = asyncio.Semaphore(1)
        self._orphan_iterators = [None, None]
        self._orphan_directory = 0
        self._orphan_reconciled = False
        self._orphan_scan_clean = True
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
                CREATE INDEX IF NOT EXISTS attachments_run ON attachments(run_id);
                CREATE INDEX IF NOT EXISTS attachments_cleanup_order ON attachments(created_at,id);
                CREATE TABLE IF NOT EXISTS attachment_cleanup_cursor(
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    created_at REAL NOT NULL, attachment_id TEXT NOT NULL,
                    remaining INTEGER NOT NULL DEFAULT 0);
                INSERT OR IGNORE INTO attachment_cleanup_cursor(singleton,created_at,attachment_id)
                    VALUES(1,-1,'');''')
            columns = {row[1] for row in db.execute('PRAGMA table_info(attachments)')}
            if 'position' not in columns:
                db.execute('ALTER TABLE attachments ADD COLUMN position INTEGER')
            cursor_columns = {row[1] for row in db.execute('PRAGMA table_info(attachment_cleanup_cursor)')}
            if 'remaining' not in cursor_columns:
                db.execute('ALTER TABLE attachment_cleanup_cursor ADD COLUMN remaining INTEGER NOT NULL DEFAULT 0')
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
        row = db.execute('''SELECT a.* FROM attachments a WHERE a.id=? AND a.user_id=?
            AND a.profile=? AND (a.session_id=? OR EXISTS (
                SELECT 1 FROM runs r JOIN run_history_anchors h ON h.run_id=r.id
                WHERE r.id=a.run_id AND r.user_id=a.user_id AND r.profile=a.profile
                AND ? IN (r.session_id,h.session_id,h.canonical_session_id)))''',
            (attachment_id, user['id'], user['profile'], session_id, session_id)).fetchone()
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

    def _acquire_upload_lease(self, attachment_id):
        path = self.staging / (attachment_id + '.lease')
        descriptor = None
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(descriptor)
            return None
        except OSError as exc:
            if descriptor is not None:
                os.close(descriptor)
            raise AttachmentError(503, 'Photo upload recovery is unavailable; try again later.') from exc
        return descriptor

    def _upload_is_live(self, attachment_id):
        path = self.staging / (attachment_id + '.lease')
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        except FileNotFoundError:
            return False
        except OSError:
            return False
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            return False
        finally:
            os.close(descriptor)

    def _release_upload_lease(self, attachment_id):
        # Acquisition and same-key recovery use this writer lock. Keep closing
        # the old inode and removing its pathname in the same critical section,
        # so a retry cannot acquire an inode that we subsequently unlink.
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            descriptor = self._upload_leases.pop(attachment_id, None)
            if descriptor is None:
                return
            try:
                os.close(descriptor)
            except OSError:
                pass
            self._unlink(self.staging / (attachment_id + '.lease'))
            db.commit()

    def begin(self, user, session_id, upload_key, *, worker=False):
        if not isinstance(upload_key, str) or not UPLOAD_KEY.fullmatch(upload_key):
            raise AttachmentError(422, 'A valid photo upload idempotency key is required.')
        if any(not isinstance(value, str) or not value or len(value.encode('utf-8')) > 512
               for value in (user['id'], user['profile'], session_id)):
            raise AttachmentError(422, 'Photo owner and session identifiers must be bounded.')
        self.cleanup(limit=16)
        if not self._orphan_reconciled:
            raise AttachmentError(503, 'Photo storage is reconciling; retry the upload shortly.')
        now = time.time()
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('''SELECT * FROM attachments WHERE user_id=? AND profile=?
                AND session_id=? AND upload_key=?''',
                (user['id'], user['profile'], session_id, upload_key)).fetchone()
            if existing:
                if existing['state'] == 'receiving':
                    if not worker:
                        raise AttachmentError(409, 'This photo upload is still being processed; try again shortly.')
                    try:
                        free = shutil.disk_usage(self.root).free
                    except OSError as exc:
                        raise AttachmentError(503, 'Photo storage is unavailable; try again later.') from exc
                    reserved = db.execute(
                        "SELECT COALESCE(SUM(reserved_bytes),0) FROM attachments WHERE state='receiving'"
                    ).fetchone()[0]
                    if free - reserved < self.min_free_bytes:
                        raise AttachmentError(507, 'The server is preserving required free space; photo upload is unavailable.')
                    descriptor = self._acquire_upload_lease(existing['id'])
                    if descriptor is None:
                        raise AttachmentError(409, 'This photo upload is still being processed; try again shortly.')
                    if not self._remove_receiving_files(existing['id']):
                        os.close(descriptor)
                        raise AttachmentError(503, 'Photo upload recovery is unavailable; try again later.')
                    db.execute('''UPDATE attachments SET sha256=NULL,content_type=NULL,width=NULL,height=NULL,
                        size=0,stored_name=NULL,created_at=?,expires_at=?,metadata_expires_at=?
                        WHERE id=? AND state='receiving' ''',
                        (now, now + ABANDONED_TTL, now + ABANDONED_TTL + LINKED_RETENTION, existing['id']))
                    self._upload_leases[existing['id']] = descriptor
                    try:
                        db.commit()
                    except BaseException:
                        self._upload_leases.pop(existing['id'], None)
                        os.close(descriptor)
                        self._unlink(self.staging / (existing['id'] + '.lease'))
                        raise
                    return dict(db.execute('SELECT * FROM attachments WHERE id=?',
                                           (existing['id'],)).fetchone()), True
                if existing['state'] == 'expired':
                    raise AttachmentError(410, 'This photo upload expired; select the photo again.')
                return dict(existing), False
            counts = db.execute('''SELECT COUNT(*),
                COALESCE(SUM(CASE WHEN user_id=? THEN 1 ELSE 0 END),0) FROM attachments''',
                (user['id'],)).fetchone()
            if counts[0] >= self.global_metadata_rows or counts[1] >= self.user_metadata_rows:
                raise AttachmentError(413, 'Photo metadata storage is full; retry after retention cleanup.')
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
            if free - reserved_pending - RESERVATION_BYTES - METADATA_ROW_BYTES < self.min_free_bytes:
                raise AttachmentError(507, 'The server is preserving required free space; photo upload is unavailable.')
            attachment_id = secrets.token_hex(16)
            descriptor = self._acquire_upload_lease(attachment_id) if worker else None
            try:
                db.execute('''INSERT INTO attachments(id,user_id,profile,session_id,upload_key,
                    reserved_bytes,state,created_at,expires_at,metadata_expires_at)
                    VALUES(?,?,?,?,?,?, 'receiving',?,?,?)''',
                    (attachment_id, user['id'], user['profile'], session_id, upload_key,
                     RESERVATION_BYTES, now, now + ABANDONED_TTL,
                     now + ABANDONED_TTL + LINKED_RETENTION))
                db.commit()
            except BaseException:
                if descriptor is not None:
                    os.close(descriptor)
                    self._unlink(self.staging / (attachment_id + '.lease'))
                raise
            if descriptor is not None:
                self._upload_leases[attachment_id] = descriptor
            return dict(db.execute('SELECT * FROM attachments WHERE id=?', (attachment_id,)).fetchone()), True

    async def _upload_io(self, operation, *args, cancel_result=None, on_submit=None,
                         executor=None):
        slots = self._decode_slots if executor is _IMAGE_DECODE_EXECUTOR else self._upload_io_slots
        async with slots:
            future = asyncio.get_running_loop().run_in_executor(
                executor or _UPLOAD_IO_EXECUTOR, operation, *args)
            if on_submit is not None:
                on_submit()
            try:
                return await asyncio.shield(future)
            except asyncio.CancelledError:
                # A cancelled waiter still owns the actual worker and its result.
                with anyio.CancelScope(shield=True):
                    while not future.done():
                        try:
                            await asyncio.shield(future)
                        except asyncio.CancelledError:
                            continue
                        except BaseException:
                            break
                try:
                    result = future.result()
                except BaseException:
                    pass
                else:
                    if cancel_result is not None:
                        cancel_result(result)
                raise

    def _publish_upload(self, attachment_id, user_id, stage, digest, normalized,
                        content_type, width, height, suffix):
        final_name = attachment_id + '.' + suffix
        temporary = self.objects / (attachment_id + '.tmp')
        final_path = self.objects / final_name
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, 'O_NOFOLLOW', 0)
        out_fd = os.open(temporary, flags, 0o600)
        try:
            _write_all(out_fd, normalized)
            os.fsync(out_fd)
        finally:
            os.close(out_fd)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT state FROM attachments WHERE id=? AND user_id=?',
                                 (attachment_id, user_id)).fetchone()
            if current is None or current['state'] != 'receiving':
                raise AttachmentError(409, 'This photo upload reservation is no longer active.')
            os.replace(temporary, final_path)
            os.unlink(stage)
            now = time.time()
            db.execute('''UPDATE attachments SET sha256=?,content_type=?,width=?,height=?,
                size=?,reserved_bytes=0,stored_name=?,state='pending',expires_at=?,
                metadata_expires_at=? WHERE id=? AND state='receiving' ''',
                (digest, content_type, width, height, len(normalized), final_name,
                 now + ABANDONED_TTL, now + ABANDONED_TTL + LINKED_RETENTION,
                 attachment_id))
            db.commit()
            completed = db.execute('SELECT * FROM attachments WHERE id=?',
                                   (attachment_id,)).fetchone()
            return _metadata(completed)

    async def upload(self, user, session_id, upload_key, chunks):
        reservation = {}

        def begin_upload():
            reservation['result'] = self.begin(user, session_id, upload_key, worker=True)
            return reservation['result']

        try:
            row, fresh = await self._upload_io(begin_upload)
        except asyncio.CancelledError:
            result = reservation.get('result')
            if result and result[1]:
                attachment_id = result[0]['id']
                with anyio.CancelScope(shield=True):
                    try:
                        await self._upload_io(self.abort, attachment_id)
                    finally:
                        await self._upload_io(self._release_upload_lease, attachment_id)
            raise
        attachment_id = row['id']
        digest = hashlib.sha256()
        total = 0
        stage = self.staging / (attachment_id + '.part')
        descriptor = None
        try:
            if fresh:
                flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
                flags |= getattr(os, 'O_NOFOLLOW', 0)
                descriptor = await self._upload_io(
                    os.open, stage, flags, 0o600, cancel_result=os.close)
            async for chunk in chunks:
                if not isinstance(chunk, bytes):
                    raise AttachmentError(400, 'The photo upload body is invalid.')
                total += len(chunk)
                if total > MAX_INPUT_BYTES:
                    raise AttachmentError(413, 'Each original photo must be 10 MiB or smaller.')
                digest.update(chunk)
                if descriptor is not None:
                    await self._upload_io(_write_all, descriptor, chunk)
            if not total:
                raise AttachmentError(422, 'Choose a photo before uploading.')
            if descriptor is None:
                if row['sha256'] != digest.hexdigest():
                    raise AttachmentError(409, 'This upload key already belongs to different photo data.')
                if row['state'] not in ('pending', 'bound'):
                    raise AttachmentError(410, 'This photo upload expired; select the photo again.')
                return _metadata(row)
            def transfer_descriptor():
                nonlocal descriptor
                descriptor = None

            await self._upload_io(
                _sync_and_close, descriptor, on_submit=transfer_descriptor)
            normalized, content_type, width, height, suffix = await self._upload_io(
                _normalize_image, stage, executor=_IMAGE_DECODE_EXECUTOR)
            return await self._upload_io(
                self._publish_upload, attachment_id, user['id'], stage, digest.hexdigest(),
                normalized, content_type, width, height, suffix)
        except OSError as exc:
            with anyio.CancelScope(shield=True):
                if descriptor is not None:
                    await self._upload_io(os.close, descriptor)
                if fresh:
                    await self._upload_io(self.abort, attachment_id)
            if exc.errno in (errno.ENOSPC, errno.EDQUOT):
                raise AttachmentError(507, 'Photo storage is full; free space or try again later.') from exc
            raise AttachmentError(503, 'Photo storage is unavailable; try again later.') from exc
        except BaseException:
            with anyio.CancelScope(shield=True):
                if descriptor is not None:
                    await self._upload_io(os.close, descriptor)
                if fresh:
                    await self._upload_io(self.abort, attachment_id)
            raise
        finally:
            if fresh:
                with anyio.CancelScope(shield=True):
                    await self._upload_io(self._release_upload_lease, attachment_id)

    def abort(self, attachment_id):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT stored_name FROM attachments WHERE id=? AND state='receiving'",
                             (attachment_id,)).fetchone()
            if row is not None:
                if self._remove_receiving_files(attachment_id):
                    db.execute("DELETE FROM attachments WHERE id=? AND state='receiving'", (attachment_id,))
            db.commit()

    def _remove_receiving_files(self, attachment_id):
        if not ATTACHMENT_ID.fullmatch(attachment_id):
            return False
        paths = [self.staging / (attachment_id + '.part'),
                 self.objects / (attachment_id + '.tmp'),
                 *(self.objects / (attachment_id + '.' + suffix) for suffix in ('jpg', 'png', 'webp'))]
        try:
            for path in paths:
                if not self._unlink(path):
                    return False
        except OSError:
            return False
        return True

    @staticmethod
    def _unlink(path):
        try:
            path.lstat()
        except FileNotFoundError:
            return True
        if path.is_symlink() or not path.is_file():
            return False
        path.unlink()
        return True

    def _unlink_if_unread(self, attachment_id, name):
        if name not in (attachment_id + '.jpg', attachment_id + '.png',
                        attachment_id + '.webp'):
            raise AttachmentError(410, 'This photo is unavailable; its message text is still available.')
        path = self.objects / name
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        except FileNotFoundError:
            return True
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            self._unlink(path)
            return not path.exists()
        finally:
            os.close(descriptor)

    def metadata(self, user, session_id, attachment_id):
        with self.connection() as db:
            row = self._owned_row(db, user, session_id, attachment_id)
            if (row['state'] in ('expired', 'releasing') or not row['stored_name']
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
            db.execute('BEGIN IMMEDIATE')
            row = self._owned_row(db, user, session_id, attachment_id)
            if (row['state'] in ('expired', 'releasing') or not row['stored_name']
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
            try:
                fcntl.flock(descriptor, fcntl.LOCK_SH)
                info = os.fstat(descriptor)
                current = self._owned_row(db, user, session_id, attachment_id)
                if (current['state'] not in ('pending', 'bound') or not current['stored_name']
                        or current['stored_name'] != name or current['size'] != row['size']
                        or (current['expires_at'] <= time.time()
                            and (current['state'] != 'bound'
                                 or not self._run_pinned(db, current['run_id'])))):
                    raise AttachmentError(410, 'This photo is unavailable; its message text is still available.')
                if (not stat.S_ISREG(info.st_mode) or info.st_size != row['size']
                        or info.st_size > MAX_IMAGE_BYTES):
                    raise AttachmentError(410, 'This photo is unavailable; its message text is still available.')
                db.commit()
                return descriptor, row['content_type'], row['size']
            except BaseException:
                db.rollback()
                try:
                    os.close(descriptor)
                except OSError:
                    pass
                raise

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

    def run_image_sizes(self, user_id, profile, session_id, attachment_ids):
        sizes = []
        now = time.time()
        with self.connection() as db:
            for attachment_id in attachment_ids:
                row = db.execute('''SELECT content_type,size,stored_name,state,expires_at
                    FROM attachments WHERE id=? AND user_id=? AND profile=? AND session_id=?''',
                    (attachment_id, user_id, profile, session_id)).fetchone()
                if (row is None or row['state'] != 'pending' or row['expires_at'] <= now
                        or row['stored_name'] not in
                        (attachment_id + '.jpg', attachment_id + '.png', attachment_id + '.webp')
                        or type(row['size']) is not int or not 0 < row['size'] <= MAX_IMAGE_BYTES):
                    raise AttachmentError(409, 'A selected photo is no longer available for this Session.')
                sizes.append((row['content_type'], row['size']))
        return sizes

    def run_images(self, user_id, profile, session_id, run_id, attachment_ids):
        images = []
        with self.connection() as db:
            for attachment_id in attachment_ids:
                db.execute('BEGIN IMMEDIATE')
                descriptor = None
                try:
                    row = db.execute('''SELECT * FROM attachments WHERE id=? AND user_id=? AND profile=?
                        AND session_id=? AND run_id=? AND state='bound' ''',
                        (attachment_id, user_id, profile, session_id, run_id)).fetchone()
                    if (row is None or row['stored_name'] not in
                            (attachment_id + '.jpg', attachment_id + '.png', attachment_id + '.webp')
                            or (row['expires_at'] <= time.time()
                                and not self._run_pinned(db, row['run_id']))):
                        raise AttachmentError(410, 'A photo expired before the native run could use it.')
                    path = self.objects / row['stored_name']
                    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
                    try:
                        descriptor = os.open(path, flags)
                    except OSError as exc:
                        raise AttachmentError(410, 'A selected photo is no longer available.') from exc
                    fcntl.flock(descriptor, fcntl.LOCK_SH)
                    info = os.fstat(descriptor)
                    if not stat.S_ISREG(info.st_mode) or info.st_size != row['size'] or info.st_size > MAX_IMAGE_BYTES:
                        raise AttachmentError(410, 'A selected photo is no longer available.')
                    current = db.execute('''SELECT state,stored_name,size,expires_at FROM attachments
                        WHERE id=? AND user_id=? AND profile=? AND session_id=? AND run_id=?''',
                        (attachment_id, user_id, profile, session_id, run_id)).fetchone()
                    if (current is None or current['state'] != 'bound'
                            or current['stored_name'] != row['stored_name']
                            or current['size'] != row['size']
                            or (current['expires_at'] <= time.time()
                                and not self._run_pinned(db, run_id))):
                        raise AttachmentError(410, 'A selected photo is no longer available.')
                    db.commit()
                except BaseException:
                    db.rollback()
                    if descriptor is not None:
                        os.close(descriptor)
                    raise
                try:
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

    def cleanup(self, *, limit=64, now=None, rescan=False):
        now = time.time() if now is None else now
        removed = 0
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            saved_cursor = db.execute('SELECT created_at,attachment_id,remaining '
                                     'FROM attachment_cleanup_cursor WHERE singleton=1').fetchone()
            cursor, remaining = tuple(saved_cursor[:2]), saved_cursor['remaining']
            if remaining <= 0:
                cursor = (-1, '')
                remaining = max(1, db.execute('SELECT COUNT(*) FROM attachments').fetchone()[0])
            query = '''SELECT a.* FROM attachments a LEFT JOIN runs r ON r.id=a.run_id
                WHERE ((a.state='receiving' AND a.created_at<=?)
                   OR (a.state='pending' AND a.expires_at<=?)
                   OR (a.state='releasing')
                   OR (a.state='bound' AND a.expires_at<=?
                       AND r.status IN ('completed','failed','cancelled'))
                   OR (a.state='expired' AND a.run_id IS NULL AND a.metadata_expires_at<=?))
                   AND (a.created_at,a.id) > (?,?)
                ORDER BY a.created_at,a.id LIMIT ?'''
            params = (now - ABANDONED_TTL, now, now, now)
            batch_size = max(1, min(int(limit), 256))
            rows = db.execute(query, (*params, *cursor, min(batch_size, remaining))).fetchall()
            if not rows:
                remaining = max(1, db.execute('SELECT COUNT(*) FROM attachments').fetchone()[0])
                rows = db.execute(query, (*params, -1, '', min(batch_size, remaining))).fetchall()
            if rows:
                db.execute('UPDATE attachment_cleanup_cursor SET created_at=?,attachment_id=?,remaining=? '
                          'WHERE singleton=1',
                          (rows[-1]['created_at'], rows[-1]['id'], remaining - len(rows)))
            for row in rows:
                if row['state'] == 'receiving':
                    try:
                        lease = self._acquire_upload_lease(row['id'])
                    except AttachmentError:
                        continue
                    if lease is None:
                        continue
                    try:
                        if not self._remove_receiving_files(row['id']):
                            continue
                        db.execute('DELETE FROM attachments WHERE id=?', (row['id'],))
                        removed += 1
                    finally:
                        os.close(lease)
                    continue
                if row['stored_name']:
                    if row['stored_name'] not in (
                            row['id'] + '.jpg', row['id'] + '.png', row['id'] + '.webp'):
                        continue
                    try:
                        removed_image = self._unlink_if_unread(row['id'], row['stored_name'])
                    except OSError:
                        continue
                    if not removed_image:
                        db.execute("UPDATE attachments SET state='releasing' WHERE id=?",
                                   (row['id'],))
                        continue
                self._unlink(self.staging / (row['id'] + '.part'))
                self._unlink(self.objects / (row['id'] + '.tmp'))
                if row['state'] == 'expired' or row['state'] == 'receiving':
                    db.execute('DELETE FROM attachments WHERE id=?', (row['id'],))
                else:
                    db.execute('''UPDATE attachments SET state='expired',size=0,reserved_bytes=0,
                        stored_name=NULL WHERE id=?''', (row['id'],))
                removed += 1
            db.commit()
        if rescan and self._orphan_directory >= 2:
            self._orphan_iterators = [None, None]
            self._orphan_directory = 0
            self._orphan_scan_clean = True
        self._cleanup_orphans(max(1, min(int(limit), 256)))
        return removed

    def _cleanup_orphans(self, limit):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            scanned = 0
            directories = (self.objects, self.staging)
            while scanned < limit and self._orphan_directory < len(directories):
                index = self._orphan_directory
                iterator = self._orphan_iterators[index]
                if iterator is None:
                    iterator = self._orphan_iterators[index] = directories[index].iterdir()
                try:
                    path = next(iterator)
                except StopIteration:
                    self._orphan_iterators[index] = None
                    self._orphan_directory += 1
                    continue
                scanned += 1
                if index == 0 and path.name.endswith(('.jpg', '.png', '.webp')):
                    known = db.execute('SELECT 1 FROM attachments WHERE stored_name=? LIMIT 1',
                                       (path.name,)).fetchone()
                elif path.name.endswith(('.tmp', '.lease', '.part')):
                    attachment_id, suffix = path.name.rsplit('.', 1)
                    known = (db.execute("SELECT 1 FROM attachments WHERE id=? AND state='receiving' LIMIT 1",
                                        (attachment_id,)).fetchone()
                             if suffix in ('tmp', 'lease', 'part') else None)
                else:
                    known = None
                if not known:
                    self._orphan_reconciled = False
                    if not self._unlink(path):
                        self._orphan_scan_clean = False
            if self._orphan_directory == len(directories):
                self._orphan_reconciled = self._orphan_scan_clean
            db.commit()

    def metadata_for_history(self, user, session_id, attachment_ids):
        metadata = self.metadata_for_history_batch(user, session_id, attachment_ids)
        return [metadata.get(attachment_id, {'id': attachment_id, 'status': 'expired'})
                for attachment_id in attachment_ids[:MAX_IMAGES_PER_RUN]]

    def metadata_for_history_batch(self, user, session_id, attachment_ids):
        ids = list(dict.fromkeys(
            attachment_id for attachment_id in attachment_ids
            if isinstance(attachment_id, str) and ATTACHMENT_ID.fullmatch(attachment_id)))
        result = {}
        now = time.time()
        with self.connection() as db:
            for offset in range(0, len(ids), 250):
                batch = ids[offset:offset + 250]
                rows = db.execute('''SELECT a.*,r.status AS run_status FROM attachments a
                    LEFT JOIN runs r ON r.id=a.run_id
                    WHERE a.id IN (''' + ','.join('?' for _ in batch) + ''')
                    AND a.user_id=? AND a.profile=? AND (a.session_id=? OR EXISTS (
                        SELECT 1 FROM runs alias_run JOIN run_history_anchors h ON h.run_id=alias_run.id
                        WHERE alias_run.id=a.run_id AND alias_run.user_id=a.user_id
                        AND alias_run.profile=a.profile
                        AND ? IN (alias_run.session_id,h.session_id,h.canonical_session_id)))''',
                    (*batch, user['id'], user['profile'], session_id, session_id)).fetchall()
                for row in rows:
                    pinned = bool(row['run_id']) and (
                        row['run_status'] is None or row['run_status'] not in TERMINAL_RUNS)
                    if (row['state'] in ('expired', 'releasing')
                            or (row['expires_at'] <= now
                                and (row['state'] != 'bound' or not pinned))):
                        result[row['id']] = {'id': row['id'], 'status': 'expired'}
                    else:
                        result[row['id']] = _metadata(row)
        for attachment_id in ids:
            result.setdefault(attachment_id, {'id': attachment_id, 'status': 'expired'})
        return result

    def release(self, user, session_id, attachment_id):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self._owned_row(db, user, session_id, attachment_id)
            if row['state'] == 'bound':
                raise AttachmentError(409, 'A photo already sent with a message cannot be removed here.')
            if row['state'] == 'pending':
                if not row['stored_name'] or row['stored_name'] not in (
                        attachment_id + '.jpg', attachment_id + '.png',
                        attachment_id + '.webp'):
                    raise AttachmentError(410, 'This photo is unavailable; its message text is still available.')
                if self._unlink_if_unread(attachment_id, row['stored_name']):
                    db.execute('''UPDATE attachments SET state='expired',size=0,reserved_bytes=0,
                        stored_name=NULL,metadata_expires_at=? WHERE id=?''',
                        (time.time() + LINKED_RETENTION, attachment_id))
                else:
                    db.execute("UPDATE attachments SET state='releasing' WHERE id=?",
                               (attachment_id,))
            db.commit()
