"""Keyboard driven file browser of the client shell: a Local tab and a Server tab, switched with Tab."""

from __future__ import annotations

import os
import posixpath
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.prompt import Prompt
from rich.style import Style
from rich.table import Table
from rich.text import Text

from .client import RequestError
from .files import human_size
from . import ui, viewer
from .ui import console, format_timestamp

if TYPE_CHECKING:
    from .shell import Shell

# ---------------------------------------------------------------------- keyboard

_ESCAPES = {
    '[A': 'up', '[B': 'down', '[C': 'right', '[D': 'left', '[H': 'home', '[F': 'end',
    '[1~': 'home', '[4~': 'end', '[5~': 'pgup', '[6~': 'pgdn',
    'OA': 'up', 'OB': 'down', 'OC': 'right', 'OD': 'left', 'OH': 'home', 'OF': 'end',
}
_SPECIAL = {'\r': 'enter', '\n': 'enter', '\t': 'tab', ' ': 'space', '\x7f': 'backspace', '\x08': 'backspace',
            '\x1b': 'esc'}
_WINDOWS = {'H': 'up', 'P': 'down', 'K': 'left', 'M': 'right', 'G': 'home', 'O': 'end', 'I': 'pgup', 'Q': 'pgdn'}


def interactive_terminal() -> bool:
    return sys.stdin.isatty() and console.is_terminal


if os.name == 'nt':
    import msvcrt

    def read_key() -> str:
        """Block until a key is pressed and return its name ('up', 'enter', 'a'...)."""
        ch = msvcrt.getwch()
        if ch in ('\x00', '\xe0'):
            return _WINDOWS.get(msvcrt.getwch(), '')
        if ch == '\x03':
            raise KeyboardInterrupt
        return _SPECIAL.get(ch, ch)
else:
    import select
    import termios
    import tty

    def read_key() -> str:
        """Block until a key is pressed and return its name ('up', 'enter', 'a'...)."""
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            data = os.read(fd, 1)
            if data == b'\x1b' and select.select([fd], [], [], 0.05)[0]:
                data += os.read(fd, 8)  # rest of an escape sequence (arrows...)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        key = data.decode(errors='ignore')
        if len(key) > 1 and key.startswith('\x1b'):
            return _ESCAPES.get(key[1:], '')
        return _SPECIAL.get(key, key)


# ---------------------------------------------------------------------- panes


@dataclass(frozen=True)
class Entry:
    name: str
    is_dir: bool
    size: int | None = None
    mtime: float | None = None


PARENT = Entry('..', True)


class LocalPane:
    label = 'Local'
    color = 'pane.local'  # theme style
    verb = 'upload'

    def __init__(self, cwd: Path):
        self.cwd = cwd
        self.show_hidden = False
        self.entries: list[Entry] = []

    @property
    def location(self) -> str:
        home = Path.home()
        if self.cwd == home:
            return '~'
        if self.cwd.is_relative_to(home):
            return f'~/{self.cwd.relative_to(home)}'
        return str(self.cwd)

    def load(self, cwd: Path | None = None) -> None:
        """List ``cwd`` (default: current folder) and move into it. Raises OSError, leaving the pane unchanged."""
        cwd = (cwd or self.cwd).resolve()
        entries = []
        for path in cwd.iterdir():
            if path.name.startswith('.') and not self.show_hidden:
                continue
            try:
                stat = path.stat()
                is_dir = path.is_dir()
            except OSError:
                continue  # broken symlink, permission denied...
            entries.append(Entry(path.name, is_dir, None if is_dir else stat.st_size, stat.st_mtime))
        entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))
        self.cwd = cwd
        self.entries = ([PARENT] if cwd.parent != cwd else []) + entries

    def open(self, entry: Entry) -> None:
        self.load(self.cwd.parent if entry is PARENT else self.cwd / entry.name)

    def parent(self) -> str | None:
        """Go up one folder, return the name of the folder we left."""
        if self.cwd.parent == self.cwd:
            return None
        left = self.cwd.name
        self.load(self.cwd.parent)
        return left

    def path(self, entry: Entry) -> Path:
        return self.cwd / entry.name


class RemotePane:
    label = 'Server'
    color = 'pane.remote'  # theme style
    verb = 'download'

    def __init__(self, shell: Shell):
        self.shell = shell
        self.entries: list[Entry] = []

    @property
    def cwd(self) -> str:
        return self.shell.remote_cwd

    location = cwd

    def load(self, cwd: str | None = None) -> None:
        """List ``cwd`` (default: current folder) and move into it. Raises RequestError, leaving the pane unchanged."""
        cwd = cwd or self.cwd
        entries = [Entry(e['name'], e['type'] == 'dir', e['size'], e['mtime']) for e in self.shell.client.list_dir(cwd)]
        self.shell.remote_cwd = cwd
        self.entries = ([PARENT] if cwd != '/' else []) + entries

    def open(self, entry: Entry) -> None:
        self.load(self.shell.resolve_remote(entry.name))

    def parent(self) -> str | None:
        if self.cwd == '/':
            return None
        left = posixpath.basename(self.cwd)
        self.load(posixpath.dirname(self.cwd))
        return left

    def path(self, entry: Entry) -> str:
        return self.shell.resolve_remote(entry.name)


# ---------------------------------------------------------------------- browser

HINTS = {
    'local': '↑↓ move  ⏎ open/upload  ← back  v view  space mark  a mark all  u upload  . hidden  ~ home',
    'remote': '↑↓ move  ⏎ open/download  ← back  v view  space mark  a mark all  d download  n new folder  r refresh',
}


class FileBrowser:
    def __init__(
        self,
        shell: Shell,
        start: str = 'local',
        read_key: Callable[[], str] = read_key,
        ask: Callable[[str], str] | None = None,
    ):
        self.shell = shell
        self.panes = {'local': LocalPane(shell.local_cwd), 'remote': RemotePane(shell)}
        self.loaded = set()
        self.active = start
        self.cursor = {'local': 0, 'remote': 0}
        self.offset = {'local': 0, 'remote': 0}
        self.marked: dict[str, set[str]] = {'local': set(), 'remote': set()}
        self.status = Text()
        self.pending: tuple | None = None  # action waiting for a y/n confirmation
        self.read_key = read_key
        self.ask = ask or (lambda prompt: Prompt.ask(prompt, console=console))

    @property
    def pane(self) -> LocalPane | RemotePane:
        return self.panes[self.active]

    @property
    def current(self) -> Entry | None:
        entries = self.pane.entries
        return entries[self.cursor[self.active]] if entries else None

    def run(self) -> None:
        try:
            self._ensure_loaded(self.active)
            while self.shell.client.connected:
                action = self._interact()
                if action is None:
                    return
                self._perform(action)
        finally:
            self.shell.local_cwd = self.panes['local'].cwd

    # ------------------------------------------------------------------ interaction loop

    def _interact(self) -> tuple | None:
        """Handle keys in a live display until an action needs the full terminal (prompt, progress bars)."""
        with Live(self._render(), console=console, auto_refresh=False, transient=True) as live:
            while True:
                action = self._on_key(self.read_key())
                if action is not None:
                    return None if action[0] == 'quit' else action
                if not self.shell.client.connected:
                    return None
                live.update(self._render(), refresh=True)

    def _on_key(self, key: str) -> tuple | None:
        if self.pending is not None:
            action, self.pending = self.pending, None
            if key in ('y', 'Y', 'enter'):
                return action
            self._set_status('Cancelled', 'muted')
            return None

        pane, entries, current = self.pane, self.pane.entries, self.current
        self.status = Text()
        match key:
            case 'up' | 'k':
                self._move(-1)
            case 'down' | 'j':
                self._move(1)
            case 'pgup':
                self._move(-self._page_size())
            case 'pgdn':
                self._move(self._page_size())
            case 'home' | 'g':
                self._move(-len(entries))
            case 'end' | 'G':
                self._move(len(entries))
            case 'enter' | 'right' | 'l':
                if current is not None and current.is_dir:
                    self._navigate(lambda: pane.open(current))
                elif current is not None or self.marked[self.active]:
                    self._ask_transfer()
            case 'left' | 'backspace' | 'h':
                self._navigate(pane.parent)
            case 'tab':
                self.active = 'remote' if self.active == 'local' else 'local'
                self._ensure_loaded(self.active)
            case 'space':
                if current is not None and not current.is_dir:
                    self.marked[self.active] ^= {current.name}
                    self._move(1)
                elif current is not None:
                    self._set_status('Only files can be marked, press Enter to open a folder', 'muted')
            case 'a':
                files = {e.name for e in entries if not e.is_dir}
                marks = self.marked[self.active]
                self.marked[self.active] = set() if files <= marks else files
            case 'u' if self.active == 'local':
                self._ask_transfer()
            case 'd' if self.active == 'remote':
                self._ask_transfer()
            case 'v':
                if current is not None and not current.is_dir:
                    return ('view', current)
                self._set_status('Select a file to preview it', 'muted')
            case 'n' if self.active == 'remote':
                return ('mkdir',)
            case 'r':
                self._navigate(pane.load, keep_cursor=True)
            case '.' if self.active == 'local':
                pane.show_hidden = not pane.show_hidden
                self._navigate(pane.load, keep_cursor=True)
                self._set_status(f'Hidden files {"shown" if pane.show_hidden else "hidden"}', 'muted')
            case '~' if self.active == 'local':
                self._navigate(lambda: pane.load(Path.home()))
            case 'q' | 'Q' | 'esc':
                return ('quit',)
        return None

    def _move(self, delta: int) -> None:
        count = len(self.pane.entries)
        if count:
            self.cursor[self.active] = max(0, min(count - 1, self.cursor[self.active] + delta))

    def _navigate(self, change: Callable[[], str | None], keep_cursor: bool = False) -> None:
        """Run a pane change (open, parent, reload) and reset the cursor, reporting errors in the status line."""
        previous = self.current.name if keep_cursor and self.current else None
        try:
            left = change()
        except (OSError, RequestError) as exc:
            self._set_status(f'Cannot open this folder: {exc}', 'failure')
            return
        self.marked[self.active] &= {e.name for e in self.pane.entries} if keep_cursor else set()
        self.cursor[self.active] = self.offset[self.active] = 0
        self._select(left or previous)

    def _select(self, name: str | None) -> None:
        for index, entry in enumerate(self.pane.entries):
            if entry.name == name:
                self.cursor[self.active] = index

    def _ensure_loaded(self, name: str) -> None:
        if name not in self.loaded:
            self.loaded.add(name)
            try:
                self.panes[name].load()
            except (OSError, RequestError) as exc:  # the current folder vanished or is not readable
                if not self.shell.client.connected:
                    raise
                fallback = Path.home() if name == 'local' else '/'
                self._set_status(f'{exc}, showing {fallback} instead', 'failure')
                self.panes[name].load(fallback)

    def _ask_transfer(self) -> None:
        pane = self.pane
        items = [e for e in pane.entries if e.name in self.marked[self.active] and not e.is_dir]
        if not items and self.current is not None and not self.current.is_dir:
            items = [self.current]
        if not items:
            self._set_status('Select a file, or mark several with space', 'muted')
            return
        what = f'"{items[0].name}"' if len(items) == 1 else f'{len(items)} files'
        if self.active == 'local':
            question = f'Upload {what} to the server folder {self.shell.remote_cwd} ?'
        else:
            question = f'Download {what} to {self.shell.client.downloads_dir} ?'
        self.pending = ('transfer', items)
        self.status = Text.assemble((question, 'bold'), ('  [y/n]', 'key'))

    def _set_status(self, message: str, style: str) -> None:
        self.status = Text(message, style=style)

    # ------------------------------------------------------------------ actions needing the full terminal

    def _perform(self, action: tuple) -> None:
        try:
            match action:
                case ('transfer', items):
                    self._transfer(items)
                case ('mkdir',):
                    self._mkdir()
                case ('view', entry):
                    self._view(entry)
        except KeyboardInterrupt:
            self._set_status('Cancelled', 'muted')

    def _transfer(self, items: list[Entry]) -> None:
        pane = self.pane
        paths = [pane.path(entry) for entry in items]
        if self.active == 'local':
            done = self.shell.upload_files(paths)
            where, other = self.shell.remote_cwd, 'remote'
        else:
            done = self.shell.download_files(paths)
            where, other = str(self.shell.client.downloads_dir), 'local'
        verb = 'Uploaded' if self.active == 'local' else 'Downloaded'
        self._set_status(f'{verb} {done}/{len(items)} file(s) to {where}', 'success' if done == len(items) else 'failure')
        self.marked[self.active].clear()
        if other in self.loaded:  # the other side now holds new files
            try:
                self.panes[other].load()
            except (OSError, RequestError):
                pass

    def _view(self, entry: Entry) -> None:
        try:
            if self.active == 'local':
                meta, data = viewer.local_file(self.pane.path(entry))
            else:
                meta, data = self.shell.client.read_file(self.pane.path(entry))
        except (OSError, RequestError) as exc:
            self._set_status(f'Cannot preview {entry.name}: {exc}', 'failure')
            return
        viewer.show(viewer.preview(meta, data))

    def _mkdir(self) -> None:
        name = self.ask('New folder name').strip()
        if not name:
            return
        try:
            path = self.shell.client.mkdir(self.shell.resolve_remote(name))
        except (RequestError, ValueError) as exc:
            self._set_status(f'Cannot create the folder: {exc}', 'failure')
            return
        self._navigate(self.pane.load, keep_cursor=True)
        self._select(posixpath.basename(path))
        self._set_status(f'Created {path}', 'success')

    # ------------------------------------------------------------------ rendering

    def _page_size(self) -> int:
        return max(5, console.size.height - 10)

    def _render(self) -> Panel:
        pane, name = self.pane, self.active
        entries, marks = pane.entries, self.marked[name]
        height = self._page_size()
        cursor = self.cursor[name]
        if cursor < self.offset[name]:
            self.offset[name] = cursor
        elif cursor >= self.offset[name] + height:
            self.offset[name] = cursor - height + 1
        offset = self.offset[name]

        table = Table(box=None, expand=True, padding=(0, 1), header_style='muted')
        table.add_column('', width=1, no_wrap=True)
        table.add_column('Name', ratio=1, no_wrap=True, overflow='ellipsis')
        table.add_column('Size', justify='right', width=9, no_wrap=True)
        table.add_column('Modified', width=16, no_wrap=True)
        if not entries:
            table.add_row('', Text('(empty folder)', style='muted'), '', '')
        for index in range(offset, min(offset + height, len(entries))):
            entry = entries[index]
            if entry.is_dir:
                label = Text(f'📁 {entry.name}' + ('' if entry is PARENT else '/'), style=pane.color)
            else:
                label = Text(f'📄 {entry.name}')
            table.add_row(
                Text('●', style='success') if entry.name in marks else '',
                label,
                human_size(entry.size) if entry.size is not None else '',
                format_timestamp(entry.mtime) if entry.mtime else '',
                style='reverse' if index == cursor else None,
            )

        tabs = Text()
        for key, tab in self.panes.items():
            active = console.get_style(tab.color) + Style(reverse=True, bold=True)
            tabs.append(f' {tab.label} ', style=active if key == name else 'muted')
            tabs.append(' ')
        title = Text.assemble(tabs, (pane.location, 'bold'))

        info = [f'{cursor + 1}/{len(entries)}' if entries else '0 items']
        if marks:
            info.append(f'{len(marks)} marked')
        if name == 'local':
            info.append(f'uploads go to {self.shell.remote_cwd}')
        else:
            info.append(f'downloads go to {self.shell.client.downloads_dir}')
        footer = Text.assemble((' · '.join(info), 'muted'))
        hints = Text(f'{HINTS[name]}  tab switch  q quit', style='muted')
        return Panel(Group(table, Text(), self.status, footer, hints), title=title, title_align='left',
                     border_style=pane.color, box=ui.box())
