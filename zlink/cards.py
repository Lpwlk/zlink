"""Server and user cards, shared by the server console and the client shell."""

from __future__ import annotations

from rich.panel import Panel
from rich.text import Text

from . import ui
from .files import human_size
from .ui import card, color_chip, format_duration, meter, status_badge


def _temperature(temp: float | None) -> Text:
    if temp is None:
        return Text('n/a', style='muted')
    return Text(f'{temp:.1f} °C', style='success' if temp < 60 else 'warning' if temp < 75 else 'failure')


def server_card(info: dict) -> Panel:
    """Card of a server from its INFO response (missing fields, e.g. from older servers, are skipped)."""
    get = info.get
    clients, max_clients = get('clients', 0), get('max_clients', 0)
    load = get('load_avg')
    disk_total, disk_free = get('disk_total'), get('disk_free')
    auth = Text('required (accounts only)', style='warning') if get('auth_required') else Text(
        'optional (guests allowed)', style='success')

    sections = {
        'system': {
            'Hostname': get('hostname', '?'),
            'Platform': get('platform', '?'),
            'Python': get('python', '?'),
            'CPU temperature': _temperature(get('cpu_temp')),
            'Load average': ' · '.join(f'{x:.2f}' for x in load) if load else Text('n/a', style='muted'),
        },
        'service': {
            'Address': get('address') or Text('n/a', style='muted'),
            'Uptime': format_duration(get('uptime', 0)),
            'Clients': meter(clients, max_clients, label=f'{clients}/{max_clients}'),
            'Peak · total': f'{get("peak_clients", 0)} peak · {get("connections", 0)} sessions since boot',
            'Accounts': f'{get("registered_users", 0)} registered',
            'Authentication': auth,
            'Remote shutdown': 'allowed' if get('allow_shutdown') else 'disabled',
        },
        'storage': {
            'Files': f'{get("files", 0)} ({human_size(get("storage_used", 0))})',
            'Disk': meter(disk_total - disk_free, disk_total,
                          label=f'{human_size(disk_free)} free of {human_size(disk_total)}') if disk_total else 'n/a',
            'Max upload': human_size(get('max_upload', 0)),
        },
        'traffic': {
            'Messages relayed': get('messages', 0),
            'Chat history': f'{get("history", 0)} message(s) kept',
            'Received': human_size(get('bytes_in', 0)),
            'Sent': human_size(get('bytes_out', 0)),
        },
    }
    title = Text.assemble(('⚡ ', 'accent'), ('Server · ', 'title'), (get('hostname', '?'), 'title'))
    return card(title, sections, subtitle=Text(f'zlink {get("version", "?")}', style='muted'))


def user_card(user: dict, local: dict[str, object] | None = None) -> Panel:
    """Card of a connected user (WHO / WHOIS entry), ``local`` adds a section about this client."""
    get = user.get
    color = ui.user_color(get('index', 1))
    name = Text(get('username', '?'), style=f'bold {color}')
    idle = get('idle', 0)
    sections = {
        'identity': {
            'Username': name,
            'Account': Text('★ registered', style='key') if get('registered') else Text('☆ guest', style='muted'),
            'Status': status_badge(get('status', 'online'), get('status_message')),
            'Color': color_chip(color),
            'Client #': get('index', '?'),
        },
        'connection': {
            'Address': get('address', '?'),
            'Connected for': format_duration(get('connected_for', 0)),
            'Idle for': Text(format_duration(idle), style='warning' if idle > 300 else 'muted'),
        },
        'activity': {
            'Messages sent': get('messages', 0),
            'Uploads': f'{get("uploads", 0)} file(s) · {human_size(get("bytes_up", 0))}',
            'Downloads': f'{get("downloads", 0)} file(s) · {human_size(get("bytes_down", 0))}',
        },
    }
    if local:
        sections['this client'] = local
    title = Text.assemble(('◆ ', color), ('User · ', 'title'), name)
    return card(title, sections, border_style=color)
