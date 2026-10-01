import io
import socket
import threading

import pytest
from rich.console import Console

from zlink import debug
from zlink.client import RequestError
from zlink.protocol import Message, MsgType


@pytest.fixture
def trace():
    """Run a CallTracer writing into a buffer, return a function giving the plain text trace."""
    buffer = io.StringIO()
    tracer = debug.CallTracer(Console(file=buffer, width=250, color_system=None))
    tracer.start()
    yield lambda: buffer.getvalue()
    tracer.stop()


def test_call_tree(server, make_client, tmp_path, trace):
    client = make_client(server, connect_only=True)
    client.login  # noqa: B018  (attribute access is not traced)
    with pytest.raises(Exception):
        client.login('nobody', 'hunter2')
    client.join_as_guest('loic')
    (tmp_path / 'big.bin').write_bytes(b'x' * 400_000)
    client.upload(tmp_path / 'big.bin')
    with pytest.raises(RequestError):
        client.download('missing.txt')
    client.close()
    output = trace()

    assert '▶ client.Client.join_as_guest(nickname=\'loic\')' in output
    assert '◀ client.Client.join_as_guest → \'loic\'' in output
    assert 'hunter2' not in output and 'password=•••' in output
    assert '<ping' not in output and '<upload #' in output  # messages rendered compactly
    assert 'payload=<65536 bytes>' in output
    assert '↻ ×' in output  # chunk loops are collapsed
    assert '✗ RequestError: no file named "/missing.txt"' in output
    assert 'session-1' in output and 'MainThread' in output
    assert '_recv_exact' not in output  # quiet helpers are hidden


def test_caught_exception_is_not_reported(trace):
    from zlink.files import safe_filename
    from zlink.server import Server

    with pytest.raises(Exception):
        Server._filename('../x')  # raises RequestError, traced as an error
    assert safe_filename('ok.txt') == 'ok.txt'
    output = trace()
    assert "✗ files.safe_filename(name='../x') ✗ ValueError" in output
    assert '◀ server.Server._filename ✗ RequestError' in output
    assert "● files.safe_filename(name='ok.txt') → 'ok.txt'" in output


def test_short_repr():
    msg = Message(MsgType.CHUNK, {'reply_to': 4}, b'x' * 100)
    assert debug.short_repr(msg) == '<chunk ↩4 +100B>'
    assert debug.short_repr(MsgType.PING) == 'ping'
    assert debug.short_repr({'username': 'a', 'password': 'secret'}) == "{'username': 'a', 'password': '•••'}"
    assert debug.short_repr(b'x' * 100) == '<100 bytes>'
    assert debug.short_repr(threading.Lock()).startswith('<')
    assert len(debug.short_repr('y' * 500)) == 48


def test_wire_trace_reports_tcp_byte_counts_without_payload():
    left, right = socket.socketpair()
    buffer = io.StringIO()
    tracer = debug.CallTracer(Console(file=buffer, width=250, color_system=None), wire_trace=True)
    tracer.start()
    try:
        from zlink.protocol import Connection

        Connection(left).send(MsgType.AUTH, password='secret')
        Connection(right).recv()
    finally:
        tracer.stop()
        left.close()
        right.close()

    output = buffer.getvalue()
    assert 'TCP →' in output and 'TCP ←' in output
    assert ' B' in output
    assert 'secret' not in output and 'password=•••' in output


def test_wire_trace_setting_survives_trace_toggle(monkeypatch):
    buffer = io.StringIO()
    monkeypatch.setattr(debug, 'console', Console(file=buffer, width=250, color_system=None))
    debug.enable('-', wire_trace=True)
    debug.disable()
    debug.enable()
    try:
        assert debug._tracer.wire_trace
    finally:
        debug.disable()
        debug.enable('-', wire_trace=False)
        debug.disable()


def test_stack_tree(server, make_client):
    make_client(server, 'alice')
    buffer = io.StringIO()
    Console(file=buffer, width=200, color_system=None).print(debug.stack_tree())
    output = buffer.getvalue()
    assert 'MainThread' in output and 'client-reader (daemon)' in output and 'session-1 (daemon)' in output
    assert '_serve_session' in output and 'library frame' in output
