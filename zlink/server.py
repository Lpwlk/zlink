"""zlink server: accepts clients and serves each session in its own thread."""

from __future__ import annotations

import hashlib
import itertools
import os
import platform
import shutil
import socket
import threading
import time
import uuid
from collections import deque
from pathlib import Path, PurePosixPath
from typing import Callable

from rich.table import Table
from rich.text import Text

from . import __version__, sounds, ui
from .cards import server_card
from .auth import AuthError, UserStore, validate_username
from .files import display_path, human_size, list_dir, safe_filename, safe_relpath, storage_stats, unique_path
from .protocol import (CHUNK_SIZE, PROTOCOL_VERSION, Connection, EventKind, Message, MsgType,
                       ProtocolError)
from .ui import STATUSES, console, format_duration, get_logger

MAX_AUTH_ATTEMPTS = 3
MAX_CHAT_LENGTH = 2000
MAX_STATUS_LENGTH = 80
HISTORY_SIZE = 100
MAX_PING_PAYLOAD = 1024 * 1024
MAX_READ_SIZE = 1024 * 1024


class RequestError(Exception):
    """Raised by a request handler to answer the client with an ERROR response."""


def cpu_temperature() -> float | None:
    try:
        return int(Path('/sys/class/thermal/thermal_zone0/temp').read_text()) / 1000
    except (OSError, ValueError):
        return None


class Session:
    """One connected client, as seen by the server."""

    def __init__(self, conn: Connection, index: int):
        self.conn = conn
        self.index = index
        self.id = uuid.uuid4().hex[:8]
        self.color = ui.user_color(index)
        self.address = conn.peer
        self.username: str | None = None
        self.registered = False
        self.status = 'online'
        self.status_message: str | None = None
        self.connected_at = self.last_activity = time.time()
        self.stats = {'messages': 0, 'uploads': 0, 'downloads': 0, 'bytes_up': 0, 'bytes_down': 0}
        self.closed = False

    def push(self, kind: EventKind, **fields) -> None:
        try:
            self.conn.send(MsgType.EVENT, kind=kind.value, **fields)
        except OSError:
            pass  # the session thread notices the dead socket and cleans up

    def describe(self) -> dict:
        now = time.time()
        return {
            'username': self.username,
            'index': self.index,
            'color': self.color,
            'address': self.address,
            'registered': self.registered,
            'status': self.status,
            'status_message': self.status_message,
            'connected_at': self.connected_at,
            'connected_for': now - self.connected_at,  # durations are immune to clock differences
            'idle': now - self.last_activity,
            **self.stats,
        }


class Server:
    def __init__(
        self,
        host: str = '127.0.0.1',
        port: int = 8000,
        data_dir: Path | str = Path.home() / '.zlink' / 'server',
        auth: bool = False,
        max_clients: int = 16,
        max_upload: int = 2 * 1024**3,
        allow_shutdown: bool = False,
        verbose: bool = False,
    ):
        self.host = host
        self.port = port
        self.auth = auth
        self.max_clients = max_clients
        self.max_upload = max_upload
        self.allow_shutdown = allow_shutdown
        self.data_dir = Path(data_dir)
        self.storage_dir = self.data_dir / 'files'
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.users = UserStore(self.data_dir / 'users.json')
        self.log = get_logger('server', self.data_dir / 'logs' / 'server.log', verbose)
        self.sessions: list[Session] = []
        self.started_at = time.time()
        self.address: tuple[str, int] | None = None
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()
        self._counter = itertools.count(1)
        self._stop = threading.Event()
        self._closed = False
        self.history: deque[dict] = deque(maxlen=HISTORY_SIZE)  # last public chat messages
        self.stats = {'connections': 0, 'peak_clients': 0, 'messages': 0, 'bytes_in': 0, 'bytes_out': 0}
        self._handlers: dict[MsgType, Callable[[Session, Message], dict | None]] = {
            MsgType.PING: self._on_ping,
            MsgType.INFO: self._on_info,
            MsgType.WHO: self._on_who,
            MsgType.CHAT: self._on_chat,
            MsgType.LIST: self._on_list,
            MsgType.UPLOAD: self._on_upload,
            MsgType.DOWNLOAD: self._on_download,
            MsgType.MKDIR: self._on_mkdir,
            MsgType.STATUS: self._on_status,
            MsgType.WHOIS: self._on_whois,
            MsgType.HISTORY: self._on_history,
            MsgType.READ: self._on_read,
            MsgType.DISCONNECT: lambda session, msg: {},
            MsgType.SHUTDOWN: self._on_shutdown,
        }

    # ------------------------------------------------------------------ lifecycle

    def bind(self) -> tuple[str, int]:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, self.port))
        sock.listen(self.max_clients)
        sock.settimeout(0.5)  # lets the accept loop notice shutdowns
        self._sock = sock
        self.address = sock.getsockname()[:2]
        self.log.info(f'Listening on {self.address[0]}:{self.address[1]} '
                      f'(auth {"required" if self.auth else "disabled"}, storage: {self.storage_dir})')
        console.print(server_card(self.info()))
        return self.address

    def serve_forever(self) -> None:
        if self._sock is None:
            self.bind()
        try:
            while not self._stop.is_set():
                try:
                    client_sock, _ = self._sock.accept()
                except TimeoutError:
                    continue
                except OSError:
                    break  # listening socket closed by shutdown()
                client_sock.settimeout(None)
                index = next(self._counter)
                threading.Thread(target=self._handle, args=(client_sock, index), name=f'session-{index}',
                                 daemon=True).start()
        except KeyboardInterrupt:
            self.log.info('Keyboard interrupt')
        finally:
            self.shutdown()

    def start(self) -> threading.Thread:
        """Serve in a background thread (handy for tests and embedding)."""
        if self._sock is None:
            self.bind()
        thread = threading.Thread(target=self.serve_forever, name='accept-loop', daemon=True)
        thread.start()
        return thread

    def shutdown(self, reason: str = 'server shutting down') -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            sessions = list(self.sessions)
        self._stop.set()
        for session in sessions:
            session.push(EventKind.SHUTDOWN, reason=reason)
            session.conn.close()
        if self._sock is not None:
            self._sock.close()
        self.log.info(f'Server stopped after {format_duration(time.time() - self.started_at)}')

    # ------------------------------------------------------------------ sessions

    def _handle(self, client_sock: socket.socket, index: int) -> None:
        session = Session(Connection(client_sock), index)
        self.log.info(f'Connection from {session.address}')
        try:
            with self._lock:
                full = len(self.sessions) >= self.max_clients
            if full:
                session.conn.send(MsgType.ERROR, reason='server is full')
                return
            session.conn.send(
                MsgType.WELCOME,
                version=__version__,
                protocol=PROTOCOL_VERSION,
                server=socket.gethostname(),
                session=session.id,
                index=session.index,
                color=session.color,
                auth_required=self.auth,
                max_upload=self.max_upload,
            )
            if self._authenticate(session):
                self._serve_session(session)
        except OSError:
            pass  # connection lost, includes ConnectionClosed
        except ProtocolError as exc:
            self.log.warning(f'{session.address}: protocol error ({exc})')
        finally:
            self._close_session(session)

    def _authenticate(self, session: Session) -> bool:
        attempts = 0
        while True:
            msg = session.conn.recv()
            if msg.type is MsgType.DISCONNECT:
                return False
            if msg.type is not MsgType.AUTH:
                raise ProtocolError(f'expected an auth request, got "{msg.type.value}"')
            try:
                self._claim(session, msg)
            except AuthError as exc:
                attempts += 1
                final = attempts >= MAX_AUTH_ATTEMPTS
                self.log.warning(f'{session.address}: authentication failed ({exc}), '
                                 f'attempt {attempts}/{MAX_AUTH_ATTEMPTS}')
                session.conn.send(MsgType.AUTH_RESULT, ok=False, reason=str(exc), final=final)
                if final:
                    return False
                continue
            session.conn.send(MsgType.AUTH_RESULT, ok=True, username=session.username)
            self._on_join(session)
            return True

    def _claim(self, session: Session, msg: Message) -> None:
        """Check the credentials of an AUTH request and register the session under its username."""
        action, username, password = msg.get('action'), msg.get('username'), msg.get('password')
        match action:
            case 'guest':
                if self.auth:
                    raise AuthError('this server requires an account, please login or register')
                username = validate_username(username) if username else 'guest'
                if username in self.users:
                    raise AuthError(f'"{username}" is a registered account, please login')
            case 'login':
                if not self.users.verify(validate_username(username), password):
                    raise AuthError('wrong username or password')
                session.registered = True
            case 'register':
                session.registered = True
                self.users.register(username, password)
                self.log.info(f'New account registered: {username}')
            case _:
                raise AuthError('unknown authentication action')

        with self._lock:
            taken = {s.username for s in self.sessions}
            if action == 'guest':
                base, n = username, 2
                while username in taken:
                    username, n = f'{base}{n}', n + 1
            elif username in taken:
                raise AuthError(f'"{username}" is already connected')
            session.username = username
            self.sessions.append(session)
            self.stats['connections'] += 1
            self.stats['peak_clients'] = max(self.stats['peak_clients'], len(self.sessions))

    def _on_join(self, session: Session) -> None:
        self.log.info(f'{session.username} joined from {session.address}')
        sounds.play(sounds.Sound.CLIENT_JOINED)
        self._broadcast(EventKind.JOIN, exclude=session, username=session.username)
        self.print_sessions()

    def _close_session(self, session: Session) -> None:
        with self._lock:
            if session.closed:
                return
            session.closed = True
            registered = session in self.sessions
            if registered:
                self.sessions.remove(session)
        session.conn.close()
        if registered:
            self.log.info(f'{session.username} left')
            self._broadcast(EventKind.LEAVE, username=session.username)
            if not self._stop.is_set():
                self.print_sessions()

    def _serve_session(self, session: Session) -> None:
        while not self._stop.is_set():
            msg = session.conn.recv()
            if msg.type in (MsgType.CHUNK, MsgType.END):
                continue  # leftovers of an upload that was rejected midway
            request_id = msg.get('id')
            handler = self._handlers.get(msg.type)
            if handler is None:
                session.conn.send(MsgType.ERROR, reply_to=request_id, reason=f'unknown request "{msg.type.value}"')
                continue
            self.log.debug(f'{session.username}: {msg.type.value} request')
            if msg.type is not MsgType.PING:
                session.last_activity = time.time()
            try:
                result = handler(session, msg)
            except RequestError as exc:
                session.conn.send(MsgType.ERROR, reply_to=request_id, reason=str(exc))
                continue
            except (OSError, ProtocolError):
                raise
            except Exception:
                self.log.exception(f'Error while handling {msg.type.value} from {session.username}')
                session.conn.send(MsgType.ERROR, reply_to=request_id, reason='internal server error')
                continue
            if isinstance(result, tuple):  # (fields, binary payload)
                session.conn.send(MsgType.OK, result[1], reply_to=request_id, **result[0])
            elif result is not None:
                session.conn.send(MsgType.OK, reply_to=request_id, **result)
            if msg.type is MsgType.DISCONNECT:
                return
            if msg.type is MsgType.SHUTDOWN:
                self.shutdown(reason=f'shutdown requested by {session.username}')
                return

    def _broadcast(self, kind: EventKind, exclude: Session | None = None, **fields) -> None:
        with self._lock:
            sessions = [s for s in self.sessions if s is not exclude]
        for session in sessions:
            session.push(kind, **fields)

    def _find(self, username: str) -> Session | None:
        with self._lock:
            return next((s for s in self.sessions if s.username == username), None)

    def print_sessions(self) -> None:
        with self._lock:
            users = [s.describe() for s in self.sessions]
        table = Table(title=f'Connected clients ({len(users)}/{self.max_clients})', title_style='title', box=ui.box(),
                      border_style='border')
        table.add_column('#', style='muted', justify='right')
        table.add_column('User')
        table.add_column('Status')
        table.add_column('Address', style='value')
        table.add_column('Connected', style='key', justify='right')
        table.add_column('Idle', style='muted', justify='right')
        table.add_column('↑ / ↓', style='muted', justify='right')
        for user in users:
            name = Text(user['username'], style=f'bold {ui.user_color(user["index"])}')
            name.append(' ★' if user['registered'] else ' ☆', style='key' if user['registered'] else 'muted')
            table.add_row(str(user['index']), name, ui.status_badge(user['status']), user['address'],
                          format_duration(user['connected_for']), format_duration(user['idle']),
                          f'{human_size(user["bytes_up"])} / {human_size(user["bytes_down"])}')
        console.print(table)

    def info(self) -> dict:
        files, storage_used = storage_stats(self.storage_dir)
        disk = shutil.disk_usage(self.storage_dir)
        with self._lock:
            clients = len(self.sessions)
        return {
            'hostname': socket.gethostname(),
            'address': f'{self.address[0]}:{self.address[1]}' if self.address else None,
            'platform': f'{platform.system()} {platform.release()} ({platform.machine()})',
            'python': platform.python_version(),
            'version': __version__,
            'uptime': time.time() - self.started_at,
            'clients': clients,
            'max_clients': self.max_clients,
            'registered_users': len(self.users),
            'files': files,
            'storage_used': storage_used,
            'disk_free': disk.free,
            'disk_total': disk.total,
            'max_upload': self.max_upload,
            'auth_required': self.auth,
            'allow_shutdown': self.allow_shutdown,
            'cpu_temp': cpu_temperature(),
            'load_avg': list(os.getloadavg()) if hasattr(os, 'getloadavg') else None,
            'history': len(self.history),
            **self.stats,
        }

    # ------------------------------------------------------------------ request handlers

    def _on_ping(self, session: Session, msg: Message) -> tuple[dict, bytes]:
        if len(msg.payload) > MAX_PING_PAYLOAD:
            raise RequestError(f'ping payload too large (max {human_size(MAX_PING_PAYLOAD)})')
        return {'time': time.time()}, msg.payload  # the payload is echoed back

    def _on_info(self, session: Session, msg: Message) -> dict:
        return self.info()

    def _on_who(self, session: Session, msg: Message) -> dict:
        with self._lock:
            return {'users': [s.describe() for s in self.sessions]}

    def _on_chat(self, session: Session, msg: Message) -> dict:
        text, to = msg.get('text'), msg.get('to')
        if not isinstance(text, str) or not text.strip():
            raise RequestError('empty message')
        if len(text) > MAX_CHAT_LENGTH:
            raise RequestError(f'message too long (max {MAX_CHAT_LENGTH} characters)')
        action, sent_at = bool(msg.get('action')), time.time()
        if to is None:
            entry = {'sender': session.username, 'text': text, 'action': action, 'time': sent_at}
            self.history.append(entry)
            self._broadcast(EventKind.CHAT, exclude=session, private=False, **entry)
            self.log.info(f'[chat] {"* " if action else ""}{session.username}{"" if action else ":"} {text}')
        else:
            target = self._find(to)
            if target is None:
                raise RequestError(f'no connected user named "{to}"')
            if target is session:
                raise RequestError('you cannot send a private message to yourself')
            target.push(EventKind.CHAT, sender=session.username, text=text, action=action, time=sent_at, private=True)
            self.log.info(f'[chat] {session.username} -> {to}: (private message)')
        session.stats['messages'] += 1
        self.stats['messages'] += 1
        return {'time': sent_at}

    def _on_status(self, session: Session, msg: Message) -> dict:
        status, message = msg.get('status'), msg.get('message') or None
        if status not in STATUSES:
            raise RequestError(f'unknown status, use one of: {", ".join(STATUSES)}')
        if message is not None and (not isinstance(message, str) or len(message) > MAX_STATUS_LENGTH):
            raise RequestError(f'status message too long (max {MAX_STATUS_LENGTH} characters)')
        session.status, session.status_message = status, message
        self.log.info(f'{session.username} is now {status}{f" ({message})" if message else ""}')
        self._broadcast(EventKind.STATUS, exclude=session, username=session.username, status=status, message=message)
        return {'status': status, 'message': message}

    def _on_whois(self, session: Session, msg: Message) -> dict:
        target = self._find(msg.get('username'))
        if target is None:
            raise RequestError(f'no connected user named "{msg.get("username")}"')
        return {'user': target.describe()}

    def _on_history(self, session: Session, msg: Message) -> dict:
        count = msg.get('count', 20)
        if not isinstance(count, int) or count < 1:
            raise RequestError('invalid message count')
        return {'messages': list(self.history)[-count:]}

    def _on_list(self, session: Session, msg: Message) -> dict:
        path, rel = self._resolve(msg.get('path', ''))
        if not path.is_dir():
            raise RequestError(f'no folder named "{display_path(rel)}" on the server')
        return {'path': display_path(rel), 'entries': list_dir(path)}

    def _on_mkdir(self, session: Session, msg: Message) -> dict:
        path, rel = self._resolve(msg.get('path'))
        if not rel.parts:
            raise RequestError('invalid folder name')
        if path.exists():
            raise RequestError(f'"{display_path(rel)}" already exists')
        if not path.parent.is_dir():
            raise RequestError(f'no folder named "{display_path(rel.parent)}" on the server')
        path.mkdir()
        self.log.info(f'{session.username} created folder {display_path(rel)}')
        return {'path': display_path(rel)}

    def _on_upload(self, session: Session, msg: Message) -> dict:
        request_id, size = msg.get('id'), msg.get('size')
        name = self._filename(msg.get('name'))
        folder, folder_rel = self._resolve(msg.get('dir', ''))
        if not folder.is_dir():
            raise RequestError(f'no folder named "{display_path(folder_rel)}" on the server')
        if not isinstance(size, int) or size < 0:
            raise RequestError('invalid file size')
        if size > self.max_upload:
            raise RequestError(f'file too large ({human_size(size)}, max {human_size(self.max_upload)})')
        if size > shutil.disk_usage(self.storage_dir).free:
            raise RequestError('not enough disk space on the server')

        tmp = self.storage_dir / f'.upload-{uuid.uuid4().hex}.part'
        digest, received = hashlib.sha256(), 0
        session.conn.send(MsgType.OK, reply_to=request_id)
        try:
            with tmp.open('wb') as f:
                while True:
                    frame = session.conn.recv()
                    if frame.get('transfer') != request_id or frame.type not in (MsgType.CHUNK, MsgType.END):
                        raise RequestError(f'unexpected "{frame.type.value}" frame during upload')
                    if frame.type is MsgType.END:
                        break
                    received += len(frame.payload)
                    if received > size:
                        raise RequestError('received more data than announced')
                    f.write(frame.payload)
                    digest.update(frame.payload)
            if frame.get('aborted'):
                raise RequestError('upload aborted by client')
            if received != size:
                raise RequestError(f'size mismatch: announced {size} bytes, received {received}')
            if frame.get('sha256') != digest.hexdigest():
                raise RequestError('checksum mismatch, file discarded')
            with self._lock:
                final = unique_path(folder, name)
                tmp.replace(final)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

        remote_path = display_path(folder_rel / final.name)
        session.stats['uploads'] += 1
        session.stats['bytes_up'] += size
        self.stats['bytes_in'] += size
        self.log.info(f'{session.username} uploaded {remote_path} ({human_size(size)})')
        sounds.play(sounds.Sound.TRANSFER_DONE)
        self._broadcast(EventKind.FILE, exclude=session, sender=session.username, name=final.name,
                        path=remote_path, size=size)
        return {'name': final.name, 'path': remote_path, 'size': size, 'sha256': digest.hexdigest()}

    def _on_download(self, session: Session, msg: Message) -> None:
        request_id = msg.get('id')
        path, rel = self._resolve(msg.get('name'))
        if not path.is_file():
            raise RequestError(f'no file named "{display_path(rel)}" on the server')
        size = path.stat().st_size
        session.conn.send(MsgType.OK, reply_to=request_id, name=path.name, size=size)
        digest = hashlib.sha256()
        with path.open('rb') as f:
            while chunk := f.read(CHUNK_SIZE):
                digest.update(chunk)
                session.conn.send(MsgType.CHUNK, chunk, reply_to=request_id)
        session.conn.send(MsgType.END, reply_to=request_id, sha256=digest.hexdigest())
        session.stats['downloads'] += 1
        session.stats['bytes_down'] += size
        self.stats['bytes_out'] += size
        self.log.info(f'{session.username} downloaded {display_path(rel)} ({human_size(size)})')

    def _on_read(self, session: Session, msg: Message) -> tuple[dict, bytes]:
        """First ``limit`` bytes of a file, for previews."""
        path, rel = self._resolve(msg.get('name'))
        if not path.is_file():
            raise RequestError(f'no file named "{display_path(rel)}" on the server')
        limit = msg.get('limit', 256 * 1024)
        if not isinstance(limit, int) or limit < 1:
            raise RequestError('invalid read limit')
        limit = min(limit, MAX_READ_SIZE)
        stat = path.stat()
        with path.open('rb') as f:
            data = f.read(limit)
        fields = {'name': path.name, 'path': display_path(rel), 'size': stat.st_size, 'mtime': stat.st_mtime,
                  'truncated': stat.st_size > len(data)}
        return fields, data

    def _on_shutdown(self, session: Session, msg: Message) -> dict:
        if not self.allow_shutdown:
            raise RequestError('remote shutdown is disabled on this server (start it with --allow-shutdown)')
        self.log.warning(f'Shutdown requested by {session.username}')
        return {}

    def _resolve(self, path: object) -> tuple[Path, PurePosixPath]:
        """Map a client supplied path onto the storage directory, refusing anything outside of it."""
        try:
            rel = safe_relpath(path)
        except ValueError as exc:
            raise RequestError(str(exc)) from None
        full = self.storage_dir / rel
        if not full.resolve().is_relative_to(self.storage_dir.resolve()):  # symlinks placed on the server side
            raise RequestError('invalid path')
        return full, rel

    @staticmethod
    def _filename(name: object) -> str:
        try:
            return safe_filename(name)
        except ValueError as exc:
            raise RequestError(str(exc)) from None
