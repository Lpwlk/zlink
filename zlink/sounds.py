"""Optional sound notifications (macOS only, silently disabled elsewhere).

The .aiff files are looked up in ``zlink/assets/sounds``; a missing file is simply skipped.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from enum import Enum
from pathlib import Path

SOUNDS_DIR = Path(__file__).parent / 'assets' / 'sounds'

enabled = sys.platform == 'darwin' and shutil.which('afplay') is not None


class Sound(str, Enum):
    CONNECTED = 'Blow.aiff'
    CLIENT_JOINED = 'Bottle.aiff'
    MESSAGE = 'Tink.aiff'
    PRIVATE_MESSAGE = 'Invitation.aiff'
    TRANSFER_DONE = 'Glass.aiff'
    ERROR = 'Frog.aiff'


def play(sound: Sound) -> None:
    path = SOUNDS_DIR / sound.value
    if enabled and path.exists():
        subprocess.Popen(['afplay', str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
