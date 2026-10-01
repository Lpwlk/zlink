import socket
import struct

import pytest

from zlink.protocol import MAX_PAYLOAD_SIZE, Connection, ConnectionClosed, MsgType, ProtocolError


@pytest.fixture
def pair():
    a, b = socket.socketpair()
    yield Connection(a), Connection(b)
    a.close()
    b.close()


def test_roundtrip_with_payload(pair):
    left, right = pair
    left.send(MsgType.CHUNK, b'\x00\x01binary', id=3, name='héllo')
    msg = right.recv()
    assert msg.type is MsgType.CHUNK
    assert msg.fields == {'id': 3, 'name': 'héllo'}
    assert msg.payload == b'\x00\x01binary'


def test_messages_are_not_merged(pair):
    left, right = pair
    for i in range(50):
        left.send(MsgType.PING, id=i)
    assert [right.recv()['id'] for _ in range(50)] == list(range(50))


def test_unknown_type_is_rejected(pair):
    left, right = pair
    header = b'{"type":"nope"}'
    left.sock.sendall(struct.pack('!II', len(header), 0) + header)
    with pytest.raises(ProtocolError):
        right.recv()


def test_oversized_frame_is_rejected(pair):
    left, right = pair
    left.sock.sendall(struct.pack('!II', 10, MAX_PAYLOAD_SIZE + 1))
    with pytest.raises(ProtocolError):
        right.recv()
    with pytest.raises(ProtocolError):
        left.send(MsgType.CHUNK, b'x' * (MAX_PAYLOAD_SIZE + 1))


def test_closed_connection(pair):
    left, right = pair
    left.close()
    with pytest.raises(ConnectionClosed):
        right.recv()
