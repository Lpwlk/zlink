
<div align="center">


#  zlink




</div>


<div align="center">

![Open source](https://img.shields.io/badge/open-source-6894d4?logo=git&logoColor=6894d4)
![GitHub license](https://img.shields.io/github/license/Lpwlk/zlink?color=86c255 "Github repo license")[![GitHub profile](https://img.shields.io/static/v1?label=Lpwlk&message=profile&color=6894d4&logo=github)](https://github.com/Lpwlk "Go to GitHub profile page")
[![GitHub tags](https://img.shields.io/github/v/tag/Lpwlk/zlink?color=6894d4)](https://github.com/Lpwlk/zlink/tags "Go to GitHub repo tags")
[![GitHub last release](https://img.shields.io/github/release-date/Lpwlk/zlink?color=6894d4?label=Release)](https://github.com/Lpwlk/zlink "Go to GitHub repo")

[![PyPI - Python version](https://img.shields.io/pypi/pyversions/zlink?color=6894fA)](https://pypi.org/project/zlink "Supported Python version from PyPi package")
[![PyPI - Package version](https://img.shields.io/pypi/v/zlink?color=eda547)](https://pypi.org/project/zlink "Pypi package version")
[![PyPI - Package downloads](https://img.shields.io/pypi/dm/zlink?color=76b645)](https://pypi.org/project/zlink "Pypi package monthly downloads")

</div>

## Table of Contents

1 - [Description](#Description)  
2 - [Installation](#Installation)  
&nbsp;&nbsp;&nbsp;2.1 - [Instructions](#Instructions)  
&nbsp;&nbsp;&nbsp;2.2 - [Dependancies](#Dependancies)  
3 - [Usage](#Usage)  
&nbsp;&nbsp;&nbsp;3.1 - [Examples](#Examples)  
&nbsp;&nbsp;&nbsp;3.2 - [Usecases](#Usecases)  
&nbsp;&nbsp;&nbsp;3.3 - [Helps](#Helps)  
4 - [Roadmap](#Roadmap)  
5 - [Contributing](#Contributing)  
6 - [License](#License)  
7 - [References](#References)  



<div align="center">
	<img src="https://github.com/Lpwlk/Lpwlk/blob/main/assets/pulsing-bar.gif?raw=true">&nbsp;
</div>



## &nbsp;&nbsp; Description

zlink is a Python3 CLI package based on the socket and rich modules, rendering a networking terminal UI. A server accepts several TCP clients at once, and each client gets an interactive shell to:

- **chat** with every connected user, or send **private messages**
- **browse, upload and download files** (with folders) from a keyboard driven file browser, with live progress bars and SHA-256 integrity checks
- see **who is connected** and get **server information** (uptime, storage, CPU temperature...)
- measure **latency** with ping statistics

Accounts are optional: a server started with `--auth` requires users to login or register. Passwords are stored as salted PBKDF2-SHA256 hashes. Sound notifications are played on macOS.

> Without TLS, traffic (passwords included) goes over the network in clear text. Use it on networks you trust.

## &nbsp;&nbsp; Installation

### &nbsp;&nbsp;&nbsp;&nbsp; Instructions

To install the lastest package version, you can use pip to grab it from the PyPi package index platform or clone this repository to work and/or perform tests on the source code.

Downloading the package using pip ...

```
pip install zlink
```

Cloning the repository using git ...

```
git clone git@github.com:Lpwlk/zlink.git

cd zlink
```

### &nbsp;&nbsp;&nbsp;&nbsp; Dependancies

The only runtime dependency is [rich](https://github.com/Textualize/rich), and Python 3.10 or newer is required. Tests run with pytest:

```
pip install -e ".[dev]"
pytest
```

## &nbsp;&nbsp; Usage

### &nbsp;&nbsp;&nbsp;&nbsp; Examples

Start a server, then connect as many clients as you want from other terminals:

```
zlink server                       # local only, guests allowed
zlink server --host 0.0.0.0 --auth # reachable on the LAN, accounts required
zlink client --host 192.168.1.21 --user loic
```

`python3 -m zlink` works too, and asks for the mode when none is given. Once connected, the client shell accepts the following commands:

| Command | Arguments | Description |
| --- | --- | --- |
| `say`, `s` | `<message>` | Message everyone, `@name` mentions ring the recipient |
| `me` | `<action>` | Emote, e.g. `me waves` |
| `dm` | `<user> <message>` | Private message |
| `history` | `[-n COUNT]` | Recent public messages (the last 5 are shown when joining) |
| `who`, `w` | `[-s index\|name\|time\|idle\|status] [-r]` | Connected users with status, idle time and traffic |
| `whois` | `<user>` | User card: account, status, connection, activity |
| `status` | `[online\|away\|busy] [message]` | Show or change your presence |
| `card` | | Your own user card, with latency, theme and session details |
| `browse`, `b` | `[local\|server]` | Open the file browser |
| `ls` | `[folder] [-s name\|size\|time] [-r]` | List a server folder |
| `cd` / `mkdir` | `<folder>` | Change / create the current server folder |
| `upload`, `up` | `[path ...]` | Upload files to the current server folder (opens the browser without argument) |
| `download`, `dl` | `[name] [dest]` | Download a server file (opens the browser without argument) |
| `ping`, `p` | `[-n COUNT] [-i SEC] [-s SIZE] [-W SEC] [-q]` | Latency with loss, jitter, trend sparkline and payload throughput (`-n 0` until Ctrl+C) |
| `info`, `i` | | Server card: system, service, storage and traffic |
| `theme` | `[name]` | List the themes or switch to another one |
| `stack` / `trace` | `[on\|off]` | Thread call stacks / debug+ live call trace |
| `shutdown` | | Stop the server (needs `--allow-shutdown` on the server) |
| `help`, `h` | `[command]` | All commands, or the options of one |
| `quit`, `q` | | Disconnect |

The file browser shows your local files on one tab and the server files on the other (switch with `Tab`): arrows to move, `Enter` to open a folder or transfer a file, `←` to go back, `Space` to mark several files, `u` / `d` to upload / download them, `n` to create a server folder, `.` to show hidden local files and `q` to leave.

The prompt is a two-line block showing your status, the server, the current server folder, how many users are online and the time. Messages received while you type are printed above it without losing what you typed. `Tab` completes commands, usernames (`dm`, `whois`, `@mentions`), server paths (`cd`, `ls`, `download`) and local paths (`upload`); `↑` / `↓` browse the command history, kept between sessions; `Ctrl+C` clears the line and `Ctrl+D` quits.

Uploaded files never overwrite each other: a numbered suffix is added instead. Server data (accounts, logs, files) lives in `~/.zlink/server`, downloads go to `~/.zlink/downloads` and the shell history to `~/.zlink/shell_history` (set `zlink_HOME` to use another folder); see `--data-dir` and `--downloads` to change them.

The `Client` and `Server` classes can also be used from Python:

```python
from zlink import Client

client = Client('127.0.0.1', 8000)
client.connect()
client.join_as_guest('bot')
client.on_event = lambda event: print(event.fields)
client.upload('report.pdf')
client.chat('report.pdf is on the server!')
```

### &nbsp;&nbsp;&nbsp;&nbsp; Usecases

This project can be used on personal networks for multiple purposes. The extension options will be investigated during developement.

### &nbsp;&nbsp;&nbsp;&nbsp; Helps

Every CLI menu has its own help utility (`help` command in the client shell). Command line options are described by ...

```
zlink server -h
```

```
zlink client -h
```

Some options are fairly useful for server configuration (`--max-clients`, `--max-upload`, `--auth`) as well as for debugging purposes (`--verbose`, `--svg` to save the session output as an SVG file).

### &nbsp;&nbsp;&nbsp;&nbsp; Themes

Eight color themes are available on both sides with `--theme NAME` (or the `zlink_THEME` environment variable), and can be switched at any time from the client shell with `theme NAME`. `zlink client --help` lists them with a color swatch, `zlink themes` previews them with sample cards, chat and ping output.

| Theme | Description |
| --- | --- |
| `ocean` | calm blues and cyans with rounded borders, the original look (default) |
| `dracula` | the famous dark palette: purple, pink and neon green, heavy edges |
| `nord` | arctic, north-bluish tones: soft, low contrast and easy on the eyes |
| `sunset` | warm oranges, pinks and golds with double borders |
| `matrix` | green phosphor terminal with heavy borders |
| `midnight` | deep night blues and violets (Tokyo Night style), soft on dark terminals |
| `graphite` | neutral greys with colorblind-safe accents (Okabe-Ito palette), sober and readable |
| `terminal` | your terminal's own 16 ANSI colors: follows its palette, works everywhere |

### &nbsp;&nbsp;&nbsp;&nbsp; Debug+ mode

`-vv` (or `--debug-plus [FILE]`) turns on a live call tree of every zlink function, on both sides. Each line shows the thread, the nesting, the arguments, the returned value, the duration and the exceptions; passwords are masked and identical consecutive calls (file chunks...) are collapsed. Add `--wire-trace` to include Rich-rendered TCP socket reads and writes with peer addresses and byte counts; payload contents are never logged:

```
00:45:58.765 session-1      │ │ ● protocol.Connection.recv() → <download #3>  1.52 ms
00:45:58.765 session-1      │ │ ▶ server.Server._on_download(session=<Session loic>, msg=<download #3>)
00:45:58.765 session-1      │ │ │ ▶ server.Server._resolve(path='missing.txt')
00:45:58.766 session-1      │ │ │ ◀ server.Server._resolve → (PosixPath('~/.zlink/server/files/…  0.64 ms
00:45:58.766 session-1      │ │ ◀ server.Server._on_download ✗ RequestError: no file named "/missing.txt"  0.92 ms
```

For example: `zlink client --wire-trace` or `zlink server --debug-plus trace.log --wire-trace`; traces can be watched with `tail -f trace.log`. The client shell also offers `trace on|off` and `stack` (current call stack of every thread); a server in debug+ mode prints its thread stacks when it receives `kill -USR1 <pid>` (the PID is shown at startup). zlink currently uses TCP only: it has no UDP transport. This trace reports calls to the socket API, not TCP segment boundaries or Ethernet/IP packets; use `tcpdump` or Wireshark for packet capture.

### &nbsp;&nbsp;&nbsp;&nbsp; Protocol

Every message is one frame: a 4 bytes header length, a 4 bytes payload length, a JSON header and an optional binary payload (see [protocol.py](zlink/protocol.py)). Requests carry an `id` and responses a matching `reply_to`, so the server can push events (chat, joins, new files) at any time without mixing them up with responses. Files are sent in 64 KB chunks and checked against their SHA-256 before being saved.

## &nbsp;&nbsp; Roadmap


- [x] Clean the project structure for Python Packaging
- [x] Split the former utils.py file into dedicated modules
- [x] Re-define argparse options for client & server
- [x] Rework the authentication protocol (salted hashes, attempt limit)
- [x] File transfers, chat and private messages
- [ ] TLS encryption (`ssl` module) with self-signed certificates
- [ ] Resumable transfers and folder uploads
- [x] Keyboard driven local / server file browser
- [x] Five color themes, user statuses, chat history, user & server cards
- [ ] Full screen TUI with a live chat panel

## &nbsp;&nbsp; Contributing

Pull requests are welcome. For major changes, please fell free to open an issue first to discuss what you would like to change and in which way you would like to to it.

The project is currently published under MIT license which allows very flexible use for any kind of purposes and contributions.

## &nbsp;&nbsp; License

The project is currently published under MIT license which allows very flexible use for any kind of purposes and contributions.

## &nbsp;&nbsp; References



<div align="center">
	<img src="https://github.com/Lpwlk/Lpwlk/blob/main/assets/pulsing-bar.gif?raw=true">&nbsp;
</div>



<div align="center">


<br>

![Alt](https://repobeats.axiom.co/api/embed/3f6754e83e479c8763eaee93c734d86b90e3ae43.svg "Repobeats analytics image")


</div>



<div align="center">


<samp>

###### Mardown file generated using <a href ="https://github.com/Lpwlk/ReadmeEngine">readme-engine</a>

</samp>


</div>

