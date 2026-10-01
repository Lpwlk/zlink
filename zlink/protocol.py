"""Wire protocol shared by the server and the client.

Every frame sent on the TCP stream is laid out as::

    +---------------+----------------+-------------+----------------+
    | header length | payload length | JSON header | binary payload |
    | uint32 (BE)   | uint32 (BE)    | utf-8       | raw bytes      |
    +---------------+----------------+-------------+----------------+

The header always carries a ``type`` key (see :class:`MsgType`). Requests sent by the client
carry an ``id``; every response (including file chunks) carries ``reply_to`` with that same id.
This lets the client tell responses apart from events the server pushes spontaneously
(chat messages, users joining or leaving, new files...).
"""

from __future__ import annotations

import json
import socket
import struct
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

PROTOCOL_VERSION = 1
CHUNK_SIZE = 64 * 1024
MAX_HEADER_SIZE = 64 * 1024
MAX_PAYLOAD_SIZE = 4 * 1024 * 1024

_PREFIX = struct.Struct('!II')
_wire_trace: Callable[[str, str, int], None] | None = None


def set_wire_trace(callback: Callable[[str, str, int], None] | None) -> None:
    """Set a callback for bytes crossing connected TCP sockets."""
    global _wire_trace
    _wire_trace = callback


def _trace_wire(direction: str, sock: socket.socket, size: int) -> None:
    if _wire_trace is None:
        return
    try:
        peer = sock.getpeername()
        address = f'{peer[0]}:{peer[1]}' if isinstance(peer, tuple) else str(peer) or 'local'
    except OSError:
        address = '?'
    try:
        _wire_trace(direction, address, size)
    except Exception:
        pass


class ProtocolError(Exception):
    """The peer sent something that does not follow the protocol."""


class ConnectionClosed(ConnectionError):
    """The peer closed the connection."""


class MsgType(str, Enum):
    # Handshake
    WELCOME = 'welcome'
    AUTH = 'auth'
    AUTH_RESULT = 'auth_result'
    # Client requests
    PING = 'ping'
    INFO = 'info'
    WHO = 'who'
    CHAT = 'chat'
    LIST = 'list'
    UPLOAD = 'upload'
    DOWNLOAD = 'download'
    MKDIR = 'mkdir'
    STATUS = 'status'
    WHOIS = 'whois'
    HISTORY = 'history'
    READ = 'read'
    DISCONNECT = 'disconnect'
    SHUTDOWN = 'shutdown'
    # Generic responses
    OK = 'ok'
    ERROR = 'error'
    # File transfer frames
    CHUNK = 'chunk'
    END = 'end'
    # Pushed by the server at any time (see EventKind)
    EVENT = 'event'


class EventKind(str, Enum):
    CHAT = 'chat'
    JOIN = 'join'
    LEAVE = 'leave'
    FILE = 'file'
    STATUS = 'status'
    SHUTDOWN = 'shutdown'


@dataclass
class Message:
    type: MsgType
    fields: dict[str, Any] = field(default_factory=dict)
    payload: bytes = b''

    def get(self, key: str, default: Any = None) -> Any:
        return self.fields.get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self.fields[key]


class Connection:
    """Frames messages over a connected socket. ``send`` is thread-safe, ``recv`` is not."""

    def __init__(self, sock: socket.socket):
        self.sock = sock
        self._send_lock = threading.Lock()

    def send(self, type: MsgType, payload: bytes = b'', **fields: Any) -> None:
        header = json.dumps({'type': MsgType(type).value, **fields}, separators=(',', ':')).encode()
        if len(header) > MAX_HEADER_SIZE:
            raise ProtocolError(f'header too large ({len(header)} bytes)')
        if len(payload) > MAX_PAYLOAD_SIZE:
            raise ProtocolError(f'payload too large ({len(payload)} bytes)')
        with self._send_lock:
            frame = _PREFIX.pack(len(header), len(payload)) + header + payload
            self.sock.sendall(frame)
            _trace_wire('write', self.sock, len(frame))

    def recv(self) -> Message:
        header_len, payload_len = _PREFIX.unpack(self._recv_exact(_PREFIX.size))
        if header_len > MAX_HEADER_SIZE or payload_len > MAX_PAYLOAD_SIZE:
            raise ProtocolError(f'frame too large (header={header_len}, payload={payload_len})')
        try:
            header = json.loads(self._recv_exact(header_len))
            msg_type = MsgType(header.pop('type'))
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise ProtocolError(f'malformed header: {exc}') from None
        payload = self._recv_exact(payload_len) if payload_len else b''
        return Message(msg_type, header, payload)

    def _recv_exact(self, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            chunk = self.sock.recv(min(n - len(buf), 1 << 20))
            if not chunk:
                raise ConnectionClosed('connection closed by peer')
            _trace_wire('read', self.sock, len(chunk))
            buf += chunk
        return bytes(buf)

    @property
    def peer(self) -> str:
        try:
            host, port = self.sock.getpeername()[:2]
            return f'{host}:{port}'
        except OSError:
            return '?'

    def close(self) -> None:
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()
