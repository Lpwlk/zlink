"""Interactive rich-powered shell driving a :class:`~zlink.client.Client`."""

from __future__ import annotations

import argparse
import difflib
import glob
import os
import posixpath
import queue
import re
import shlex
import statistics
import threading
import time
from pathlib import Path
from typing import Callable

from rich.markup import escape
from rich.console import Group
from rich.panel import Panel
from rich.progress import (BarColumn, DownloadColumn, Progress, TextColumn, TimeRemainingColumn,
                           TransferSpeedColumn)
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

from . import debug, lineedit, sounds, ui, viewer
from .browser import FileBrowser, interactive_terminal
from .cards import server_card, user_card
from .client import AuthFailed, Client, RequestError, TransferError
from .files import human_size, parse_size
from .protocol import Message, ProtocolError
from .themes import THEMES
from .ui import STATUSES, console, format_clock, format_duration, format_timestamp


# ---------------------------------------------------------------------- argument parsing


class _HelpShown(Exception):
    """A command was called with -h, its help has been printed."""


class CommandParser(argparse.ArgumentParser):
    """argparse for shell commands: errors raise ValueError instead of exiting the program."""

    def __init__(self, prog: str, description: str):
        super().__init__(prog=prog, description=description, formatter_class=argparse.RawDescriptionHelpFormatter)

    def error(self, message: str):
        raise ValueError(f'{message}\n{self.format_usage().strip()}')

    def exit(self, status: int = 0, message: str | None = None):
        raise _HelpShown

    def print_help(self, file=None) -> None:
        console.print(Text(self.format_help().rstrip()))

    def parse(self, args: str) -> argparse.Namespace:
        try:
            return self.parse_args(shlex.split(args))
        except argparse.ArgumentTypeError as exc:
            raise ValueError(str(exc)) from None

    @property
    def arguments(self) -> str:
        usage = self.format_usage().strip().removeprefix(f'usage: {self.prog}').strip()
        return ' '.join(usage.removeprefix('[-h]').split())


def _positive_float(value: str) -> float:
    number = float(value)
    if number <= 0:
        raise argparse.ArgumentTypeError(f'must be positive: {value}')
    return number


PING = CommandParser('ping', 'Measure the round trip time to the server, like the ping command.')
PING.add_argument('-n', '--count', type=int, default=4, metavar='N',
                  help='number of pings, 0 to ping until Ctrl+C (default: 4)')
PING.add_argument('-i', '--interval', type=_positive_float, default=0.5, metavar='SEC',
                  help='seconds between pings (default: 0.5)')
PING.add_argument('-s', '--size', type=parse_size, default=0, metavar='SIZE',
                  help='payload echoed by the server, e.g. 64K (max 1M)')
PING.add_argument('-W', '--timeout', type=_positive_float, default=2.0, metavar='SEC',
                  help='seconds to wait for each answer (default: 2)')
PING.add_argument('-q', '--quiet', action='store_true', help='only print the statistics')

LS = CommandParser('ls', 'List a server folder, folders first.')
LS.add_argument('folder', nargs='?', default='.', help='folder to list (default: the current server folder)')
LS.add_argument('-s', '--sort', choices=['name', 'size', 'time'], default='name', metavar='KEY',
                help='sort by name, size or time (default: name)')
LS.add_argument('-r', '--reverse', action='store_true', help='reverse the order')

WHO = CommandParser('who', 'List connected users.')
WHO.add_argument('-s', '--sort', choices=['index', 'name', 'time', 'idle', 'status'], default='index', metavar='KEY',
                 help='sort by index, name, time, idle or status (default: index)')
WHO.add_argument('-r', '--reverse', action='store_true', help='reverse the order')

HISTORY = CommandParser('history', 'Show the last public chat messages kept by the server.')
HISTORY.add_argument('-n', '--count', type=int, default=20, metavar='N', help='number of messages (default: 20, max 100)')

VIEW = CommandParser('view', 'Preview a text file: syntax highlighting, Markdown rendering, pretty JSON.\n'
                             'Long files open in less (q to quit, / to search).')
VIEW.add_argument('file', help='server file (relative to the current server folder), or local file with --local')
VIEW.add_argument('-l', '--local', action='store_true', help='preview a local file instead of a server one')
VIEW.add_argument('-r', '--raw', action='store_true', help='show Markdown and JSON sources as they are')
VIEW.add_argument('-N', '--no-numbers', action='store_true', help='hide line numbers')
VIEW.add_argument('-P', '--no-pager', action='store_true', help='print directly, even when longer than the screen')
VIEW.add_argument('-m', '--max', type=parse_size, default=viewer.PREVIEW_SIZE, metavar='SIZE',
                  help='preview size limit (default: 256K, max 1M)')

PARSERS = {'ping': PING, 'ls': LS, 'who': WHO, 'history': HISTORY, 'view': VIEW}

# (category, aliases, arguments, description); the arguments of commands with a parser come from it.
HELP = [
    ('chat', 'say, s', '<message>', 'Message everyone (@name mentions)'),
    ('chat', 'me', '<action>', 'Emote, e.g. "me waves"'),
    ('chat', 'dm', '<user> <message>', 'Private message'),
    ('chat', 'history', None, 'Recent public messages'),
    ('users', 'who, w', None, 'Connected users'),
    ('users', 'whois', '<user>', 'Card of a user'),
    ('users', 'status', '[online|away|busy] [msg]', 'Show or set your presence'),
    ('users', 'card', '', 'Your own user card'),
    ('files', 'browse, b', '[local|server]', 'File browser (arrows, Enter, Tab, v)'),
    ('files', 'ls', None, 'List a server folder'),
    ('files', 'view, cat', None, 'Preview a text file'),
    ('files', 'cd', '[folder]', 'Change the server folder'),
    ('files', 'mkdir', '<folder>', 'Create a server folder'),
    ('files', 'upload, up', '[path ...]', 'Upload files (no argument: browser)'),
    ('files', 'download, dl', '[name] [dest]', 'Download a file (no argument: browser)'),
    ('server', 'ping, p', None, 'Latency, loss, jitter, throughput'),
    ('server', 'info, i', '', 'Server card'),
    ('server', 'shutdown', '', 'Stop the server (when allowed)'),
    ('shell', 'theme', '[name]', 'List or switch color themes'),
    ('shell', 'snapshot', '', 'Save the session as an SVG image'),
    ('shell', 'clear', '', 'Clear the screen'),
    ('shell', 'help, h, ?', '[command]', "This help, or a command's options"),
    ('shell', 'quit, exit, q', '', 'Disconnect and quit'),
    ('debug', 'stack', '', 'Call stacks of the client threads'),
    ('debug', 'trace', '[on|off]', 'Debug+ live call trace'),
]


def _transfer_progress() -> Progress:
    return Progress(
        TextColumn('{task.description}'),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
        transient=True,
    )


class Shell:
    def __init__(self, client: Client, username: str | None = None, history_file: Path | None = None,
                 captures_dir: Path | None = None):
        self.client = client
        self.username = username
        self.running = True
        self.remote_cwd = '/'  # current server folder
        self.local_cwd = Path.cwd()  # where the file browser opens on the local side
        self.status, self.status_message = 'online', None
        self.captures_dir = captures_dir or client.downloads_dir.parent / 'captures'
        self.online: set[str] = set()  # connected usernames, kept up to date by join / leave events
        self.editor = lineedit.LineEditor(history_file, self.complete)
        self._output_lock = threading.RLock()
        self._at_prompt = False  # True while the main thread waits in input()
        self._input_prefix = ''
        self._ctrl_c_hint = True
        commands: dict[tuple[str, ...], Callable[[str], None]] = {
            ('help', 'h', '?'): self.cmd_help,
            ('ping', 'p'): self.cmd_ping,
            ('info', 'i'): self.cmd_info,
            ('who', 'w'): self.cmd_who,
            ('whois',): self.cmd_whois,
            ('status',): self.cmd_status,
            ('say', 's'): self.cmd_say,
            ('me',): self.cmd_me,
            ('dm',): self.cmd_dm,
            ('history',): self.cmd_history,
            ('browse', 'b', 'files'): self.cmd_browse,
            ('ls',): self.cmd_ls,
            ('view', 'cat'): self.cmd_view,
            ('snapshot',): self.cmd_snapshot,
            ('cd',): self.cmd_cd,
            ('mkdir',): self.cmd_mkdir,
            ('upload', 'up'): self.cmd_upload,
            ('download', 'dl'): self.cmd_download,
            ('card',): self.cmd_card,
            ('theme',): self.cmd_theme,
            ('stack',): lambda args: debug.print_stacks('Client thread stacks'),
            ('trace',): self.cmd_trace,
            ('clear',): lambda args: console.clear(),
            ('shutdown',): self.cmd_shutdown,
            ('quit', 'exit', 'q'): self.cmd_quit,
        }
        self.commands = {alias: handler for aliases, handler in commands.items() for alias in aliases}
        self.command_names = [aliases[0] for aliases in commands]

    @property
    def color(self) -> str:
        return ui.user_color(self.client.welcome.get('index', 1))

    # ------------------------------------------------------------------ session

    def run(self) -> int:
        address = f'{self.client.host}:{self.client.port}'
        try:
            with console.status(f'Connecting to {address}...'):
                welcome = self.client.connect()
        except RequestError as exc:
            console.print(f'[failure]Connection refused by the server:[/] {escape(str(exc))}')
            return 1
        except (OSError, ProtocolError) as exc:
            console.print(f'[failure]Could not connect to {address}:[/] {escape(str(exc))}')
            return 1
        sounds.play(sounds.Sound.CONNECTED)
        console.print(f'Connected to [bold]{escape(welcome.get("server", "?"))}[/] ({address}), '
                      f'server version {escape(str(welcome.get("version")))}')

        if not self.authenticate():
            return 1
        self.client.on_event = self.on_event
        self.client.on_disconnect = self.on_disconnect
        self.online = {user['username'] for user in self.client.who()}
        self.welcome_panel()
        self.show_recent_messages()
        self.loop()
        return 0

    def welcome_panel(self) -> None:
        try:
            info = self.client.info()
        except RequestError:
            info = {}
        others = len(self.online) - 1
        body = Text.assemble(
            ('Welcome, ', 'title'), (self.client.username, f'bold {self.color}'), ('!', 'title'), '\n',
            (f'{others} other user{"s" * (others != 1)} online · {info.get("files", 0)} file(s) on the server · '
             f'up for {format_duration(info.get("uptime", 0))}', 'muted'), '\n\n',
            ('help', 'key'), ' commands   ', ('Tab', 'key'), ' completion   ', ('↑ ↓', 'key'), ' history   ',
            ('browse', 'key'), ' files   ', ('card', 'key'), ' your profile   ', ('Ctrl+D', 'key'), ' quit',
        )
        title = Text.assemble(('⚡ ', 'accent'), (self.client.welcome.get('server', self.client.host), 'title'))
        console.print(Panel(body, title=title, title_align='left', box=ui.box(), border_style=self.color,
                            padding=(1, 2), expand=False))

    def authenticate(self) -> bool:
        choices = ['login', 'register'] if self.client.auth_required else ['guest', 'login', 'register']
        if self.client.auth_required:
            console.print('[key]This server requires an account.[/]')
        try:
            while True:
                action = Prompt.ask('Login mode', choices=choices, default=choices[0], console=console)
                try:
                    if action == 'guest':
                        nickname = self.username or Prompt.ask('Nickname [muted](empty for guest)[/]', default='',
                                                               show_default=False, console=console)
                        self.client.join_as_guest(nickname)
                    else:
                        username = self.username or Prompt.ask('Username', console=console)
                        password = Prompt.ask('Password', password=True, console=console)
                        if action == 'register':
                            if password != Prompt.ask('Confirm password', password=True, console=console):
                                console.print('[failure]Passwords do not match[/]')
                                continue
                            self.client.register(username, password)
                        else:
                            self.client.login(username, password)
                    return True
                except AuthFailed as exc:
                    console.print(f'[failure]Authentication failed:[/] {escape(str(exc))}')
                    self.username = None
                    if exc.final:
                        console.print('[failure]Too many failed attempts, the server closed the connection.[/]')
                        return False
        except (KeyboardInterrupt, EOFError):
            console.print()
            self.client.close()
            return False
        except (OSError, ProtocolError) as exc:
            console.print(f'[failure]Connection lost during authentication:[/] {escape(str(exc))}')
            return False

    def show_recent_messages(self) -> None:
        try:
            messages = self.client.history(5)
        except RequestError:
            return  # older server without history
        if messages:
            console.rule(Text('recent messages', style='muted'), style='muted')
            for message in messages:
                console.print(self.chat_line(message))
            console.rule(style='muted')

    # ------------------------------------------------------------------ prompt

    def prompt_header(self, width: int | None = None) -> Text:
        """First line of the prompt decorator: ╭─ ● user ─ server ─ /folder ─ 3 online ──── 14:02 ─"""
        width = width or console.width
        b = ui.box()
        parts = {
            'user': Text.assemble((ui.STATUS_ICONS[self.status], f'status.{self.status}'), ' ',
                                  (self.client.username or '?', f'bold {self.color}')),
            'server': Text(f'{self.client.welcome.get("server", self.client.host)}:{self.client.port}', style='value'),
            'folder': Text(self.remote_cwd, style='dir'),
            'online': Text(f'{len(self.online)} online', style='muted'),
        }
        clock = Text(f' {time.strftime("%H:%M")} ', style='muted')
        for optional in (None, 'online', 'server'):  # drop details until the line fits
            parts.pop(optional, None)
            line = Text(f'{b.top_left}{b.top} ', style='border')
            for index, part in enumerate(parts.values()):
                if index:
                    line.append(f' {b.top} ', style='border')
                line.append_text(part)
            line.append(' ')
            if line.cell_len + clock.cell_len + 2 <= width:
                break
        line.append(b.top * max(0, width - line.cell_len - clock.cell_len - 1), style='border')
        line.append_text(clock)
        line.append(b.top, style='border')
        line.truncate(width)
        return line

    def input_prefix(self) -> Text:
        b = ui.box()
        return Text.assemble((f'{b.bottom_left}{b.bottom}', 'border'), ('› ', f'bold {self.color}'))

    def loop(self) -> None:
        self.editor.install()
        try:
            while self.running and self.client.connected:
                with self._output_lock:
                    console.print()
                    console.print(self.prompt_header())
                    self._input_prefix = lineedit.to_ansi(self.input_prefix())
                    self._at_prompt = True
                try:
                    line = self.editor.read(self._input_prefix)
                except KeyboardInterrupt:  # clears the line, like a regular shell
                    console.print()
                    if self._ctrl_c_hint:
                        console.print('[muted]Ctrl+D or [bold]quit[/] to leave[/]')
                        self._ctrl_c_hint = False
                    continue
                except EOFError:
                    console.print()
                    break
                finally:
                    self._at_prompt = False
                if self.client.connected:
                    self.execute(line)
        finally:
            self.editor.save()
            self.client.close()
            console.print('[muted]Disconnected.[/]')

    def notify(self, *renderables) -> None:
        """Print from another thread: above the prompt block, which is then redrawn with the text being typed."""
        with self._output_lock:
            if not (self._at_prompt and console.is_terminal and lineedit.readline is not None):
                for renderable in renderables:
                    console.print(renderable)
                return
            buffer = lineedit.line_buffer()
            typed = lineedit.visible_width(self._input_prefix) + Text(buffer).cell_len
            input_rows = 1 + max(0, typed - 1) // max(console.width, 1)
            # cursor is on the (last) input row: clear it, the other input rows, the header and the spacer line
            console.file.write('\r\x1b[2K' + '\x1b[1A\x1b[2K' * (input_rows + 1))
            for renderable in renderables:
                console.print(renderable)
            console.print()
            console.print(self.prompt_header())
            console.file.write(self._input_prefix + buffer)
            console.file.flush()

    # ------------------------------------------------------------------ completion

    def complete(self, before: str, text: str) -> list[str]:
        """Tab completion candidates for ``text``, ``before`` being the start of the line."""
        words = before.split()
        if not words:
            return self.command_names
        command = words[0].lower()
        others = sorted(self.online - {self.client.username})
        if text.startswith('@'):
            return [f'@{user}' for user in others]
        if text.startswith('-') and command in PARSERS:
            return [option for action in PARSERS[command]._actions for option in action.option_strings]
        match command:
            case 'dm' | 'whois' if len(words) == 1:
                return others
            case 'status' if len(words) == 1:
                return list(STATUSES)
            case 'theme':
                return list(THEMES)
            case 'browse' | 'b' | 'files':
                return ['local', 'server']
            case 'trace':
                return ['on', 'off']
            case 'help' | 'h' | '?':
                return self.command_names
            case 'view' | 'cat':
                return self._local_candidates(text) if '-l' in words or '--local' in words \
                    else self._remote_candidates(text)
            case 'cd' | 'ls' | 'mkdir':
                return self._remote_candidates(text, folders_only=True)
            case 'download' | 'dl':
                return self._remote_candidates(text) if len(words) == 1 else self._local_candidates(text)
            case 'upload' | 'up':
                return self._local_candidates(text)
        return []

    def _remote_candidates(self, text: str, folders_only: bool = False) -> list[str]:
        folder = text[:text.rfind('/') + 1]  # '' / 'photos/' / '/shared/'
        entries = self.client.list_dir(self.resolve_remote(folder or '.'))
        return [f'{folder}{e["name"]}{"/" if e["type"] == "dir" else ""}' for e in entries
                if e['type'] == 'dir' or not folders_only]

    @staticmethod
    def _local_candidates(text: str) -> list[str]:
        expanded = os.path.expanduser(text)
        return [text + match[len(expanded):] + ('/' if os.path.isdir(match) else '')
                for match in glob.glob(glob.escape(expanded) + '*')]

    def execute(self, line: str) -> None:
        name, _, args = line.strip().partition(' ')
        if not name:
            return
        handler = self.commands.get(name.lower())
        if handler is None:
            guess = difflib.get_close_matches(name.lower(), self.commands, n=1, cutoff=0.6)
            hint = f'did you mean [bold]{guess[0]}[/]?' if guess else 'type [bold]help[/] to list commands'
            console.print(f'[failure]Unknown command[/] {escape(name)!r}, {hint}')
            return
        try:
            handler(args.strip())
        except _HelpShown:
            pass
        except RequestError as exc:
            console.print(f'[failure]Server error:[/] {escape(str(exc))}')
        except (TransferError, ValueError) as exc:
            console.print(f'[failure]Error:[/] {escape(str(exc))}')
        except queue.Empty:
            console.print('[failure]The server did not answer in time[/]')
        except KeyboardInterrupt:
            console.print('[muted]Cancelled[/]')
        except (OSError, ProtocolError) as exc:
            if self.client.connected:
                console.print(f'[failure]Error:[/] {escape(str(exc))}')
            else:
                console.print(f'[failure]Connection lost:[/] {escape(str(exc))}')

    # ------------------------------------------------------------------ chat rendering & server events

    def chat_line(self, message: dict, to: str | None = None) -> Text:
        """One chat message: time, sender, text; emotes, private messages and @mentions are highlighted."""
        sender = message.get('sender', '?')
        mine = sender == self.client.username
        line = Text(format_clock(message.get('time') or time.time()) + ' ', style='muted')
        if message.get('private') or to:
            line.append(f'[dm → {to}] ' if to else '[dm] ', style='chat.private')
        if message.get('action'):
            line.append(f'* {sender} {message.get("text", "")}', style='chat.action')
        else:
            line.append('you' if mine else sender, style='chat.from')
            line.append(': ')
            line.append(message.get('text', ''))
        if not mine and self.client.username:
            line.highlight_regex(rf'@{re.escape(self.client.username)}\b', 'chat.mention')
        return line

    def mentions_me(self, text: str) -> bool:
        return bool(self.client.username and re.search(rf'@{re.escape(self.client.username)}\b', text))

    def on_event(self, msg: Message) -> None:
        stamp = (format_clock(time.time()) + ' ', 'muted')
        match msg.get('kind'):
            case 'chat':
                self.notify(self.chat_line(msg.fields))
                if self.mentions_me(msg.get('text', '')):
                    console.bell()
                    sounds.play(sounds.Sound.PRIVATE_MESSAGE)
                else:
                    sounds.play(sounds.Sound.PRIVATE_MESSAGE if msg.get('private') else sounds.Sound.MESSAGE)
            case 'join':
                self.online.add(msg.get('username'))
                self.notify(Text.assemble(stamp, (f'→ {msg.get("username")} joined', 'event')))
            case 'leave':
                self.online.discard(msg.get('username'))
                self.notify(Text.assemble(stamp, (f'← {msg.get("username")} left', 'event')))
            case 'status':
                self.notify(Text.assemble(stamp, (f'{msg.get("username")} is now ', 'event'),
                                            ui.status_badge(msg.get('status', 'online'), msg.get('message'))))
            case 'file':
                self.notify(Text.assemble(stamp, (f'{msg.get("sender")} uploaded {msg.get("path", msg.get("name"))} '
                                                    f'({human_size(msg.get("size", 0))})', 'event')))
            case 'shutdown':
                self.notify(Text.assemble(stamp, (f'Server shutting down: {msg.get("reason", "")}', 'failure')))

    def on_disconnect(self, exc: Exception) -> None:
        self.notify(Text.assemble(('Connection to the server lost', 'failure'), f' ({exc}), press Enter to exit'))

    # ------------------------------------------------------------------ help & display commands

    def cmd_help(self, args: str) -> None:
        if args:
            name = args.split()[0].lower()
            if name in PARSERS:
                PARSERS[name].print_help()
                return
            rows = [row for row in HELP if name in [alias.strip() for alias in row[1].split(',')]]
            if not rows:
                raise ValueError(f'unknown command {name!r}')
            _, aliases, arguments, description = rows[0]
            console.print(Text.assemble((aliases, 'key'), ' ', (arguments or '', 'value'), '\n  ', description))
            return
        console.print(self.help_view())

    def help_view(self) -> Group:
        """All commands with their arguments, grouped by category, as aligned borderless columns."""
        groups: dict[str, list[tuple[str, str, str]]] = {}
        for category, aliases, arguments, description in HELP:
            if arguments is None:
                arguments = PARSERS[aliases.split(',')[0]].arguments
            groups.setdefault(category, []).append((aliases, arguments, description))

        def block(categories: list[str], width: int) -> Table:
            grid = Table.grid(padding=(0, 2), expand=True)
            grid.add_column(style='section', no_wrap=True, width=6)
            grid.add_column(max_width=max(24, width // 2 - 8), overflow='fold')  # long option lists wrap, aligned
            grid.add_column(style='muted', ratio=1, overflow='fold')
            for category in categories:
                for index, (aliases, arguments, description) in enumerate(groups[category]):
                    command = Text(aliases, style='key')
                    if arguments:
                        command.append(f' {arguments}', style='value')
                    grid.add_row(category if index == 0 else '', command, description)
            return grid

        body = block(list(groups), console.width)
        keys = Text.assemble(('Tab', 'key'), ' complete · ', ('↑↓', 'key'), ' history · ', ('Ctrl+C', 'key'),
                             ' clear line · ', ('Ctrl+D', 'key'), ' quit · ', ('help <cmd>', 'key'), ' all options',
                             style='muted')
        return Group(Text('Commands', style='title'), body, keys)

    def cmd_theme(self, args: str) -> None:
        if args:
            theme = ui.set_theme(args.strip().lower())
            console.print(Text.assemble('Theme switched to ', (theme.name, 'title'), ' ', ui.swatch(theme)))
            return
        table = Table(title='Themes', title_style='title', box=ui.box(), border_style='border')
        table.add_column('Name', style='key')
        table.add_column('Colors')
        table.add_column('Description')
        for theme in THEMES.values():
            name = f'{theme.name} (current)' if theme is ui.current_theme() else theme.name
            table.add_row(name, ui.swatch(theme), theme.description)
        table.caption = 'theme <name> to switch, or start the client with --theme <name>'
        console.print(table)

    # ------------------------------------------------------------------ server & users

    def cmd_ping(self, args: str) -> None:
        opts = PING.parse(args)
        if opts.count < 0:
            raise ValueError('the count must be positive, or 0 to ping until Ctrl+C')
        if opts.size > 1024 * 1024:
            raise ValueError('the payload size is limited to 1M')
        target = f'{self.client.host}:{self.client.port}'
        extra = f' · {human_size(opts.size)} payload' if opts.size else ''
        until = ' · Ctrl+C to stop' if opts.count == 0 else ''
        console.print(Text.assemble(('PING ', 'title'), (target, 'bold'), (f'{extra}{until}', 'muted')))

        times: list[float] = []
        lost = seq = 0
        started = time.perf_counter()
        try:
            while opts.count == 0 or seq < opts.count:
                seq += 1
                try:
                    ms = self.client.ping(size=opts.size, timeout=opts.timeout)
                except queue.Empty:
                    lost += 1
                    if not opts.quiet:
                        console.print(Text.assemble((f'  seq={seq:<4} ', 'muted'), ('timeout', 'failure')))
                else:
                    times.append(ms)
                    if not opts.quiet:
                        bar = Text('▮' * min(30, max(1, round(ms / 2))), style=ui.latency_style(ms))
                        console.print(Text.assemble((f'  seq={seq:<4} ', 'muted'), ui.latency(ms), '  ', bar))
                if opts.count == 0 or seq < opts.count:
                    time.sleep(opts.interval)
        except KeyboardInterrupt:
            console.print('[muted]  interrupted[/]')
        self._ping_summary(target, times, lost, opts.size, time.perf_counter() - started)

    def _ping_summary(self, target: str, times: list[float], lost: int, size: int, elapsed: float) -> None:
        sent = len(times) + lost
        loss = lost / sent if sent else 0
        packets = Text.assemble(f'{sent} sent · {len(times)} received · ',
                                (f'{loss:.0%} loss', 'success' if not lost else 'warning' if loss < 0.5 else 'failure'))
        rows: dict[str, object] = {'Packets': packets}
        if times:
            rows['Latency'] = Text.assemble('min ', ui.latency(min(times)), ' · avg ',
                                            ui.latency(statistics.fmean(times)), ' · max ', ui.latency(max(times)))
            rows['Jitter'] = f'{statistics.pstdev(times):.2f} ms (standard deviation)'
            rows['Trend'] = ui.sparkline(times[-40:])
            if size:
                rows['Throughput'] = f'{human_size(2 * size * len(times) / max(elapsed, 1e-9))}/s (echo, both ways)'
        console.print(ui.card(f'Ping statistics · {target}', {'results': rows}))

    def cmd_info(self, args: str) -> None:
        console.print(server_card(self.client.info()))

    def cmd_who(self, args: str) -> None:
        opts = WHO.parse(args)
        users = self.client.who()
        keys = {
            'index': lambda u: u['index'],
            'name': lambda u: u['username'].lower(),
            'time': lambda u: -u.get('connected_for', 0),
            'idle': lambda u: u.get('idle', 0),
            'status': lambda u: STATUSES.index(u.get('status', 'online')),
        }
        users.sort(key=keys[opts.sort], reverse=opts.reverse)
        table = Table(title=f'Connected users ({len(users)})', title_style='title', box=ui.box(), border_style='border')
        table.add_column('#', style='muted', justify='right')
        table.add_column('User', no_wrap=True)
        table.add_column('Status')
        table.add_column('Address', style='value')
        table.add_column('Connected', style='key', justify='right')
        table.add_column('Idle', style='muted', justify='right')
        table.add_column('Msgs', justify='right')
        table.add_column('↑ / ↓', style='muted', justify='right')
        for user in users:
            name = Text(user['username'], style=f'bold {ui.user_color(user["index"])}')
            name.append(' ★' if user.get('registered') else ' ☆', style='key' if user.get('registered') else 'muted')
            if user['username'] == self.client.username:
                name.append(' (you)', style='muted')
            table.add_row(str(user['index']), name, ui.status_badge(user.get('status', 'online'), user.get('status_message')),
                          user['address'], format_duration(user.get('connected_for', 0)),
                          format_duration(user.get('idle', 0)), str(user.get('messages', 0)),
                          f'{human_size(user.get("bytes_up", 0))} / {human_size(user.get("bytes_down", 0))}')
        table.caption = '★ registered account · ☆ guest · whois <user> for details'
        console.print(table)

    def cmd_whois(self, args: str) -> None:
        if not args:
            raise ValueError('usage: whois <user>')
        user = self.client.whois(args.split()[0].lstrip('@'))
        console.print(user_card(user, self._local_section() if user['username'] == self.client.username else None))

    def cmd_card(self, args: str) -> None:
        console.print(user_card(self.client.whois(self.client.username), self._local_section()))

    def _local_section(self) -> dict[str, object]:
        w = self.client.welcome
        try:
            rtt = ui.latency(self.client.ping(timeout=2))
        except (queue.Empty, RequestError):
            rtt = Text('timeout', style='failure')
        return {
            'Server': f'{w.get("server")} ({self.client.host}:{self.client.port})',
            'Latency': rtt,
            'Session ID': w.get('session'),
            'Local address': '{}:{}'.format(*self.client.conn.sock.getsockname()[:2]),
            'Server folder': self.remote_cwd,
            'Downloads': str(self.client.downloads_dir).replace(str(Path.home()), '~', 1),
            'Theme': Text.assemble(ui.swatch(ui.current_theme(), '█'), f' {ui.current_theme().name}'),
            'Debug+': debug.describe(),
        }

    def cmd_status(self, args: str) -> None:
        if not args:
            console.print(Text.assemble('Your status: ', ui.status_badge(self.status, self.status_message)))
            return
        status, _, message = args.partition(' ')
        if status.lower() not in STATUSES:
            raise ValueError(f'usage: status [{"|".join(STATUSES)}] [message]')
        result = self.client.set_status(status.lower(), message.strip() or None)
        self.status, self.status_message = result['status'], result['message']
        console.print(Text.assemble('Your status is now ', ui.status_badge(self.status, self.status_message)))

    # ------------------------------------------------------------------ chat

    def cmd_say(self, args: str) -> None:
        if not args:
            raise ValueError('usage: say <message>')
        self.client.chat(args)
        console.print(self.chat_line({'sender': self.client.username, 'text': args}))

    def cmd_me(self, args: str) -> None:
        if not args:
            raise ValueError('usage: me <action>')
        self.client.chat(args, action=True)
        console.print(self.chat_line({'sender': self.client.username, 'text': args, 'action': True}))

    def cmd_dm(self, args: str) -> None:
        user, _, text = args.partition(' ')
        if not user or not text.strip():
            raise ValueError('usage: dm <user> <message>')
        self.client.chat(text.strip(), to=user.lstrip('@'))
        console.print(self.chat_line({'sender': self.client.username, 'text': text.strip()}, to=user.lstrip('@')))

    def cmd_history(self, args: str) -> None:
        opts = HISTORY.parse(args)
        messages = self.client.history(max(1, min(opts.count, 100)))
        if not messages:
            console.print('[muted]No public message yet.[/]')
            return
        console.rule(Text(f'last {len(messages)} message(s)', style='muted'), style='muted')
        for message in messages:
            console.print(self.chat_line(message))
        console.rule(style='muted')

    # ------------------------------------------------------------------ files

    def resolve_remote(self, path: str) -> str:
        """Absolute server path of ``path``, relative to the current server folder."""
        resolved = posixpath.normpath(posixpath.join(self.remote_cwd, path))
        return '/' + resolved.lstrip('/')

    def cmd_browse(self, args: str) -> None:
        self.open_browser('remote' if args.lower() in ('server', 'remote', 's', 'r') else 'local')

    def open_browser(self, start: str) -> None:
        if not interactive_terminal():
            raise ValueError('the file browser needs an interactive terminal, use "upload <path>" or "download <name>"')
        FileBrowser(self, start).run()

    def cmd_ls(self, args: str) -> None:
        opts = LS.parse(args)
        path = self.resolve_remote(opts.folder)
        entries = self.client.list_dir(path)
        if not entries:
            console.print(f'[muted]{escape(path)} is empty, use [bold]upload[/] or [bold]browse[/] to add files.[/]')
            return
        keys = {'name': lambda e: e['name'].lower(), 'size': lambda e: e['size'] or 0, 'time': lambda e: e['mtime']}
        folders = sorted((e for e in entries if e['type'] == 'dir'), key=keys[opts.sort], reverse=opts.reverse)
        files = sorted((e for e in entries if e['type'] != 'dir'), key=keys[opts.sort], reverse=opts.reverse)
        table = Table(title=f'Server folder {escape(path)}', title_style='title', box=ui.box(), border_style='border')
        table.add_column('Name')
        table.add_column('Size', style='key', justify='right')
        table.add_column('Modified', style='muted')
        for entry in folders + files:
            if entry['type'] == 'dir':
                name, size = Text(f'📁 {entry["name"]}/', style='dir'), ''
            else:
                name, size = Text(f'📄 {entry["name"]}', style='value'), human_size(entry['size'])
            table.add_row(name, size, format_timestamp(entry['mtime']))
        table.caption = (f'{len(folders)} folder(s) · {len(files)} file(s) · '
                         f'{human_size(sum(e["size"] for e in files))}')
        console.print(table)

    def cmd_view(self, args: str) -> None:
        opts = VIEW.parse(args)
        limit = max(1, min(opts.max, 1024 * 1024))
        if opts.local:
            meta, data = viewer.local_file(Path(opts.file).expanduser(), limit)
        else:
            meta, data = self.client.read_file(self.resolve_remote(opts.file), limit)
        viewer.show(viewer.preview(meta, data, raw=opts.raw, line_numbers=not opts.no_numbers), pager=not opts.no_pager)

    def cmd_snapshot(self, args: str) -> None:
        if not console.record:
            console.record = True
            console.print('[key]Recording started.[/] Run [bold]snapshot[/] again to save everything printed from '
                          'now on as an SVG image (or start the client with --svg).')
            return
        path = ui.save_capture(self.captures_dir, 'client', self.client.username)
        console.print(f'[success]Snapshot saved[/] {escape(str(path))}')

    def cmd_cd(self, args: str) -> None:
        path = self.resolve_remote(args or '/')
        self.client.list_dir(path)  # fails if the folder does not exist
        self.remote_cwd = path

    def cmd_mkdir(self, args: str) -> None:
        if not args:
            raise ValueError('usage: mkdir <folder>')
        console.print(f'[success]Created[/] {escape(self.client.mkdir(self.resolve_remote(args)))}')

    def cmd_upload(self, args: str) -> None:
        paths = shlex.split(args)
        if not paths:
            self.open_browser('local')
            return
        self.upload_files([Path(path).expanduser() for path in paths])

    def cmd_download(self, args: str) -> None:
        parts = shlex.split(args)
        if not parts:
            self.open_browser('remote')
            return
        if len(parts) > 2:
            raise ValueError('usage: download [name] [dest]')
        self.download_files([self.resolve_remote(parts[0])], parts[1] if len(parts) == 2 else None)

    def upload_files(self, paths: list[Path]) -> int:
        """Upload files to the current server folder with progress bars, return how many succeeded."""
        done = 0
        for path in paths:
            if not path.is_file():
                console.print(f'[failure]Not a file:[/] {escape(str(path))}')
                continue
            try:
                with _transfer_progress() as progress:
                    task = progress.add_task(f'↑ {escape(path.name)}', total=path.stat().st_size)
                    result = self.client.upload(path, self.remote_cwd,
                                                lambda sent, total: progress.update(task, completed=sent))
            except (RequestError, TransferError) as exc:
                console.print(f'[failure]Upload of {escape(path.name)} failed:[/] {escape(str(exc))}')
                continue
            done += 1
            sounds.play(sounds.Sound.TRANSFER_DONE)
            console.print(f'[success]Uploaded[/] {escape(path.name)} → [bold]{escape(result["path"])}[/] '
                          f'({human_size(result["size"])}, sha256 {result["sha256"][:12]}…)')
        return done

    def download_files(self, remote_paths: list[str], dest: str | None = None) -> int:
        """Download server files with progress bars, return how many succeeded."""
        done = 0
        for remote_path in remote_paths:
            name = posixpath.basename(remote_path)
            try:
                with _transfer_progress() as progress:
                    task = progress.add_task(f'↓ {escape(name)}', total=None)
                    path = self.client.download(remote_path, dest, lambda received, total: progress.update(
                        task, completed=received, total=total))
            except (RequestError, TransferError) as exc:
                console.print(f'[failure]Download of {escape(name)} failed:[/] {escape(str(exc))}')
                continue
            done += 1
            sounds.play(sounds.Sound.TRANSFER_DONE)
            console.print(f'[success]Downloaded[/] {escape(remote_path)} → {escape(str(path))} '
                          f'({human_size(path.stat().st_size)})')
        return done

    # ------------------------------------------------------------------ misc

    def cmd_trace(self, args: str) -> None:
        match args.lower():
            case 'on':
                debug.enable()
            case 'off':
                debug.disable()
            case '':
                pass
            case _:
                raise ValueError('usage: trace [on|off]')
        console.print(f'Debug+ call trace: [bold]{escape(debug.describe())}[/]')

    def cmd_shutdown(self, args: str) -> None:
        if Confirm.ask('Really shut the server down for everyone?', default=False, console=console):
            self.client.shutdown_server()

    def cmd_quit(self, args: str) -> None:
        self.running = False
