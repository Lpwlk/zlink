import pytest

from zlink import ui
from zlink.shell import PING, Shell, _HelpShown


@pytest.fixture
def shell(server, make_client):
    return Shell(make_client(server, 'alice'))


def run(shell, line):
    with ui.console.capture() as capture:
        shell.execute(line)
    return capture.get()


def test_command_parser():
    opts = PING.parse('-n 3 -i 0.1 -s 64K -q')
    assert (opts.count, opts.interval, opts.size, opts.quiet) == (3, 0.1, 65536, True)
    with pytest.raises(ValueError, match='usage: ping'):
        PING.parse('--bogus')
    with pytest.raises(ValueError, match='invalid size'):
        PING.parse('-s lots')
    with pytest.raises(_HelpShown):
        PING.parse('-h')


def test_ping_command(shell):
    output = run(shell, 'ping -n 3 -i 0.01 -s 4K')
    assert output.count('seq=') == 3
    assert '3 sent · 3 received · 0% loss' in output and 'Throughput' in output and 'Jitter' in output
    assert 'seq=' not in run(shell, 'ping -n 2 -i 0.01 -q')
    assert 'usage: ping' in run(shell, 'ping -x')
    assert '--interval' in run(shell, 'help ping')


def test_help_lists_categories(shell):
    output = run(shell, 'help')
    for word in ('chat', 'users', 'files', 'server', 'shell', 'debug', '[-n N]', 'whois'):
        assert word in output


def test_status_whois_and_cards(shell, make_client, server):
    bob = make_client(server, 'bob')
    assert 'away' in run(shell, 'status away back in 5')
    assert bob.whois('alice')['status_message'] == 'back in 5'
    assert 'usage: status' in run(shell, 'status sleeping')
    card = run(shell, 'whois bob')
    assert 'User · bob' in card and 'guest' in card and 'this client' not in card.lower()
    own = run(shell, 'card')
    assert 'THIS CLIENT' in own and 'Latency' in own and 'ocean' in own
    assert 'SERVICE' in run(shell, 'info')


def test_chat_commands(shell, make_client, server):
    bob = make_client(server, 'bob')
    run(shell, 'say hi @bob')
    run(shell, 'me waves')
    output = run(shell, 'history -n 5')
    assert 'you: hi @bob' in output and '* alice waves' in output
    who = run(shell, 'who -s name -r')
    assert who.index('bob') < who.index('alice')
    assert bob.whois('alice')['messages'] == 2


def test_ls_sort_and_theme(shell, server):
    (server.storage_dir / 'small.txt').write_bytes(b'x')
    (server.storage_dir / 'big.txt').write_bytes(b'x' * 5000)
    (server.storage_dir / 'docs').mkdir()
    output = run(shell, 'ls -s size -r')
    assert output.index('docs') < output.index('big.txt') < output.index('small.txt')
    assert '1 folder(s) · 2 file(s)' in output
    assert 'dracula' in run(shell, 'theme')
    assert 'switched to dracula' in run(shell, 'theme dracula')
    assert ui.current_theme().name == 'dracula'
    ui.set_theme('ocean')
    assert 'unknown theme' in run(shell, 'theme nope')


def test_prompt_decorator_fits_the_terminal(shell):
    shell.online = {'alice', 'bob'}
    shell.remote_cwd = '/photos'
    wide = shell.prompt_header(width=120).plain
    assert wide.startswith('╭─ ● alice ─ ') and '/photos' in wide and '2 online' in wide and len(wide) == 120
    narrow = shell.prompt_header(width=40).plain
    assert len(narrow) <= 40 and 'alice' in narrow and 'online' not in narrow  # details dropped first
    assert shell.input_prefix().plain == '╰─› '
    ui.set_theme('nord')  # square borders
    assert shell.prompt_header(width=80).plain.startswith('┌─') and shell.input_prefix().plain == '└─› '
    ui.set_theme('ocean')


def test_readline_safe_prompt():
    from zlink import lineedit
    ansi = '\x1b[1;38;2;95;175;255malice\x1b[0m › '
    wrapped = lineedit.readline_prompt(ansi)
    if lineedit.readline is not None:
        assert wrapped == '\001\x1b[1;38;2;95;175;255m\002alice\001\x1b[0m\002 › '
    assert lineedit.visible_width(ansi) == len('alice › ')


def test_completion(shell, server, tmp_path):
    (server.storage_dir / 'photos').mkdir()
    (server.storage_dir / 'report.pdf').write_bytes(b'x')
    (tmp_path / 'notes.txt').write_text('n')
    shell.online = {'alice', 'bob', 'carol'}
    assert 'download' in shell.complete('', 'dow') and 'whois' in shell.complete('', 'who')
    assert shell.complete('dm ', '') == ['bob', 'carol']
    assert shell.complete('say hi ', '@') == ['@bob', '@carol']
    assert shell.complete('download ', '') == ['photos/', 'report.pdf']
    assert shell.complete('cd ', '') == ['photos/']
    assert shell.complete('upload ', f'{tmp_path}/no') == [f'{tmp_path}/notes.txt']
    assert '--count' in shell.complete('ping ', '--')
    assert 'graphite' in shell.complete('theme ', '')


def test_unknown_command_suggestion(shell):
    assert 'did you mean ping' in run(shell, 'pnig')
    assert 'type help' in run(shell, 'zzzzzz')


def test_view_command(shell, server, tmp_path):
    (server.storage_dir / 'notes').mkdir()
    (server.storage_dir / 'notes' / 'script.py').write_text('def hello():\n    return "world"\n')
    (server.storage_dir / 'README.md').write_text('# Title\n\nSome **bold** text\n')
    (server.storage_dir / 'data.json').write_text('{"b": [1, 2], "a": "x"}')
    (server.storage_dir / 'photo.bin').write_bytes(b'\x89PNG\x00\x01\x02' * 10)
    (server.storage_dir / 'big.log').write_text('line\n' * 100_000)

    output = run(shell, 'view notes/script.py -P')
    assert 'def hello' in output and '/notes/script.py' in output and 'python' in output and '2 lines' in output
    rendered = run(shell, 'cat README.md -P')
    assert 'Title' in rendered and '**bold**' not in rendered and 'Markdown' in rendered
    assert '**bold**' in run(shell, 'view README.md --raw -P')
    pretty = run(shell, 'view data.json -P -N')
    assert '"b": [' in pretty and '"a": "x"' in pretty
    assert 'Binary file' in run(shell, 'view photo.bin -P')
    assert 'preview limited to the first 16.0 KB' in run(shell, 'view big.log -m 16K -P -N')
    assert 'no file named' in run(shell, 'view missing.txt')
    (tmp_path / 'local.txt').write_text('local content')
    assert 'local content' in run(shell, f'view --local {tmp_path}/local.txt -P')


def test_read_request(server, make_client):
    client = make_client(server)
    (server.storage_dir / 'a.txt').write_bytes(b'0123456789')
    meta, data = client.read_file('a.txt', limit=4)
    assert data == b'0123' and meta['truncated'] and meta['size'] == 10 and meta['path'] == '/a.txt'
    meta, data = client.read_file('/a.txt')
    assert data == b'0123456789' and not meta['truncated']
    with pytest.raises(Exception, match='invalid'):
        client.read_file('../users.json')


def test_decode_text():
    from zlink.viewer import decode_text
    assert decode_text('héllo'.encode()) == 'héllo'
    assert decode_text('héllo'.encode('latin-1')) == 'héllo'
    assert decode_text(b'\x00\x01\x02binary') is None
    assert decode_text(bytes(range(128, 256)) * 4) is None


def test_snapshot_capture(shell, tmp_path):
    shell.captures_dir = tmp_path / 'captures'
    was_recording = ui.console.record
    try:
        ui.console.record = False
        assert 'Recording started' in run(shell, 'snapshot')
        ui.console.print('something worth keeping')
        run(shell, 'snapshot')
        files = list((tmp_path / 'captures').glob('client-alice-*.svg'))
        assert len(files) == 1 and 'something' in files[0].read_text()
    finally:
        ui.console.record = was_recording


def test_capture_path():
    from pathlib import Path
    import re
    path = ui.capture_path(Path('/x/captures'), 'server')
    assert re.fullmatch(r'/x/captures/server-\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.svg', str(path))
    assert ui.capture_path(Path('/x'), 'client', 'bob').name.startswith('client-bob-')
