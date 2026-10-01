"""Filesystem helpers used by file transfers on both sides."""

from __future__ import annotations

import argparse
import re
from pathlib import Path, PurePosixPath


def safe_filename(name: str) -> str:
    """Return ``name`` if it is a plain file name, raise ``ValueError`` otherwise.

    Rejects anything that could escape the storage directory (separators, ``..``) as well as
    hidden names, which are reserved for partial transfers.
    """
    if not isinstance(name, str) or not name or len(name) > 255:
        raise ValueError('invalid file name')
    if '/' in name or '\\' in name or '\0' in name or name.startswith('.'):
        raise ValueError(f'invalid file name: {name!r}')
    return name


def safe_relpath(path: str) -> PurePosixPath:
    """Parse a ``/``-separated path relative to a storage root, every part must be a safe file name.

    Leading and trailing slashes are ignored, so ``''`` and ``'/'`` both designate the root.
    """
    if not isinstance(path, str) or len(path) > 1024:
        raise ValueError('invalid path')
    parts = [part for part in path.split('/') if part]
    for part in parts:
        safe_filename(part)
    return PurePosixPath(*parts)


def display_path(path: PurePosixPath) -> str:
    return '/' + '/'.join(path.parts)


def unique_path(directory: Path, name: str) -> Path:
    """``directory / name``, suffixed with `` (1)``, `` (2)``... if that file already exists."""
    candidate = directory / name
    stem, suffix = candidate.stem, candidate.suffix
    n = 1
    while candidate.exists():
        candidate = directory / f'{stem} ({n}){suffix}'
        n += 1
    return candidate


def list_dir(directory: Path) -> list[dict]:
    """Visible entries of ``directory``, folders first then files, both sorted by name."""
    entries = []
    for path in directory.iterdir():
        if path.name.startswith('.'):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        is_dir = path.is_dir()
        entries.append({
            'name': path.name,
            'type': 'dir' if is_dir else 'file',
            'size': None if is_dir else stat.st_size,
            'mtime': stat.st_mtime,
        })
    entries.sort(key=lambda e: (e['type'] != 'dir', e['name'].lower()))
    return entries


def storage_stats(root: Path) -> tuple[int, int]:
    """(number of files, total size in bytes) under ``root``, hidden entries excluded."""
    count = total = 0
    for path in root.rglob('*'):
        if path.is_file() and not any(part.startswith('.') for part in path.relative_to(root).parts):
            count += 1
            total += path.stat().st_size
    return count, total


def human_size(n: float) -> str:
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return f'{n:.0f} {unit}' if unit == 'B' else f'{n:.1f} {unit}'
        n /= 1024
    return f'{n:.1f} TB'


def parse_size(value: str) -> int:
    """'1024', '500K', '20M', '2G' -> bytes (also usable as an argparse ``type``)."""
    match = re.fullmatch(r'(\d+)\s*([KMG]?)B?', str(value).strip(), re.IGNORECASE)
    if not match:
        raise argparse.ArgumentTypeError(f'invalid size: {value!r} (examples: 512, 64K, 20M, 2G)')
    return int(match[1]) * 1024 ** ' KMG'.index(match[2].upper() or ' ')
