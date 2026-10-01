"""Debug+ mode: a live, thread aware call tree of zlink functions, and thread stack snapshots.

The call tracer relies on ``sys.settrace`` restricted to the package source files, so it slows the
program down and cannot run together with a debugger using the same hook (pdb, older debugpy).

Reading the trace::

    00:12:03.114 session-1   ▶ server.Server._serve_session()                 a call with traced children
    00:12:03.115 session-1   │ ● protocol.Connection.recv() → <ping #4>  0.02 ms   a call without traced child
    00:12:03.115 session-1   │ ● protocol.Connection.send(type=ok, ...)  0.03 ms
    00:12:03.115 session-1   │   ↻ ×41 more  (1.2 ms)                        identical consecutive calls, collapsed
    00:12:03.116 session-1   │ ✗ server.Server._on_download(...) ✗ RequestError: no file named ...
    00:12:03.200 session-1   ◀ server.Server._serve_session  88.1 ms         end of a call with children
"""

from __future__ import annotations

import dis
import signal
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path, PurePath
from types import FrameType

from rich.console import Console
from rich.text import Text
from rich.tree import Tree

from .protocol import Message, set_wire_trace
from . import ui
from .ui import console

PACKAGE_DIR = str(Path(__file__).resolve().parent)
_THIS_FILE = str(Path(__file__).resolve())

# Functions left out of the trace: hot loops, rendering and trivial accessors.
QUIET = {
    'protocol.Connection._recv_exact', 'protocol.Connection.peer', 'protocol.Message.get', 'protocol.Message.__getitem__',
    'protocol._trace_wire',
    'files.human_size', 'files.display_path',
    'browser.FileBrowser._render', 'browser.FileBrowser._page_size', 'browser.FileBrowser.pane',
    'browser.FileBrowser.current', 'browser.LocalPane.location', 'browser.RemotePane.cwd',
    'client.Client.auth_required',
}
QUIET_MODULES = {'ui', 'debug'}
SKIPPED_NAMES = {'<lambda>', '<genexpr>', '<listcomp>', '<dictcomp>', '<setcomp>', '<module>'}
SECRET_NAMES = {'password', 'passwd', 'secret'}
_RETURN_OPCODES = {dis.opmap[name] for name in ('RETURN_VALUE', 'RETURN_CONST', 'YIELD_VALUE') if name in dis.opmap}


# ---------------------------------------------------------------------- readable reprs


def short_repr(value: object, limit: int = 48) -> str:
    if isinstance(value, Message):
        parts = [value.type.value]
        if value.get('id') is not None:
            parts.append(f'#{value.get("id")}')
        if value.get('reply_to') is not None:
            parts.append(f'↩{value.get("reply_to")}')
        if value.get('kind'):
            parts.append(str(value.get('kind')))
        if value.payload:
            parts.append(f'+{len(value.payload)}B')
        return f'<{" ".join(parts)}>'
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, (bytes, bytearray)):
        return repr(bytes(value)) if len(value) <= 16 else f'<{len(value)} bytes>'
    if isinstance(value, PurePath):
        text = str(value).replace(str(Path.home()), '~', 1)
        return text if len(text) <= limit else '…' + text[-(limit - 1):]  # the end of a path is the useful part
    if isinstance(value, float):
        return repr(round(value, 3))
    if isinstance(value, dict):
        value = {k: '•••' if k in SECRET_NAMES else v for k, v in value.items()}
    module = type(value).__module__ or ''
    if module.startswith('zlink') or module == 'socket':
        name = getattr(value, 'username', None)
        return f'<{type(value).__name__}{f" {name}" if isinstance(name, str) else ""}>'
    try:
        text = repr(value)
    except Exception:
        text = f'<{type(value).__name__}>'
    if text.startswith('<') and ' object at 0x' in text:
        return f'<{type(value).__name__}>'
    return text if len(text) <= limit else text[:limit - 1] + '…'


def _call_arguments(frame: FrameType) -> str:
    code = frame.f_code
    count = code.co_argcount + code.co_kwonlyargcount
    count += bool(code.co_flags & 0x04) + bool(code.co_flags & 0x08)  # *args, **kwargs
    parts = []
    for name in code.co_varnames[:count]:
        if name in ('self', 'cls') or name not in frame.f_locals:
            continue
        value = frame.f_locals[name]
        if isinstance(value, bytes) and not value:
            continue  # empty payloads, present on almost every Connection.send call
        if name in SECRET_NAMES:
            parts.append(f'{name}=•••')
        elif code.co_flags & 0x08 and name == code.co_varnames[count - 1] and isinstance(value, dict):
            parts.extend(f'{k}={"•••" if k in SECRET_NAMES else short_repr(v)}' for k, v in value.items())
        else:
            parts.append(f'{name}={short_repr(value)}')
    return ', '.join(parts)


def _label(frame: FrameType) -> str:
    code = frame.f_code
    qualname = getattr(code, 'co_qualname', None)  # Python 3.11+
    if qualname is None:
        qualname = code.co_name
        owner = frame.f_locals.get('self') if code.co_argcount else None
        if owner is not None:
            qualname = f'{type(owner).__name__}.{qualname}'
    return f'{Path(code.co_filename).stem}.{qualname}'


# ---------------------------------------------------------------------- call tracer


@dataclass
class _Call:
    frame: FrameType
    label: str
    args: str
    depth: int
    start: float
    header_printed: bool = False
    exception: BaseException | None = None


@dataclass
class _ThreadState:
    stack: list[_Call] = field(default_factory=list)
    busy: bool = False
    last_leaf: tuple | None = None
    repeats: int = 0
    repeat_time: float = 0.0


class CallTracer:
    def __init__(self, output: Console, wire_trace: bool = False):
        self.console = output
        self.wire_trace = wire_trace
        self.active = False
        self._local = threading.local()
        self._thread_colors: dict[str, str] = {}
        self._wire_lock = threading.Lock()

    def start(self) -> None:
        self.active = True
        set_wire_trace(self._emit_wire if self.wire_trace else None)
        threading.settrace(self._global_trace)
        if hasattr(threading, 'settrace_all_threads'):  # Python 3.12+: also trace threads already running
            threading.settrace_all_threads(self._global_trace)
        else:
            sys.settrace(self._global_trace)

    def stop(self) -> None:
        self.active = False
        set_wire_trace(None)
        threading.settrace(None)
        if hasattr(threading, 'settrace_all_threads'):
            threading.settrace_all_threads(None)
        else:
            sys.settrace(None)

    # ------------------------------------------------------------------ trace hooks

    def _state(self) -> _ThreadState:
        state = getattr(self._local, 'state', None)
        if state is None:
            state = self._local.state = _ThreadState()
        return state

    def _global_trace(self, frame: FrameType, event: str, arg: object):
        if event != 'call' or not self.active:
            return None
        code = frame.f_code
        if not code.co_filename.startswith(PACKAGE_DIR) or code.co_filename == _THIS_FILE:
            return None
        if code.co_name in SKIPPED_NAMES:
            return None
        label = _label(frame)
        if label in QUIET or label.split('.', 1)[0] in QUIET_MODULES:
            return None
        state = self._state()
        if state.busy:
            return None
        if state.stack:
            parent = state.stack[-1]
            parent.exception = None  # the parent kept running, so any exception it saw was handled
            if not parent.header_printed:
                self._emit_header(state, parent)
        state.stack.append(_Call(frame, label, _call_arguments(frame), len(state.stack), time.perf_counter()))
        frame.f_trace_lines = False
        return self._local_trace

    def _local_trace(self, frame: FrameType, event: str, arg: object):
        if not self.active:
            return None
        state = self._state()
        if event == 'exception':
            if state.stack and state.stack[-1].frame is frame:
                state.stack[-1].exception = arg[1]
        elif event == 'return':
            while state.stack:
                call = state.stack.pop()
                if call.frame is frame:
                    break
            else:
                return None
            elapsed = time.perf_counter() - call.start
            code = frame.f_code
            returned = frame.f_lasti < len(code.co_code) and code.co_code[frame.f_lasti] in _RETURN_OPCODES
            error = None if returned else call.exception
            if call.header_printed:
                self._emit_footer(state, call, arg, error, elapsed)
            else:
                self._emit_leaf(state, call, arg, error, elapsed)
        return self._local_trace

    # ------------------------------------------------------------------ output

    def _emit_wire(self, direction: str, peer: str, size: int) -> None:
        arrow = '→' if direction == 'write' else '←'
        color = 'key' if direction == 'write' else 'success'
        line = Text.assemble(
            (datetime.now().strftime('%H:%M:%S.%f')[:-3] + ' ', 'muted'),
            (f'{threading.current_thread().name[:14]:<14} ', 'muted'),
            ('TCP ', 'bold'), (arrow + ' ', color), (peer, 'value'), (f'  {size} B', 'muted'),
        )
        with self._wire_lock:
            try:
                self.console.print(line, no_wrap=True, overflow='ellipsis', crop=True, soft_wrap=False)
            except Exception:
                pass

    def _prefix(self, depth: int) -> Text:
        thread = threading.current_thread().name
        color = self._thread_colors.setdefault(thread, ui.user_color(len(self._thread_colors) + 1))
        return Text.assemble(
            (datetime.now().strftime('%H:%M:%S.%f')[:-3] + ' ', 'muted'),
            (f'{thread[:14]:<14} ', color),
            ('│ ' * depth, 'grey37'),
        )

    @staticmethod
    def _name(call: _Call) -> Text:
        module, _, qualname = call.label.partition('.')
        color = ui.user_color(sum(map(ord, module)))
        return Text.assemble((f'{module}.', 'muted'), (qualname, f'bold {color}'))

    @staticmethod
    def _duration(seconds: float) -> Text:
        style = 'failure' if seconds > 1 else 'key' if seconds > 0.1 else 'muted'
        text = f'{seconds * 1000:.2f} ms' if seconds < 1 else f'{seconds:.2f} s'
        return Text(f'  {text}', style=style)

    @staticmethod
    def _outcome(result: object, error: BaseException | None) -> Text:
        if error is not None:
            return Text(f' ✗ {type(error).__name__}: {error}', style='failure')
        if result is None:
            return Text()
        return Text.assemble((' → ', 'muted'), (short_repr(result), 'success'))

    def _print(self, state: _ThreadState, line: Text) -> None:
        state.busy = True
        try:
            self.console.print(line, no_wrap=True, overflow='ellipsis', crop=True, soft_wrap=False)
        except Exception:
            pass
        finally:
            state.busy = False

    def _flush_repeats(self, state: _ThreadState) -> None:
        if state.repeats:
            depth = state.last_leaf[1]
            self._print(state, Text.assemble(self._prefix(depth), (f'  ↻ ×{state.repeats} more', 'key'),
                                             self._duration(state.repeat_time)))
        state.last_leaf, state.repeats, state.repeat_time = None, 0, 0.0

    def _emit_header(self, state: _ThreadState, call: _Call) -> None:
        self._flush_repeats(state)
        call.header_printed = True
        self._print(state, Text.assemble(self._prefix(call.depth), ('▶ ', 'bold'), self._name(call),
                                         (f'({call.args})', 'value')))

    def _emit_footer(self, state: _ThreadState, call: _Call, result, error, elapsed: float) -> None:
        self._flush_repeats(state)
        self._print(state, Text.assemble(self._prefix(call.depth), ('◀ ', 'bold'), self._name(call),
                                         self._outcome(result, error), self._duration(elapsed)))

    def _emit_leaf(self, state: _ThreadState, call: _Call, result, error, elapsed: float) -> None:
        key = (call.label, call.depth)
        if error is None and state.last_leaf == key:
            state.repeats += 1
            state.repeat_time += elapsed
            return
        self._flush_repeats(state)
        state.last_leaf = key if error is None else None
        bullet = ('✗ ', 'failure') if error is not None else ('● ', 'muted')
        self._print(state, Text.assemble(self._prefix(call.depth), bullet, self._name(call), (f'({call.args})', 'value'),
                                         self._outcome(result, error), self._duration(elapsed)))


_tracer: CallTracer | None = None
_wire_trace_enabled = False


def enable(destination: str | None = None, wire_trace: bool | None = None) -> str:
    """Start the live call trace, on the console or appended to a file (ANSI colored, read it with ``tail -f``)."""
    global _tracer, _wire_trace_enabled
    if wire_trace is not None:
        _wire_trace_enabled = wire_trace
    if _tracer is not None and _tracer.active:
        return describe()
    if destination and destination != '-':
        output = Console(file=open(destination, 'a', encoding='utf-8', buffering=1), force_terminal=True,
                         color_system='256', width=220, theme=ui.current_theme().rich_theme)
    else:
        output = console
    _tracer = CallTracer(output, wire_trace=_wire_trace_enabled)
    _tracer.start()
    return describe()


def disable() -> None:
    if _tracer is not None:
        _tracer.stop()


def is_enabled() -> bool:
    return _tracer is not None and _tracer.active


def describe() -> str:
    if not is_enabled():
        return 'disabled'
    name = getattr(_tracer.console.file, 'name', None)
    return f'written to {name}' if _tracer.console is not console and name else 'printed on the console'


# ---------------------------------------------------------------------- stack snapshots


def stack_tree(title: str = 'Thread stacks') -> Tree:
    """Current call stack of every thread, zlink frames highlighted and library frames folded."""
    frames = sys._current_frames()
    tree = Tree(Text(title, style='title'))
    for index, thread in enumerate(threading.enumerate()):
        frame = frames.get(thread.ident)
        if frame is None:
            continue
        flags = ' (daemon)' if thread.daemon else ''
        branch = tree.add(Text(f'{thread.name}{flags}', style=f'bold {ui.user_color(index + 1)}'))
        summary = [fs for fs in traceback.extract_stack(frame) if fs.filename != _THIS_FILE]
        folded = 0
        for position, fs in enumerate(summary):
            ours = fs.filename.startswith(PACKAGE_DIR)
            if not ours and position != len(summary) - 1:
                folded += 1
                continue
            if folded:
                branch.add(Text(f'… {folded} library frame{"s" * (folded > 1)}', style='muted'))
                folded = 0
            location = f'{Path(fs.filename).name}:{fs.lineno}'
            line = Text.assemble((fs.name, 'bold value' if ours else 'muted'), (f'  {location}', 'muted'))
            if fs.line:
                line.append(f'\n{fs.line.strip()}', style='key' if ours else 'grey42')
            branch.add(line)
    return tree


def print_stacks(title: str = 'Thread stacks') -> None:
    console.print(stack_tree(title))


def install_stack_signal() -> bool:
    """Print every thread stack when the process receives SIGUSR1 (POSIX only)."""
    if not hasattr(signal, 'SIGUSR1'):
        return False
    signal.signal(signal.SIGUSR1, lambda signum, frame: print_stacks('Thread stacks (SIGUSR1)'))
    return True
