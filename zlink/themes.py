"""The five zlink color themes, selected with ``--theme NAME`` or the ``theme`` shell command."""

from __future__ import annotations

from dataclasses import dataclass, field

from rich import box
from rich.box import Box
from rich.theme import Theme

# Semantic style names used across the package, every theme defines all of them.
STYLE_KEYS = (
    'title', 'section', 'border', 'accent', 'key', 'value', 'muted', 'success', 'warning', 'failure', 'event',
    'chat.from', 'chat.private', 'chat.action', 'chat.mention', 'pane.local', 'pane.remote', 'dir',
    'status.online', 'status.away', 'status.busy', 'latency.good', 'latency.fair', 'latency.bad', 'meter.empty',
)


@dataclass(frozen=True)
class ThemeSpec:
    name: str
    description: str
    styles: dict[str, str]
    user_colors: tuple[str, ...]
    box: Box = box.ROUNDED
    code_theme: str = 'monokai'  # pygments style used to highlight file previews
    rich_theme: Theme = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        accent, success, failure, warning = (self.styles[k] for k in ('accent', 'success', 'failure', 'warning'))
        derived = {
            # rich built-in styles following the palette
            'prompt.default': f'bold {_color(accent)}',
            'prompt.choices': _color(accent),
            'repr.number': f'bold {_color(success)}',
            'logging.level.debug': _color(self.styles['muted']),
            'logging.level.info': _color(accent),
            'logging.level.warning': _color(warning),
            'logging.level.error': _color(failure),
            'logging.level.critical': f'bold reverse {_color(failure)}',
            'log.time': self.styles['muted'],
            'rule.line': _color(accent),
            'bar.complete': _color(accent),
            'bar.finished': _color(success),
            'bar.pulse': _color(accent),
            'progress.percentage': _color(accent),
            'progress.download': _color(success),
            'progress.data.speed': _color(warning),
            'progress.remaining': _color(self.styles['key']),
            'status.spinner': _color(accent),
            'table.header': f'bold {_color(self.styles["key"])}',
            'tree.line': self.styles['muted'],
        }
        object.__setattr__(self, 'rich_theme', Theme({**derived, **self.styles}))

    @property
    def swatch(self) -> tuple[str, ...]:
        """The main colors of the theme, shown in ``--help`` and ``zlink themes``."""
        return tuple(_color(self.styles[k]) for k in ('accent', 'key', 'value', 'success', 'failure', 'chat.private'))


def _color(style: str) -> str:
    """Last word of a style definition, i.e. its color ('bold #ff79c6' -> '#ff79c6')."""
    return style.split()[-1]


THEMES: dict[str, ThemeSpec] = {theme.name: theme for theme in (
    ThemeSpec(
        name='ocean',
        description='calm blues and cyans with rounded borders, the original look',
        styles={
            'title': 'bold italic #5fafff', 'section': 'bold #00afff', 'border': '#3a6ea5', 'accent': '#00afff',
            'key': '#ffd75f', 'value': '#87d7ff', 'muted': 'grey50',
            'success': 'bold #5fd75f', 'warning': '#ffaf00', 'failure': 'bold #ff5f5f', 'event': 'italic #5fd7d7',
            'chat.from': 'bold #87afff', 'chat.private': 'bold #d787ff', 'chat.action': 'italic #5fd7d7',
            'chat.mention': 'bold black on #ffd75f',
            'pane.local': 'bold #5f87ff', 'pane.remote': 'bold #d75fd7', 'dir': 'bold #5f87ff',
            'status.online': '#5fd75f', 'status.away': '#ffaf00', 'status.busy': '#ff5f5f',
            'latency.good': '#5fd75f', 'latency.fair': '#ffaf00', 'latency.bad': 'bold #ff5f5f',
            'meter.empty': 'grey23',
        },
        user_colors=('#5fafff', '#5fd787', '#d787ff', '#5fd7d7', '#ffd75f', '#ff875f', '#87afff', '#afd75f'),
        box=box.ROUNDED,
        code_theme='one-dark',
    ),
    ThemeSpec(
        name='dracula',
        description='the famous dark palette: purple, pink and neon green, heavy edges',
        styles={
            'title': 'bold #ff79c6', 'section': 'bold #bd93f9', 'border': '#6272a4', 'accent': '#bd93f9',
            'key': '#8be9fd', 'value': '#f8f8f2', 'muted': '#6272a4',
            'success': 'bold #50fa7b', 'warning': '#ffb86c', 'failure': 'bold #ff5555', 'event': 'italic #8be9fd',
            'chat.from': 'bold #ff79c6', 'chat.private': 'bold #bd93f9', 'chat.action': 'italic #f1fa8c',
            'chat.mention': 'bold #282a36 on #ff79c6',
            'pane.local': 'bold #8be9fd', 'pane.remote': 'bold #ff79c6', 'dir': 'bold #bd93f9',
            'status.online': '#50fa7b', 'status.away': '#f1fa8c', 'status.busy': '#ff5555',
            'latency.good': '#50fa7b', 'latency.fair': '#ffb86c', 'latency.bad': 'bold #ff5555',
            'meter.empty': '#44475a',
        },
        user_colors=('#ff79c6', '#8be9fd', '#50fa7b', '#bd93f9', '#f1fa8c', '#ffb86c', '#ff5555', '#6be5fd'),
        box=box.HEAVY_EDGE,
        code_theme='dracula',
    ),
    ThemeSpec(
        name='nord',
        description='arctic, north-bluish tones: soft, low contrast and easy on the eyes',
        styles={
            'title': 'bold #88c0d0', 'section': 'bold #81a1c1', 'border': '#4c566a', 'accent': '#88c0d0',
            'key': '#81a1c1', 'value': '#e5e9f0', 'muted': '#616e88',
            'success': 'bold #a3be8c', 'warning': '#ebcb8b', 'failure': 'bold #bf616a', 'event': 'italic #8fbcbb',
            'chat.from': 'bold #88c0d0', 'chat.private': 'bold #b48ead', 'chat.action': 'italic #8fbcbb',
            'chat.mention': 'bold #2e3440 on #ebcb8b',
            'pane.local': 'bold #81a1c1', 'pane.remote': 'bold #b48ead', 'dir': 'bold #88c0d0',
            'status.online': '#a3be8c', 'status.away': '#ebcb8b', 'status.busy': '#bf616a',
            'latency.good': '#a3be8c', 'latency.fair': '#ebcb8b', 'latency.bad': 'bold #bf616a',
            'meter.empty': '#3b4252',
        },
        user_colors=('#88c0d0', '#a3be8c', '#b48ead', '#ebcb8b', '#8fbcbb', '#d08770', '#81a1c1', '#5e81ac'),
        box=box.SQUARE,
        code_theme='nord',
    ),
    ThemeSpec(
        name='sunset',
        description='warm oranges, pinks and golds with double borders',
        styles={
            'title': 'bold #ff8700', 'section': 'bold #ff5f87', 'border': '#d75f00', 'accent': '#ff8700',
            'key': '#ffaf5f', 'value': '#ffd7af', 'muted': '#af875f',
            'success': 'bold #d7d75f', 'warning': '#ffd700', 'failure': 'bold #ff005f', 'event': 'italic #ff87af',
            'chat.from': 'bold #ffaf00', 'chat.private': 'bold #ff5f87', 'chat.action': 'italic #ff87af',
            'chat.mention': 'bold #1c1c1c on #ffaf00',
            'pane.local': 'bold #ffaf00', 'pane.remote': 'bold #ff5f87', 'dir': 'bold #ff8700',
            'status.online': '#d7d75f', 'status.away': '#ffaf00', 'status.busy': '#ff005f',
            'latency.good': '#d7d75f', 'latency.fair': '#ffaf00', 'latency.bad': 'bold #ff005f',
            'meter.empty': '#5f3a2a',
        },
        user_colors=('#ff8700', '#ff5f87', '#ffd75f', '#d787af', '#ffaf87', '#d75f5f', '#ff875f', '#d7af5f'),
        box=box.DOUBLE_EDGE,
        code_theme='gruvbox-dark',
    ),
    ThemeSpec(
        name='matrix',
        description='green phosphor terminal with heavy borders, follow the white rabbit',
        styles={
            'title': 'bold #00ff5f', 'section': 'bold #5fff87', 'border': '#008700', 'accent': '#00ff5f',
            'key': '#5fff87', 'value': '#d7ffd7', 'muted': '#3a7a3a',
            'success': 'bold #00ff00', 'warning': '#d7ff00', 'failure': 'bold #ff3030', 'event': 'italic #00d787',
            'chat.from': 'bold #87ff87', 'chat.private': 'bold #00ffaf', 'chat.action': 'italic #5fd75f',
            'chat.mention': 'bold #000000 on #00ff5f',
            'pane.local': 'bold #00d75f', 'pane.remote': 'bold #87ff00', 'dir': 'bold #00ff87',
            'status.online': '#00ff00', 'status.away': '#d7ff00', 'status.busy': '#ff3030',
            'latency.good': '#00ff00', 'latency.fair': '#d7ff00', 'latency.bad': 'bold #ff3030',
            'meter.empty': '#003a00',
        },
        user_colors=('#00ff5f', '#87ff00', '#00ffaf', '#5fd75f', '#d7ff5f', '#00d7af', '#afff87', '#00af5f'),
        box=box.HEAVY,
        code_theme='native',
    ),
    ThemeSpec(
        name='midnight',
        description='deep night blues and violets (Tokyo Night style), soft on dark terminals',
        styles={
            'title': 'bold #7aa2f7', 'section': 'bold #bb9af7', 'border': '#3b4261', 'accent': '#7aa2f7',
            'key': '#7dcfff', 'value': '#c0caf5', 'muted': '#565f89',
            'success': 'bold #9ece6a', 'warning': '#e0af68', 'failure': 'bold #f7768e', 'event': 'italic #73daca',
            'chat.from': 'bold #7aa2f7', 'chat.private': 'bold #bb9af7', 'chat.action': 'italic #73daca',
            'chat.mention': 'bold #1a1b26 on #e0af68',
            'pane.local': 'bold #7aa2f7', 'pane.remote': 'bold #bb9af7', 'dir': 'bold #7dcfff',
            'status.online': '#9ece6a', 'status.away': '#e0af68', 'status.busy': '#f7768e',
            'latency.good': '#9ece6a', 'latency.fair': '#e0af68', 'latency.bad': 'bold #f7768e',
            'meter.empty': '#292e42',
        },
        user_colors=('#7aa2f7', '#9ece6a', '#bb9af7', '#7dcfff', '#e0af68', '#ff9e64', '#73daca', '#f7768e'),
        box=box.ROUNDED,
        code_theme='material',
    ),
    ThemeSpec(
        name='graphite',
        description='neutral greys with colorblind-safe accents (Okabe-Ito palette), sober and readable',
        styles={
            'title': 'bold #e4e4e4', 'section': 'bold #e69f00', 'border': '#585858', 'accent': '#56b4e9',
            'key': '#e69f00', 'value': '#e4e4e4', 'muted': '#8a8a8a',
            'success': 'bold #009e73', 'warning': '#f0e442', 'failure': 'bold #d55e00', 'event': 'italic #56b4e9',
            'chat.from': 'bold #56b4e9', 'chat.private': 'bold #cc79a7', 'chat.action': 'italic #bcbcbc',
            'chat.mention': 'bold #1c1c1c on #f0e442',
            'pane.local': 'bold #56b4e9', 'pane.remote': 'bold #cc79a7', 'dir': 'bold #56b4e9',
            'status.online': '#009e73', 'status.away': '#f0e442', 'status.busy': '#d55e00',
            'latency.good': '#009e73', 'latency.fair': '#f0e442', 'latency.bad': 'bold #d55e00',
            'meter.empty': '#3a3a3a',
        },
        user_colors=('#56b4e9', '#e69f00', '#009e73', '#cc79a7', '#f0e442', '#0072b2', '#d55e00', '#bcbcbc'),
        box=box.ROUNDED,
        code_theme='lightbulb',
    ),
    ThemeSpec(
        name='terminal',
        description="your terminal's own 16 ANSI colors: follows its palette, works everywhere",
        styles={
            'title': 'bold bright_blue', 'section': 'bold cyan', 'border': 'blue', 'accent': 'bright_blue',
            'key': 'yellow', 'value': 'default', 'muted': 'bright_black',
            'success': 'bold green', 'warning': 'yellow', 'failure': 'bold red', 'event': 'italic cyan',
            'chat.from': 'bold bright_blue', 'chat.private': 'bold magenta', 'chat.action': 'italic cyan',
            'chat.mention': 'bold reverse yellow',
            'pane.local': 'bold blue', 'pane.remote': 'bold magenta', 'dir': 'bold blue',
            'status.online': 'green', 'status.away': 'yellow', 'status.busy': 'red',
            'latency.good': 'green', 'latency.fair': 'yellow', 'latency.bad': 'bold red',
            'meter.empty': 'bright_black',
        },
        user_colors=('bright_blue', 'bright_green', 'bright_magenta', 'bright_cyan', 'yellow', 'blue', 'green', 'magenta'),
        box=box.ROUNDED,
        code_theme='ansi_dark',
    ),
)}

DEFAULT_THEME = 'ocean'
