import argparse

import pytest

from zlink.files import display_path, human_size, parse_size, safe_filename, safe_relpath, unique_path


@pytest.mark.parametrize('name', ['', '.', '..', '../etc/passwd', 'a/b', 'a\\b', '.hidden', 'nul\0byte', 'x' * 256, None])
def test_unsafe_filenames(name):
    with pytest.raises(ValueError):
        safe_filename(name)


def test_safe_filename():
    assert safe_filename('report v2 (final).pdf') == 'report v2 (final).pdf'


def test_unique_path(tmp_path):
    assert unique_path(tmp_path, 'a.txt') == tmp_path / 'a.txt'
    (tmp_path / 'a.txt').touch()
    assert unique_path(tmp_path, 'a.txt') == tmp_path / 'a (1).txt'
    (tmp_path / 'a (1).txt').touch()
    assert unique_path(tmp_path, 'a.txt') == tmp_path / 'a (2).txt'


def test_parse_size():
    assert parse_size('1024') == 1024
    assert parse_size('500K') == 500 * 1024
    assert parse_size('2g') == 2 * 1024**3
    with pytest.raises(argparse.ArgumentTypeError):
        parse_size('lots')


def test_human_size():
    assert human_size(12) == '12 B'
    assert human_size(1536) == '1.5 KB'
    assert human_size(5 * 1024**3) == '5.0 GB'


@pytest.mark.parametrize('path, expected', [('', '/'), ('/', '/'), ('a/b.txt', '/a/b.txt'), ('/a//b/', '/a/b')])
def test_safe_relpath(path, expected):
    assert display_path(safe_relpath(path)) == expected


@pytest.mark.parametrize('path', ['..', 'a/../..', 'a/.git', 'a\\b', None])
def test_unsafe_relpath(path):
    with pytest.raises(ValueError):
        safe_relpath(path)
