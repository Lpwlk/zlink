"""zlink client API, independent from the interactive shell (see ``shell.py``)."""

from __future__ import annotations

import hashlib
import itertools
import os
import queue
import socket
import threading
import time
from pathlib import Path
from typing import Callable

from .files import safe_relpath, unique_path
from .protocol import CHUNK_SIZE, Connection, ConnectionClosed, Message, MsgType, ProtocolError

ProgressCallback = Callable[[int, int], None]  # (bytes transferred so far, total bytes)


class RequestError(Exception):
    """The server answered a request with an error."""


class AuthFailed(Exception):
    def __init__(self, reason: str, final: bool):
        super().__init__(reason)
        self.final = final  # True when the server closed the connection


class TransferError(Exception):
    pass


class Client:
    def __init__(
        self,
        host: str = '127.0.0.1',
        port: int = 8000,
        downloads_dir: Path | str = Path.home() / '.zlink' / 'downloads',
        timeout: float = 10.0,
    ):
        self.host = host
        self.port = port
        self.downloads_dir = Path(downloads_dir)
        self.timeout = timeout
        self.conn: Connection | None = None
        self.welcome: dict = {}
        self.username: str | None = None
        self.connected = False
        # Called from the reader thread for every event pushed by the server.
        self.on_event: Callable[[Message], None] = lambda msg: None
        # Called from the reader thread when the connection is lost (not after close()).
        self.on_disconnect: Callable[[Exception], None] = lambda exc: None
        self._ids = itertools.count(1)
        self._pending: dict[int, queue.Queue] = {}
        self._pending_lock = threading.Lock()
        self._closing = False

    # ------------------------------------------------------------------ connection

    def connect(self) -> dict:
        """Open the connection and return the server WELCOME fields."""
        sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self.conn = Connection(sock)
        msg = self.conn.recv()
        sock.settimeout(None)
        if msg.type is MsgType.ERROR:
            self.conn.close()
            raise RequestError(msg.get('reason', 'connection refused'))
        if msg.type is not MsgType.WELCOME:
            raise ProtocolError(f'expected welcome, got "{msg.type.value}"')
        self.welcome = msg.fields
        return self.welcome

    @property
    def auth_required(self) -> bool:
        return bool(self.welcome.get('auth_required'))

    def login(self, username: str, password: str) -> str:
        return self._authenticate('login', username, password)

    def register(self, username: str, password: str) -> str:
        return self._authenticate('register', username, password)

    def join_as_guest(self, nickname: str | None = None) -> str:
        return self._authenticate('guest', nickname or None)

    def _authenticate(self, action: str, username: str | None, password: str | None = None) -> str:
        self.conn.send(MsgType.AUTH, action=action, username=username, password=password)
        result = self.conn.recv()
        if result.type is not MsgType.AUTH_RESULT:
            raise ProtocolError(f'expected auth result, got "{result.type.value}"')
        if not result.get('ok'):
            if result.get('final'):
                self.conn.close()
            raise AuthFailed(result.get('reason', 'authentication failed'), bool(result.get('final')))
        self.username = result['username']
        self.connected = True
        threading.Thread(target=self._reader, name='client-reader', daemon=True).start()
        return self.username

    def close(self) -> None:
        if self.conn is None:
            return
        self._closing = True
        if self.connected:
            try:
                self._call(MsgType.DISCONNECT, timeout=2)
            except (OSError, RequestError, queue.Empty):
                pass
        self.connected = False
        self.conn.close()

    def _reader(self) -> None:
        try:
            while True:
                msg = self.conn.recv()
                reply_to = msg.fields.pop('reply_to', None)
                if reply_to is not None:
                    with self._pending_lock:
                        q = self._pending.get(reply_to)
                    if q is not None:
                        q.put(msg)
                elif msg.type is MsgType.EVENT:
                    self.on_event(msg)
        except (OSError, ProtocolError) as exc:
            self.connected = False
            with self._pending_lock:
                for q in self._pending.values():
                    q.put(None)
            if not self._closing:
                self.on_disconnect(exc)

    # ------------------------------------------------------------------ request plumbing

    def _open(self, type: MsgType, payload: bytes = b'', **fields) -> tuple[int, queue.Queue]:
        if not self.connected:
            raise ConnectionClosed('not connected')
        request_id = next(self._ids)
        q: queue.Queue = queue.Queue()
        with self._pending_lock:
            self._pending[request_id] = q
        self.conn.send(type, payload, id=request_id, **fields)
        return request_id, q

    def _release(self, request_id: int) -> None:
        with self._pending_lock:
            self._pending.pop(request_id, None)

    def _wait(self, q: queue.Queue, timeout: float | None = None) -> Message:
        msg = q.get(timeout=timeout or self.timeout)
        if msg is None:
            raise ConnectionClosed('connection lost')
        if msg.type is MsgType.ERROR:
            raise RequestError(msg.get('reason', 'unknown error'))
        return msg

    def _call(self, type: MsgType, timeout: float | None = None, payload: bytes = b'', **fields) -> Message:
        request_id, q = self._open(type, payload, **fields)
        try:
            return self._wait(q, timeout)
        finally:
            self._release(request_id)

    # ------------------------------------------------------------------ requests

    def ping(self, size: int = 0, timeout: float | None = None) -> float:
        """Round trip time in milliseconds, optionally with ``size`` random bytes echoed by the server.

        Raises ``queue.Empty`` when no answer came within ``timeout`` seconds.
        """
        payload = os.urandom(size) if size else b''
        start = time.perf_counter()
        reply = self._call(MsgType.PING, timeout=timeout, payload=payload)
        elapsed = (time.perf_counter() - start) * 1000
        if reply.payload != payload:
            raise TransferError('the server echoed a corrupted ping payload')
        return elapsed

    def info(self) -> dict:
        return self._call(MsgType.INFO).fields

    def who(self) -> list[dict]:
        return self._call(MsgType.WHO)['users']

    def chat(self, text: str, to: str | None = None, action: bool = False) -> None:
        """Send a message to everyone, or privately ``to`` a user. ``action`` sends an emote (``/me``)."""
        self._call(MsgType.CHAT, text=text, to=to, action=action)

    def set_status(self, status: str, message: str | None = None) -> dict:
        """Presence shown to the other users: 'online', 'away' or 'busy', with an optional message."""
        return self._call(MsgType.STATUS, status=status, message=message).fields

    def whois(self, username: str) -> dict:
        return self._call(MsgType.WHOIS, username=username)['user']

    def history(self, count: int = 20) -> list[dict]:
        """Last public chat messages kept by the server, oldest first."""
        return self._call(MsgType.HISTORY, count=count)['messages']

    def list_dir(self, path: str = '/') -> list[dict]:
        """Entries of a server folder: dicts with name, type ('dir' or 'file'), size and mtime."""
        return self._call(MsgType.LIST, path=path)['entries']

    def mkdir(self, path: str) -> str:
        return self._call(MsgType.MKDIR, path=path)['path']

    def read_file(self, remote_path: str, limit: int = 256 * 1024) -> tuple[dict, bytes]:
        """Beginning of a server file (at most ``limit`` bytes, 1 MB max): (name, path, size, mtime, truncated), data."""
        safe_relpath(remote_path)
        reply = self._call(MsgType.READ, name=remote_path, limit=limit)
        return reply.fields, reply.payload

    def shutdown_server(self) -> None:
        self._call(MsgType.SHUTDOWN)

    def upload(self, path: Path | str, remote_dir: str = '/', progress: ProgressCallback | None = None) -> dict:
        """Send a local file into a server folder. Returns the stored name, path, size and sha256."""
        path = Path(path).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f'no such file: {path}')
        size = path.stat().st_size
        request_id, q = self._open(MsgType.UPLOAD, name=path.name, dir=remote_dir, size=size)
        try:
            self._wait(q)  # the server accepted the transfer
            digest, sent = hashlib.sha256(), 0
            try:
                with path.open('rb') as f:
                    while chunk := f.read(CHUNK_SIZE):
                        if not q.empty():  # the server rejected the transfer midway
                            self._wait(q)
                            raise ProtocolError('unexpected response during upload')
                        digest.update(chunk)
                        self.conn.send(MsgType.CHUNK, chunk, transfer=request_id)
                        sent += len(chunk)
                        if progress:
                            progress(sent, size)
            except (KeyboardInterrupt, OSError):
                if self.connected:
                    self.conn.send(MsgType.END, transfer=request_id, aborted=True)
                raise
            self.conn.send(MsgType.END, transfer=request_id, sha256=digest.hexdigest())
            return self._wait(q, timeout=max(self.timeout, 60)).fields
        finally:
            self._release(request_id)

    def download(self, remote_path: str, dest: Path | str | None = None,
                 progress: ProgressCallback | None = None) -> Path:
        """Fetch a server file (e.g. ``'photos/cat.jpg'``) and return its local path.

        ``dest`` can be a directory or a file path, it defaults to the downloads directory.
        Existing files are never overwritten, a numbered suffix is added instead.
        """
        safe_relpath(remote_path)
        request_id, q = self._open(MsgType.DOWNLOAD, name=remote_path)
        try:
            head = self._wait(q)
            size = head['size']
            target = Path(dest).expanduser() if dest else self.downloads_dir
            if target.is_dir() or dest is None:
                target = target / head['name']
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(f'.{target.name}.part')
            digest, received = hashlib.sha256(), 0
            try:
                with tmp.open('wb') as f:
                    while True:
                        msg = self._wait(q, timeout=max(self.timeout, 30))
                        if msg.type is MsgType.END:
                            break
                        if msg.type is not MsgType.CHUNK:
                            raise ProtocolError(f'unexpected "{msg.type.value}" frame during download')
                        f.write(msg.payload)
                        digest.update(msg.payload)
                        received += len(msg.payload)
                        if progress:
                            progress(received, size)
                if received != size:
                    raise TransferError(f'size mismatch: expected {size} bytes, received {received}')
                if msg.get('sha256') != digest.hexdigest():
                    raise TransferError('checksum mismatch, file discarded')
                final = unique_path(target.parent, target.name)
                tmp.replace(final)
                return final
            except BaseException:
                tmp.unlink(missing_ok=True)
                raise
        finally:
            self._release(request_id)
