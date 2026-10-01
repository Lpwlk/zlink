"""Previews of text files (server or local): syntax highlighting, Markdown, pretty JSON, paging with less."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from rich.console import Group, RenderableType
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

from . import ui
from .files import human_size
from .ui import console, format_timestamp

PREVIEW_SIZE = 256 * 1024
_MARKDOWN = {'.md', '.markdown'}


def decode_text(data: bytes) -> str | None:
    """``data`` as text, or None when it looks binary."""
    if b'\0' in data[:8192]:
        return None
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError as exc:
        if exc.start >= len(data) - 3:  # a character cut in half by the preview size limit
            try:
                return data[:exc.start].decode('utf-8')
            except UnicodeDecodeError:
                pass
    text = data.decode('latin-1')  # legacy 8-bit text, accepted when it is mostly printable
    printable = sum(ch.isprintable() or ch in '\n\r\t' for ch in text)
    return text if printable >= len(text) * 0.95 else None


def render(name: str, text: str, raw: bool = False, line_numbers: bool = True) -> RenderableType:
    suffix = Path(name).suffix.lower()
    code_theme = ui.current_theme().code_theme
    if suffix in _MARKDOWN and not raw:
        return Markdown(text, code_theme=code_theme)
    if suffix == '.json' and not raw:
        try:
            text = json.dumps(json.loads(text), indent=2, ensure_ascii=False)
        except ValueError:
            pass
    lexer = Syntax.guess_lexer(name, code=text)
    return Syntax(text, lexer, theme=code_theme, line_numbers=line_numbers, word_wrap=True,
                  background_color='default', indent_guides=False)


def preview(meta: dict, data: bytes, raw: bool = False, line_numbers: bool = True) -> RenderableType:
    """Framed preview of a file: header with its details, then its highlighted content (or why it is not shown)."""
    name = meta['name']
    text = decode_text(data)
    details = [human_size(meta['size'])]
    if meta.get('mtime'):
        details.append(f'modified {format_timestamp(meta["mtime"])}')
    if text is not None:
        details.append(f'{len(text.splitlines())} lines')
        lexer = Syntax.guess_lexer(name, code=text[:4096])
        details.append('Markdown' if Path(name).suffix.lower() in _MARKDOWN and not raw
                       else 'text' if lexer == 'default' else lexer)
    title = Text.assemble(('📄 ', ''), (meta.get('path') or name, 'title'))
    subtitle = Text(' · '.join(details), style='muted')
    if text is None:
        body = Text.assemble(('Binary file, no preview. ', 'warning'), ('Use ', 'muted'), ('download', 'key'),
                             (' to get it.', 'muted'))
    else:
        body = render(name, text, raw=raw, line_numbers=line_numbers)
        if meta.get('truncated'):
            body = Group(body, Text(f'\n… preview limited to the first {human_size(len(data))} of '
                                    f'{human_size(meta["size"])}', style='warning'))
    return Panel(body, title=title, title_align='left', subtitle=subtitle, subtitle_align='right', box=ui.box(),
                 border_style='border', padding=(0, 1))


def local_file(path: Path, limit: int = PREVIEW_SIZE) -> tuple[dict, bytes]:
    stat = path.stat()
    with path.open('rb') as f:
        data = f.read(limit)
    return {'name': path.name, 'path': str(path), 'size': stat.st_size, 'mtime': stat.st_mtime,
            'truncated': stat.st_size > len(data)}, data


def show(renderable: RenderableType, pager: bool = True) -> None:
    """Print, through ``less`` when the output is taller than the terminal (still recorded by --svg)."""
    less = shutil.which('less')
    if pager and less and sys.stdout.isatty() and console.is_terminal:
        if len(console.render_lines(renderable, console.options, pad=False)) >= console.height - 2:
            with console.capture() as capture:
                console.print(renderable)
            # -R colors, -F exit when it fits, -X keep the text on screen afterwards, -K Ctrl+C quits
            subprocess.run([less, '-RFXK'], input=capture.get().encode(), check=False)
            return
    console.print(renderable)
