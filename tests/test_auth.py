import pytest

from zlink.auth import AuthError, UserStore


def test_register_and_verify(tmp_path):
    store = UserStore(tmp_path / 'users.json')
    store.register('alice', 'secret')
    assert store.verify('alice', 'secret')
    assert not store.verify('alice', 'wrong')
    assert not store.verify('bob', 'secret')


def test_persisted_without_plaintext(tmp_path):
    path = tmp_path / 'users.json'
    UserStore(path).register('alice', 'secret')
    assert 'secret' not in path.read_text()
    assert UserStore(path).verify('alice', 'secret')


@pytest.mark.parametrize('username, password', [
    ('a', 'secret'),            # too short
    ('bad name', 'secret'),     # space
    ('[red]x[/]', 'secret'),    # markup
    ('alice', 'abc'),           # password too short
])
def test_invalid_registrations(tmp_path, username, password):
    with pytest.raises(AuthError):
        UserStore(tmp_path / 'users.json').register(username, password)


def test_duplicate_username(tmp_path):
    store = UserStore(tmp_path / 'users.json')
    store.register('alice', 'secret')
    with pytest.raises(AuthError):
        store.register('alice', 'other')
