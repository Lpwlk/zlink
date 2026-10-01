import pytest

from zlink import Client, Server, sounds


@pytest.fixture(autouse=True)
def mute():
    sounds.enabled = False


@pytest.fixture
def make_server(tmp_path):
    servers = []

    def factory(**options):
        server = Server(host='127.0.0.1', port=0, data_dir=tmp_path / f'server{len(servers)}', **options)
        server.start()
        servers.append(server)
        return server

    yield factory
    for server in servers:
        server.shutdown()


@pytest.fixture
def server(make_server):
    return make_server()


@pytest.fixture
def make_client(tmp_path):
    clients = []

    def factory(server, username=None, connect_only=False):
        client = Client(*server.address, downloads_dir=tmp_path / f'downloads{len(clients)}', timeout=5)
        clients.append(client)
        client.connect()
        if not connect_only:
            client.join_as_guest(username)
        return client

    yield factory
    for client in clients:
        client.close()
