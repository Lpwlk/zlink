"""Line editing of the shell prompt: readline-safe colored prompts, persistent history and Tab completion.

Rich's ``Console.input`` prints the prompt itself then calls ``input('')``: readline does not know about the
prompt and erases it when it redraws the line (e.g. when backspacing further than what was typed). Here the
prompt is handed to ``input()`` as ANSI text, invisible escape codes being wrapped in ``\\001``/``\\002`` so that
readline (GNU or macOS libedit) computes the right width.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from rich.console import RenderableType
from rich.text import Text

from .ui import console

try:
    import readline
except ImportError:  # Windows
    readline = None

LIBEDIT = readline is not None and 'libedit' in (readline.__doc__ or '')
_ESCAPE = re.compile(r'(\x1b\[[0-9;?]*[A-Za-z])')

Completer = Callable[[str, str], list[str]]  # (line before the word, word) -> candidates


def to_ansi(renderable: RenderableType) -> str:
    """Render markup / Text to the ANSI string the console would print (no trailing newline)."""
    with console.capture() as capture:
        console.print(renderable, end='', soft_wrap=True)
    return capture.get()


def visible_width(ansi: str) -> int:
    return Text.from_ansi(ansi).cell_len


def readline_prompt(ansi: str) -> str:
    """Mark escape sequences as zero-width for readline."""
    return _ESCAPE.sub('\001\\1\002', ansi) if readline is not None else ansi


def line_buffer() -> str:
    return readline.get_line_buffer() if readline is not None else ''


class LineEditor:
    def __init__(self, history_file: Path | None = None, completer: Completer | None = None):
        self.history_file = history_file
        self.completer = completer
        self._matches: list[str] = []

    def install(self) -> None:
        if readline is None:
            return
        if self.completer is not None:
            readline.set_completer_delims(' \t\n')
            readline.set_completer(self._complete)
            readline.parse_and_bind('bind ^I rl_complete' if LIBEDIT else 'tab: complete')
        if self.history_file is not None:
            readline.set_history_length(1000)
            try:
                readline.read_history_file(self.history_file)
            except (OSError, ValueError):
                pass

    def save(self) -> None:
        if readline is None or self.history_file is None:
            return
        try:
            self.history_file.parent.mkdir(parents=True, exist_ok=True)
            readline.write_history_file(self.history_file)
        except OSError:
            pass

    def read(self, prompt_ansi: str) -> str:
        return input(readline_prompt(prompt_ansi))

    def _complete(self, text: str, state: int) -> str | None:
        if state == 0:
            before = readline.get_line_buffer()[:readline.get_begidx()]
            try:
                self._matches = sorted(set(c for c in self.completer(before, text) if c.startswith(text)))
            except Exception:  # a failing completion must never break the prompt
                self._matches = []
            if LIBEDIT and len(self._matches) == 1 and not self._matches[0].endswith('/'):
                self._matches[0] += ' '  # GNU readline appends it by itself, libedit does not
        return self._matches[state] if state < len(self._matches) else None
