import hashlib
import os
import queue
import time

import pytest

from zlink import Client
from zlink.client import AuthFailed, RequestError
from zlink.protocol import MsgType


def wait_for(predicate, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def collect_events(client):
    events = queue.Queue()
    client.on_event = events.put
    return events


def next_event(events, kind):
    while True:
        msg = events.get(timeout=3)
        if msg.get('kind') == kind:
            return msg.fields


def test_basic_requests(server, make_client):
    client = make_client(server, 'alice')
    assert client.username == 'alice'
    assert client.ping() > 0
    info = client.info()
    assert info['clients'] == 1
    assert info['auth_required'] is False
    assert [u['username'] for u in client.who()] == ['alice']


def test_guest_names_are_unique(server, make_client):
    assert make_client(server).username == 'guest'
    assert make_client(server).username == 'guest2'
    assert make_client(server, 'bob').username == 'bob'
    assert make_client(server, 'bob').username == 'bob2'


def test_broadcast_and_private_chat(server, make_client):
    alice, bob, carol = make_client(server, 'alice'), make_client(server, 'bob'), make_client(server, 'carol')
    bob_events, carol_events = collect_events(bob), collect_events(carol)

    alice.chat('hello [bold]everyone[/]')
    for events in (bob_events, carol_events):
        event = next_event(events, 'chat')
        assert event.pop('time') > 0
        assert event == {'kind': 'chat', 'sender': 'alice', 'text': 'hello [bold]everyone[/]', 'action': False,
                         'private': False}

    alice.chat('psst', to='bob')
    assert next_event(bob_events, 'chat')['private'] is True
    alice.chat('sync marker')
    assert next_event(carol_events, 'chat')['text'] == 'sync marker'  # carol never got the private message

    with pytest.raises(RequestError, match='no connected user'):
        alice.chat('hi', to='nobody')
    with pytest.raises(RequestError, match='empty'):
        alice.chat('   ')


def test_join_and_leave_events(server, make_client):
    alice = make_client(server, 'alice')
    events = collect_events(alice)
    bob = make_client(server, 'bob')
    assert next_event(events, 'join')['username'] == 'bob'
    bob.close()
    assert next_event(events, 'leave')['username'] == 'bob'
    assert wait_for(lambda: len(server.sessions) == 1)


def test_upload_download_roundtrip(server, make_client, tmp_path):
    alice, bob = make_client(server, 'alice'), make_client(server, 'bob')
    bob_events = collect_events(bob)
    data = os.urandom(300_000)  # several chunks
    source = tmp_path / 'photo.bin'
    source.write_bytes(data)

    progress = []
    result = alice.upload(source, progress=lambda done, total: progress.append((done, total)))
    assert result == {'name': 'photo.bin', 'path': '/photo.bin', 'size': len(data),
                      'sha256': hashlib.sha256(data).hexdigest()}
    assert progress[-1] == (len(data), len(data))
    assert (server.storage_dir / 'photo.bin').read_bytes() == data
    assert next_event(bob_events, 'file')['name'] == 'photo.bin'

    # Same name again: stored under a new name, nothing is overwritten
    assert alice.upload(source)['name'] == 'photo (1).bin'
    assert [f['name'] for f in bob.list_dir()] == ['photo (1).bin', 'photo.bin']

    path = bob.download('photo.bin')
    assert path == bob.downloads_dir / 'photo.bin'
    assert path.read_bytes() == data
    assert bob.download('photo.bin').name == 'photo (1).bin'
    custom = bob.download('photo.bin', tmp_path / 'renamed.bin')
    assert custom.read_bytes() == data
    assert not list(server.storage_dir.glob('.*.part'))
    assert not list(bob.downloads_dir.glob('.*.part'))


def test_empty_file_upload(server, make_client, tmp_path):
    client = make_client(server)
    (tmp_path / 'empty.txt').touch()
    assert client.upload(tmp_path / 'empty.txt')['size'] == 0
    assert client.download('empty.txt').read_bytes() == b''


def test_transfer_errors(make_server, make_client, tmp_path):
    server = make_server(max_upload=1000)
    client = make_client(server)
    big = tmp_path / 'big.bin'
    big.write_bytes(b'x' * 2000)
    with pytest.raises(RequestError, match='too large'):
        client.upload(big)
    with pytest.raises(RequestError, match='no file named'):
        client.download('missing.txt')
    for name in ('../users.json', '.hidden'):
        with pytest.raises((RequestError, ValueError)):
            client.download(name)
    # Raw request bypassing the client side validation
    with pytest.raises(RequestError, match='invalid file name'):
        client._call(MsgType.DOWNLOAD, name='../users.json')
    assert client.ping() > 0  # the session survived every error


def test_corrupted_upload_is_discarded(server, make_client):
    client = make_client(server)
    request_id, q = client._open(MsgType.UPLOAD, name='bad.bin', size=4)
    client._wait(q)
    client.conn.send(MsgType.CHUNK, b'data', transfer=request_id)
    client.conn.send(MsgType.END, transfer=request_id, sha256='0' * 64)
    with pytest.raises(RequestError, match='checksum mismatch'):
        client._wait(q)
    assert list(server.storage_dir.iterdir()) == []


def test_auth_server(make_server, make_client):
    server = make_server(auth=True)
    client = make_client(server, connect_only=True)
    assert client.auth_required
    with pytest.raises(AuthFailed, match='requires an account'):
        client.join_as_guest('sneaky')
    assert client.register('alice', 'secret') == 'alice'
    client.close()

    client = make_client(server, connect_only=True)
    assert client.login('alice', 'secret') == 'alice'
    other = make_client(server, connect_only=True)
    with pytest.raises(AuthFailed, match='already connected'):
        other.login('alice', 'secret')


def test_too_many_failed_logins(make_server, make_client):
    server = make_server(auth=True)
    server.users.register('alice', 'secret')
    client = make_client(server, connect_only=True)
    for attempt in range(3):
        with pytest.raises(AuthFailed) as exc:
            client.login('alice', 'wrong')
        assert exc.value.final is (attempt == 2)
    assert not client.connected


def test_registered_name_is_protected_for_guests(server, make_client):
    server.users.register('alice', 'secret')
    client = make_client(server, connect_only=True)
    with pytest.raises(AuthFailed, match='registered account'):
        client.join_as_guest('alice')


def test_server_full(make_server, make_client):
    server = make_server(max_clients=1)
    make_client(server)
    with pytest.raises(RequestError, match='full'):
        make_client(server)


def test_remote_shutdown(make_server, make_client):
    locked = make_server()
    with pytest.raises(RequestError, match='disabled'):
        make_client(locked).shutdown_server()

    server = make_server(allow_shutdown=True)
    alice, bob = make_client(server, 'alice'), make_client(server, 'bob')
    events = collect_events(bob)
    lost = []
    bob.on_disconnect = lost.append
    alice.shutdown_server()
    assert next_event(events, 'shutdown')['reason'] == 'shutdown requested by alice'
    assert wait_for(lambda: lost and not bob.connected)
    with pytest.raises(OSError):
        Client(*server.address, timeout=1).connect()


def test_folders(server, make_client, tmp_path):
    client = make_client(server)
    source = tmp_path / 'cat.jpg'
    source.write_bytes(b'meow')
    assert client.mkdir('/photos') == '/photos'
    assert client.mkdir('photos/2026') == '/photos/2026'
    result = client.upload(source, '/photos/2026')
    assert result['path'] == '/photos/2026/cat.jpg'
    assert (server.storage_dir / 'photos' / '2026' / 'cat.jpg').read_bytes() == b'meow'

    client.upload(source)  # root by default
    root = client.list_dir('/')
    assert [(e['name'], e['type']) for e in root] == [('photos', 'dir'), ('cat.jpg', 'file')]
    assert root[0]['size'] is None
    assert client.download('/photos/2026/cat.jpg', tmp_path / 'out.jpg').read_bytes() == b'meow'
    assert client.info()['files'] == 2

    with pytest.raises(RequestError, match='already exists'):
        client.mkdir('photos')
    with pytest.raises(RequestError, match='no folder'):
        client.mkdir('missing/sub')
    with pytest.raises(RequestError, match='no folder'):
        client.list_dir('/nope')
    with pytest.raises(RequestError, match='no folder'):
        client.upload(source, '/nope')
    with pytest.raises(RequestError, match='no file'):
        client.download('/photos')
    for bad in ('../escape', 'photos/../..', '.hidden'):
        with pytest.raises(RequestError, match='invalid'):
            client._call(MsgType.MKDIR, path=bad)


def test_symlink_escape_is_refused(server, make_client, tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'secret.txt').write_text('secret')
    (server.storage_dir / 'link').symlink_to(outside)
    client = make_client(server)
    with pytest.raises(RequestError, match='invalid path'):
        client.download('link/secret.txt')


def test_ping_payload_echo(server, make_client):
    client = make_client(server)
    assert client.ping(size=200_000) > 0
    with pytest.raises(RequestError, match='too large'):
        client._call(MsgType.PING, payload=b'x' * (1024 * 1024 + 1))


def test_status_whois_history(server, make_client, tmp_path):
    alice, bob = make_client(server, 'alice'), make_client(server, 'bob')
    events = collect_events(bob)
    assert alice.set_status('busy', 'in a meeting') == {'status': 'busy', 'message': 'in a meeting'}
    assert next_event(events, 'status') == {'kind': 'status', 'username': 'alice', 'status': 'busy',
                                            'message': 'in a meeting'}
    with pytest.raises(RequestError, match='unknown status'):
        alice.set_status('asleep')
    with pytest.raises(RequestError, match='too long'):
        alice.set_status('away', 'x' * 81)

    alice.chat('first')
    alice.chat('waves', action=True)
    alice.chat('secret', to='bob')  # private messages are not kept
    (tmp_path / 'f.bin').write_bytes(b'x' * 1000)
    alice.upload(tmp_path / 'f.bin')
    bob.download('f.bin')

    history = bob.history()
    assert [(m['sender'], m['text'], m['action']) for m in history] == [('alice', 'first', False),
                                                                        ('alice', 'waves', True)]
    assert len(bob.history(1)) == 1
    user = bob.whois('alice')
    assert (user['status'], user['messages'], user['uploads'], user['bytes_up']) == ('busy', 3, 1, 1000)
    assert bob.whois('bob')['bytes_down'] == 1000
    assert user['registered'] is False and user['idle'] >= 0
    with pytest.raises(RequestError, match='no connected user'):
        bob.whois('nobody')

    info = bob.info()
    assert (info['messages'], info['bytes_in'], info['bytes_out'], info['peak_clients']) == (3, 1000, 1000, 2)
    assert info['disk_total'] > info['disk_free'] > 0 and info['history'] == 2
