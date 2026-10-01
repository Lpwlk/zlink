27/09/2026 - Rework done (see README roadmap for what's next):

[x] Update file structure in packaging-friendly format (pyproject.toml, `zlink` entry point)
[x] Switch Argparsers in appropriate locations (cli.py, `server` / `client` subcommands)
[x] Switch Helpers & Logging init in separate file(s) (ui.py)
[x] Switch request & response codes to a separate file (protocol.py, JSON framed messages)
[x] Switch Notification & sounds object in separate file (sounds.py)
[x] Define clear server & client side folder structure (~/.zlink)
[x] Analyze current auth methods & clean/update if needed (auth.py, PBKDF2)
[ ] Test sounds & rename them according their planned use (only 6 of the 22 sounds are used, see sounds.Sound)
[ ] TLS support
[ ] Resumable transfers / folder uploads
