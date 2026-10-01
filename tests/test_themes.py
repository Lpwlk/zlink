import pytest

from zlink import cli, ui
from zlink.themes import STYLE_KEYS, THEMES


@pytest.fixture(autouse=True)
def restore_theme():
    yield
    ui.set_theme('ocean')


def test_complete_themes():
    assert list(THEMES) == ['ocean', 'dracula', 'nord', 'sunset', 'matrix', 'midnight', 'graphite', 'terminal']
    for theme in THEMES.values():
        assert set(STYLE_KEYS) <= set(theme.styles), theme.name
        assert len(theme.user_colors) >= 8 and len(theme.swatch) == 6


def test_switch_theme():
    ocean_accent = ui.console.get_style('accent')
    ui.set_theme('matrix')
    assert ui.current_theme().name == 'matrix'
    assert ui.console.get_style('accent') != ocean_accent
    assert ui.user_color(1) == THEMES['matrix'].user_colors[0]
    assert ui.user_color(9) == THEMES['matrix'].user_colors[0]  # cycles
    with pytest.raises(ValueError, match='unknown theme'):
        ui.set_theme('nope')


def test_help_describes_themes(capsys):
    plain = cli.themes_help(colored=False)
    for theme in THEMES.values():
        assert theme.name in plain and theme.description in plain
    assert '\x1b[' not in plain
    assert '\x1b[38;2;' in cli.themes_help(colored=True)  # truecolor swatches
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(['client', '--help'])
    assert 'green phosphor terminal' in capsys.readouterr().out


def test_theme_argument():
    parser = cli.build_parser()
    assert parser.parse_args(['client', '--theme', 'nord']).theme == 'nord'
    assert parser.parse_args(['server']).theme == 'ocean'
    with pytest.raises(SystemExit):
        parser.parse_args(['client', '--theme', 'nope'])


def test_preview_themes():
    with ui.console.capture() as capture:
        cli.preview_themes('sunset')
    output = capture.get()
    assert 'sunset' in output and 'User · loic' in output and 'back in 5' in output
    assert ui.current_theme().name == 'ocean'  # restored afterwards
