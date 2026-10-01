import pytest

from zlink.browser import PARENT, FileBrowser
from zlink.shell import Shell


@pytest.fixture
def shell(server, make_client, tmp_path):
    local = tmp_path / 'local'
    (local / 'sub').mkdir(parents=True)
    (local / 'a.txt').write_text('A')
    (local / 'b.txt').write_text('B')
    (local / '.secret').write_text('hidden')
    shell = Shell(make_client(server, 'alice'))
    shell.local_cwd = local
    return shell


def browse(shell, keys, start='local', answers=()):
    keys, answers = iter(keys), iter(answers)
    browser = FileBrowser(shell, start, read_key=lambda: next(keys, 'q'), ask=lambda prompt: next(answers))
    browser.run()
    return browser


def test_local_listing(shell):
    browser = browse(shell, [])
    assert browser.panes['local'].entries == [PARENT] + browser.panes['local'].entries[1:]
    assert [e.name for e in browser.panes['local'].entries[1:]] == ['sub', 'a.txt', 'b.txt']
    browser = browse(shell, ['.'])
    assert '.secret' in [e.name for e in browser.panes['local'].entries]


def test_upload_marked_files(shell, server):
    # cursor: .. / sub / a.txt / b.txt -> mark a.txt and b.txt, upload, confirm
    browse(shell, ['down', 'down', 'space', 'space', 'u', 'y'])
    assert sorted(p.name for p in server.storage_dir.iterdir()) == ['a.txt', 'b.txt']


def test_cancelled_upload(shell, server):
    browse(shell, ['down', 'down', 'enter', 'n'])
    assert list(server.storage_dir.iterdir()) == []


def test_mkdir_then_upload_into_it(shell, server):
    # server tab: new folder "docs", open it, back to the local tab, upload a.txt with Enter
    browser = browse(shell, ['tab', 'n', 'enter', 'tab', 'down', 'down', 'enter', 'y'], answers=['docs'])
    assert (server.storage_dir / 'docs' / 'a.txt').read_text() == 'A'
    assert shell.remote_cwd == '/docs'
    assert [e.name for e in browser.panes['remote'].entries] == ['..', 'a.txt']  # refreshed after the upload


def test_download_and_parent_navigation(shell, server):
    (server.storage_dir / 'photos').mkdir()
    (server.storage_dir / 'photos' / 'cat.jpg').write_bytes(b'meow')
    browser = browse(shell, ['enter', 'down', 'enter', 'y', 'left'], start='remote')
    assert (shell.client.downloads_dir / 'cat.jpg').read_bytes() == b'meow'
    assert shell.remote_cwd == '/'
    assert browser.current.name == 'photos'  # cursor back on the folder we left


def test_local_navigation_and_errors(shell, server):
    browser = browse(shell, ['down', 'enter'])  # into sub/
    assert shell.local_cwd.name == 'sub'
    statuses = []
    keys = iter(['tab', 'down', 'd'])
    browser = FileBrowser(shell, read_key=lambda: statuses.append(browser.status.plain) or next(keys, 'q'))
    browser.run()
    assert 'Select a file' in statuses[-1]  # empty server folder: nothing to download


def test_preview_key(shell, server, monkeypatch):
    shown = []
    monkeypatch.setattr('zlink.viewer.show', lambda renderable, pager=True: shown.append(renderable))
    (server.storage_dir / 'hello.py').write_text('print("hi")\n')
    browse(shell, ['down', 'down', 'v'])  # local a.txt
    browse(shell, ['v'], start='remote')  # server hello.py
    assert [panel.title.plain for panel in shown][0].endswith('a.txt')
    assert shown[1].title.plain.endswith('/hello.py')
