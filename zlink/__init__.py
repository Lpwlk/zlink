"""CLI based TCP client & server built on rich: chat, file transfers and server monitoring."""

__version__ = '0.3.0'

from .client import Client  # noqa: E402
from .server import Server  # noqa: E402

__all__ = ['Client', 'Server', '__version__']
