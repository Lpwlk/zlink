"""Rich console, themes, logging and the shared building blocks (cards, meters, badges...)."""

from __future__ import annotations

import logging
import platform
from datetime import datetime
from pathlib import Path

from rich.box import Box
from rich.console import Console, Group, RenderableType
from rich.logging import RichHandler
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import __version__
from .themes import DEFAULT_THEME, THEMES, ThemeSpec

PROJECT = 'zlink'
STATUSES = ('online', 'away', 'busy')
STATUS_ICONS = {'online': '●', 'away': '◐', 'busy': '⊘'}
SPARK_BLOCKS = '▁▂▃▄▅▆▇█'

console = Console(log_path=False)
_theme: ThemeSpec = THEMES[DEFAULT_THEME]
console.push_theme(_theme.rich_theme)


# ---------------------------------------------------------------------- themes


def current_theme() -> ThemeSpec:
    return _theme


def set_theme(name: str) -> ThemeSpec:
    global _theme
    if name not in THEMES:
        raise ValueError(f'unknown theme "{name}", available themes: {", ".join(THEMES)}')
    console.pop_theme()
    _theme = THEMES[name]
    console.push_theme(_theme.rich_theme)
    return _theme


def box() -> Box:
    return _theme.box


def user_color(index: int) -> str:
    """Accent color of the n-th connected client in the current theme."""
    colors = _theme.user_colors
    return colors[(max(index, 1) - 1) % len(colors)]


def swatch(theme: ThemeSpec, block: str = '██') -> Text:
    text = Text()
    for color in theme.swatch:
        text.append(block, style=color)
    return text


# ---------------------------------------------------------------------- logging


def get_logger(name: str, logfile: Path | None = None, verbose: bool = False) -> logging.Logger:
    """Logger printing through the rich console and, optionally, to a plain text file."""
    logger = logging.getLogger(f'zlink.{name}')
    for handler in logger.handlers:
        handler.close()
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.propagate = False

    # markup stays off: log lines contain user controlled text (usernames, file names, chat).
    logger.addHandler(RichHandler(console=console, show_path=False, log_time_format='%X', markup=False))
    if logfile is not None:
        logfile.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(logfile, encoding='utf-8')
        file_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)-8s %(threadName)s | %(message)s'))
        logger.addHandler(file_handler)
    return logger


# ---------------------------------------------------------------------- cards & widgets


def _cell(value: object) -> RenderableType:
    if isinstance(value, (Text, Table, Group)):
        return value
    return Text(str(value))  # values may come from the network, never parse markup


def section(name: str, rows: dict[str, object]) -> Table:
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style='key', no_wrap=True)
    grid.add_column(style='value')
    grid.add_row(Text(name.upper(), style='section'), '')
    for key, value in rows.items():
        grid.add_row(key, _cell(value))
    return grid


def card(title: str | Text, sections: dict[str, dict[str, object]], subtitle: str | Text | None = None,
         border_style: str = 'border') -> Panel:
    """Panel made of titled sections, laid out side by side (at most 3 per row, 2 per row for 4 sections)."""
    parts = [section(name, rows) for name, rows in sections.items() if rows]
    per_row = 2 if len(parts) == 4 else 3
    body = Table.grid(padding=(0, 4))
    for _ in range(min(per_row, len(parts))):
        body.add_column(vertical='top')
    for start in range(0, len(parts), per_row):
        if start:
            body.add_row()
        body.add_row(*parts[start:start + per_row])
    return Panel(body, title=Text(title, style='title') if isinstance(title, str) else title, title_align='left',
                 subtitle=subtitle, subtitle_align='right', box=box(), border_style=border_style, padding=(1, 2),
                 expand=False)


def key_value_table(title: str, rows: dict[str, object]) -> Table:
    table = Table(title=title, title_style='title', box=box(), show_header=False, border_style='border')
    table.add_column(style='key')
    table.add_column(style='value')
    for key, value in rows.items():
        table.add_row(key, _cell(value))
    return table


def meter(used: float, total: float, width: int = 16, label: str | None = None) -> Text:
    """Horizontal gauge, colored from success to failure as it fills up."""
    fraction = min(1.0, used / total) if total else 0.0
    style = 'success' if fraction < 0.6 else 'warning' if fraction < 0.85 else 'failure'
    filled = round(fraction * width)
    return Text.assemble(('━' * filled, style), ('━' * (width - filled), 'meter.empty'),
                         (f' {label if label is not None else f"{fraction:.0%}"}', 'muted'))


def status_badge(status: str, message: str | None = None) -> Text:
    status = status if status in STATUSES else 'online'
    text = Text(f'{STATUS_ICONS[status]} {status}', style=f'status.{status}')
    if message:
        text.append(f'  “{message}”', style='muted')
    return text


def latency_style(ms: float) -> str:
    return 'latency.good' if ms < 20 else 'latency.fair' if ms < 150 else 'latency.bad'


def latency(ms: float) -> Text:
    return Text(f'{ms:.2f} ms', style=latency_style(ms))


def sparkline(values: list[float]) -> Text:
    if not values:
        return Text()
    low, high = min(values), max(values)
    span = (high - low) or 1
    text = Text()
    for value in values:
        text.append(SPARK_BLOCKS[round((value - low) / span * (len(SPARK_BLOCKS) - 1))], style=latency_style(value))
    return text


def color_chip(color: str, label: str | None = None) -> Text:
    return Text.assemble(('■■■ ', color), (label or color, 'muted'))


def header(side: str) -> None:
    uname = platform.uname()
    console.print(
        card(f'zlink {__version__} · {side}', 
            {
            'host': {
                'Node': uname.node,
                'Architecture': uname.machine,
                'System': f'{uname.system} {uname.release}',
                'Python': platform.python_version(),
            },
            'display': {
                'Theme': Text.assemble(swatch(_theme, '█'), f' {_theme.name}'),
                'Terminal': f'{console.width}×{console.height}, {console.color_system or "no"} colors',
            },
            }
        ),
        justify='center', 
    )


# ---------------------------------------------------------------------- SVG captures


def capture_path(directory: Path, side: str, label: str | None = None) -> Path:
    """Timestamped capture file name.svg"""
    stamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    name = '-'.join(part for part in (side, label, stamp) if part)
    return Path(directory) / f'{name}.svg'


def save_capture(target: Path, side: str, label: str | None = None) -> Path:
    """Save everything recorded so far as SVG; ``target`` is a folder (timestamped name) or a file path."""
    target = Path(target).expanduser()
    path = capture_path(target, side, label) if target.suffix.lower() != '.svg' else target
    path.parent.mkdir(parents=True, exist_ok=True)
    console.save_svg(str(path), title=f'{PROJECT} {side} · {datetime.now():%d/%m/%Y %H:%M}', clear=False)
    return path


# ---------------------------------------------------------------------- formatting


def format_duration(seconds: float) -> str:
    seconds = int(seconds)
    days, seconds = divmod(seconds, 86400)
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    return f'{days}d {hours:02d}:{minutes:02d}:{seconds:02d}' if days else f'{hours}:{minutes:02d}:{seconds:02d}'


def format_timestamp(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime('%d/%m/%Y %H:%M')


def format_clock(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime('%H:%M')
