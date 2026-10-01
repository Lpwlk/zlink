"""Command line entry point: ``zlink server [options]`` / ``zlink client [options]``."""

from __future__ import annotations

import argparse
import io
import os
import re
import signal
import sys
from pathlib import Path

from rich.columns import Columns
from rich.console import Console, Group
from rich.markup import escape
from rich.prompt import Prompt
from rich.text import Text
from rich.traceback import install

from . import __version__, debug, sounds, ui
from .cards import user_card
from .client import Client
from .files import parse_size
from .server import Server
from .shell import Shell
from .themes import DEFAULT_THEME, THEMES
from .ui import console, header

DEFAULT_DATA_DIR = Path(os.environ.get('zlink_HOME') or Path.home() / '.zlink')
CLIENT_DIR = DEFAULT_DATA_DIR / 'client'  # downloads/, captures/, history


def _raise_keyboard_interrupt(signum, frame):
    raise KeyboardInterrupt


def default_theme() -> str:
    theme = os.environ.get('zlink_THEME', DEFAULT_THEME)
    return theme if theme in THEMES else DEFAULT_THEME


def themes_help(colored: bool | None = None) -> str:
    """The themes block of --help, with color swatches when printed on a color terminal."""
    if colored is None:
        colored = sys.stdout.isatty() and 'NO_COLOR' not in os.environ
    buffer = io.StringIO()
    out = Console(file=buffer, force_terminal=colored, color_system='truecolor' if colored else None, width=200,
                  highlight=False)
    out.print(f'themes (--theme NAME, or the zlink_THEME variable; default: {DEFAULT_THEME}):')
    for theme in THEMES.values():
        line = Text(f'  {theme.name:<9}')
        line.append_text(ui.swatch(theme, '██') if colored else Text('      '))
        line.append(f'  {theme.description}')
        out.print(line)
    out.print('  preview them all with: zlink themes')
    return buffer.getvalue()


def _epilog() -> str:
    return f'{themes_help()}\nAuthor: Lpwlk'


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='zlink',
        description='Rich CLI TCP client & server: chat, file transfers, server monitoring.',
        epilog=_epilog(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('-H', '--host', default='127.0.0.1', help='server address (default: %(default)s)')
    common.add_argument('-p', '--port', type=int, default=8000, help='server port (default: %(default)s)')
    common.add_argument('--theme', choices=list(THEMES), default=default_theme(), metavar='NAME',
                        help=f'color theme: {", ".join(THEMES)} (default: %(default)s, see below)')
    common.add_argument('--mute', action='store_true', help='disable sound notifications')
    common.add_argument('--svg', nargs='?', const='auto', metavar='PATH',
                        help='record the session and save it as an SVG image on exit, as a timestamped file in the '
                             'captures/ folder (server: <data-dir>/captures, client: ~/.zlink/client/captures), '
                             'or at PATH (a .svg file or a folder)')
    common.add_argument('-v', '--verbose', action='count', default=0,
                        help='-v: debug logs and tracebacks with local variables, -vv: debug+ mode')
    common.add_argument('--debug-plus', nargs='?', const='-', metavar='FILE',
                        help='debug+ mode: live call tree of every zlink function, on the console or '
                             'appended to FILE (colored, follow it with `tail -f FILE`)')
    common.add_argument('--wire-trace', action='store_true',
                        help='include TCP socket reads/writes and byte counts in debug+ (no payload contents)')

    modes = parser.add_subparsers(dest='mode', metavar='{server,client,themes}')
    server = modes.add_parser('server', parents=[common], help='run a server', epilog=_epilog(),
                              formatter_class=argparse.RawDescriptionHelpFormatter,
                              description='Run a zlink server. Use --host 0.0.0.0 to accept LAN clients.')
    server.add_argument('--auth', action='store_true', help='require clients to login or register')
    server.add_argument('--data-dir', type=Path, default=DEFAULT_DATA_DIR / 'server',
                        help='accounts, logs and stored files location (default: %(default)s)')
    server.add_argument('--max-clients', type=int, default=16, help='default: %(default)s')
    server.add_argument('--max-upload', type=parse_size, default='2G', metavar='SIZE',
                        help='maximum uploaded file size, e.g. 500M or 2G (default: 2G)')
    server.add_argument('--allow-shutdown', action='store_true', help='let clients shut the server down')

    client = modes.add_parser('client', parents=[common], help='connect to a server', epilog=_epilog(),
                              formatter_class=argparse.RawDescriptionHelpFormatter,
                              description='Connect to a zlink server with an interactive shell.')
    client.add_argument('-u', '--user', help='username (or guest nickname) to use')
    client.add_argument('--downloads', type=Path, default=CLIENT_DIR / 'downloads',
                        help='where downloaded files are saved (default: %(default)s)')

    themes = modes.add_parser('themes', help='preview the color themes',
                              description='Preview the color themes with sample cards, chat and ping output.')
    themes.add_argument('name', nargs='?', choices=list(THEMES), help='only preview this theme')
    return parser


def preview_themes(name: str | None = None) -> None:
    sample_user = {
        'username': 'loic', 'index': 1, 'registered': True, 'status': 'away', 'status_message': 'back in 5',
        'address': '192.168.1.138:51034', 'connected_for': 5025, 'idle': 42, 'messages': 12,
        'uploads': 3, 'bytes_up': 5_300_000, 'downloads': 1, 'bytes_down': 820_000,
    }
    for theme in [THEMES[name]] if name else THEMES.values():
        ui.set_theme(theme.name)
        console.rule(Text.assemble((f' {theme.name} ', 'title'), ui.swatch(theme), (f'  {theme.description} ', 'muted')),
                     style='border')
        demo = Group(
            Text.assemble(('14:02 ', 'muted'), ('alice', 'chat.from'), ': the report is on the server ',
                          ('@loic', 'chat.mention')),
            Text.assemble(('14:03 ', 'muted'), ('* bob waves', 'chat.action')),
            Text.assemble(('14:03 ', 'muted'), ('[dm] ', 'chat.private'), ('carol', 'chat.from'), ': lunch?'),
            Text.assemble(('14:04 ', 'muted'), ('→ dave joined', 'event')),
            Text(),
            Text.assemble(('  seq=1  ', 'muted'), ui.latency(0.42), '   ', ('seq=2  ', 'muted'), ui.latency(48.1),
                          '   ', ('seq=3  ', 'muted'), ui.latency(310.7)),
            Text.assemble(('  trend  ', 'muted'), ui.sparkline([3, 5, 2, 8, 13, 7, 4, 21, 6, 3])),
            Text(),
            Text.assemble(('  📁 photos/', 'pane.local'), '   ', ('📁 shared/', 'pane.remote'), '   ',
                          ('report.pdf', 'value'), ('  1.2 MB', 'key')),
            Text.assemble('  ', ui.meter(3, 16, label='3/16 clients'), '   ', ui.meter(0.9, 1, label='disk 90%')),
            Text.assemble('  ', ui.status_badge('online'), '  ', ui.status_badge('away'), '  ', ui.status_badge('busy'),
                          '   ', ('✔ uploaded', 'success'), '  ', ('⚠ warning', 'warning'), '  ', ('✗ failed', 'failure')),
        )
        console.print(Columns([user_card(sample_user), demo], padding=(0, 4)))
        console.print()
    ui.set_theme(default_theme())


def legacy_data_hint() -> None:
    """Point RichSockets users (the former project name) to their previous data folder."""
    legacy = Path.home() / '.richsockets'
    if legacy.is_dir() and not DEFAULT_DATA_DIR.exists() and not os.environ.get('zlink_HOME'):
        console.print(f'[warning]Data of RichSockets (the former name of zlink) found in {legacy}.[/] '
                      f'Move it to keep your accounts, files and history: [bold]mv {legacy} {DEFAULT_DATA_DIR}[/] '
                      f'[muted](client downloads now go to {CLIENT_DIR / "downloads"})[/]')


def start_debug_plus(destination: str, mode: str, wire_trace: bool = False) -> None:
    where = debug.enable(destination, wire_trace=wire_trace)
    hints = [f'call trace {where}']
    if wire_trace:
        hints.append('TCP socket byte trace enabled (payload hidden)')
    if debug.install_stack_signal():
        hints.append(f'thread stacks: kill -USR1 {os.getpid()}')
    if mode == 'client':
        hints.append('shell commands: stack, trace on|off')
    console.print(f'[key]Debug+ mode[/] - {escape(" · ".join(hints))}')


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    argv = sys.argv[1:] if argv is None else argv
    args = parser.parse_args(argv)
    if args.mode is None:
        mode = Prompt.ask('Select zlink execution mode', choices=['server', 'client'], default='client',
                          console=console)
        args = parser.parse_args([mode, *argv])
    if args.mode == 'themes':
        preview_themes(args.name)
        return 0

    ui.set_theme(args.theme)
    if args.verbose >= 2 and args.debug_plus is None:
        args.debug_plus = '-'
    if args.wire_trace and args.debug_plus is None:
        args.debug_plus = '-'
    verbose = bool(args.verbose or args.debug_plus)
    install(console=console, show_locals=verbose)
    if args.mute:
        sounds.enabled = False
    if args.svg:
        console.record = True
    header(args.mode)
    legacy_data_hint()
    if args.debug_plus:
        start_debug_plus(args.debug_plus, args.mode, wire_trace=args.wire_trace)

    try:
        if args.mode == 'server':
            signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)  # `kill` stops the server gracefully
            Server(
                host=args.host,
                port=args.port,
                data_dir=args.data_dir,
                auth=args.auth,
                max_clients=args.max_clients,
                max_upload=args.max_upload,
                allow_shutdown=args.allow_shutdown,
                verbose=verbose,
            ).serve_forever()
            return 0
        client = Client(args.host, args.port, downloads_dir=args.downloads)
        return Shell(client, username=args.user, history_file=CLIENT_DIR / 'history',
                     captures_dir=CLIENT_DIR / 'captures').run()
    finally:
        if args.svg:
            default = args.data_dir / 'captures' if args.mode == 'server' else CLIENT_DIR / 'captures'
            path = ui.save_capture(default if args.svg == 'auto' else Path(args.svg), args.mode, args.user
                                   if args.mode == 'client' else None)
            console.print(f'[muted]Session saved as {escape(str(path))}[/]')
