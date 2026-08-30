# LFGBot

A **Looking-for-Group (LFG) bot** for Discord, built with
[discord.py](https://github.com/Rapptz/discord.py).

Servers, games, roles, forum channels, and league-website integration are defined in `config/` or
dynamically by Discord server admins, so supporting new games or servers requires no
code changes.

## Commands

| Command | Description |
| --- | --- |
| `/lfg [game] [description] [max_players]` | Post an LFG. Without `game:`, a guided game-selection view opens. |
| `/<game command>` (per server) | LFG shortcut for a configured game, with optional per-game parameters (e.g. `/rdl map:autumn,winter deck:standard`). |
| `/rename [title]` | Rename a bot-created game thread (host only). |
| `/random category:<category> [subset:<subset>] [display:<true\|false>]` | Random item from a configured set; subsets accept indices/ranges (e.g. `2,5-9`). |
| `/help [topic]` | Help for a command. |

### LFG posts

An LFG post is an embed with four buttons:

| Button | Effect |
| --- | --- |
| 👍 **Join/Leave** | Sign up or withdraw. |
| 🔔 **Toggle Notification** | Get notified when someone joins. |
| ❌ **Cancel** | Host only: cancel the LFG. |
| ✅ **Start** | Host only: start the game. |

- The game starts automatically when the last seat is taken (when `max_players` is defined).
- Starting a game creates a discussion thread for it
(in the game's forum channel, if configured) and pings the participants.
- If the game has league APIs configured, the bot also checks
that players are registered and registers the match.
- The bot is not persistent and therefore stores every information about a single LFG
into the corresponding message embed.

## Getting started

### Running a bot instance

#### Installation

Requires Python **3.14** installation and:
```bash
pip install -r requirements.txt
```

The bot must be invited with the `bot` and `applications.commands` scopes, the **Server
Members Intent** enabled, and permissions to post, create threads, and
mention roles.

#### Environment variables

| Variable | Required | Description |
| --- | --- | --- |
| `DISCORD_TOKEN` | Yes | The bot token. |
| `DATABASE_URL` | No | Bot database connection URL; see below. |
| `TEST_DATABASE_URL` | No | Test suite only. |
| *(per-game API tokens)* | No | Named by `GamesAPITokenEnvVars` in `config/games.ini`. |


#### Database configuration (optional)

With `DATABASE_URL` set (e.g. `postgres://user:password@host/dbname`), the
server configuration is read from the database instead of the `config/`
files. Without it (or when the database is unreachable), the bot uses the config files.

The storage layer uses Tortoise ORM and is database-agnostic
(but needs an async driver, e.g. `asyncpg` in `requirements.txt`).
The database itself must be created and migrated before hand:
```bash
python -m tortoise -c db.orm_config.TORTOISE_ORM migrate
```
If it is empty at startup (first run of the bot), its tables are seeded from the `config/` files.

#### Run

```bash
python bot.py
```

### Using a preconfigured bot

Alternatively, add the main bot instance to your server (contact the maintainer)
and configure it dynamically as a Discord server admin.

## Configuration

The defaults are defined in `config/`; when the bot uses its database, Discord
server admins manage their configuration dynamically.

List values are
comma-separated and positionally aligned (index *i* of every key describes
game *i*).

### `config/games.ini` — servers and games

Each section is a Discord server (`ID` = the server ID); `[DEFAULT]` applies
to every other server. Per game:

| Key | Description |
| --- | --- |
| `GamesCommands` / `GamesFullNames` | Short command name and display name of each game. |
| `GamesRoles` / `GamesIcons` / `GamesColors` | Role to ping, embed icon, embed colour. |
| `GamesForums` / `GamesTags` / `GamesVisibility` | Where game threads are created: forum channel, forum tag, `0` for private threads. |
| `GamesMaxPlayers` | Default maximum players (2–100, including the host); the game auto-starts when full. |
| `GamesMessages` | Extra message added to the game-start ping. |
| `GamesRegistrationAPI` / `GamesMatchAPI` / `GamesMatchURL` / `GamesAPITokenEnvVars` | League website endpoints, match URL, and env var holding the API token. |
| `GamesWebsiteURL` / `GamesRegistrationURL` / `GamesProfileURL` | Links shown when participants are not registered. |

Servers listed in `games.ini` get one slash command per game, on top of the global `/lfg` command.

### `config/games_parameters.ini` — game parameters

Optional parameters for each game's slash command; the section name must match
a `GamesCommands` entry in `games.ini`:

```ini
[rdl]
map = board_map: (autumn, Autumn), (winter, Winter), (lake, Lake)
houserule = my_houserule: yes,no
```

- `(value, Display Name)` pairs: the display name is shown to users, the raw
  value is what gets submitted; values without parentheses are used as-is.
- A parameter is submitted to the match API only when its value declares a
  field before the colon; `map = board_map: ...` submits the chosen values
  as `board_map`. Without a field (e.g. `houserule = yes,no`), the parameter
  is used in Discord only.
- Reserved `api_*` keys map the fixed match payload fields (title, thread
  link, participants).

### `config/rolls.ini` and `config/rolls_descriptions.json` — `/random`

`rolls.ini` maps categories to comma-separated item sets (`[DEFAULT]` applies
everywhere; server sections can override). `rolls_descriptions.json` optionally
provides a Discord embed per item.

## League website integration

For games with `GamesRegistrationAPI`/`GamesMatchAPI` configured, the bot
verifies participants' registration on the given website and registers the
match (title, thread link, participants, and any parameters mapped to API
fields). API tokens are read from the env var named in
`GamesAPITokenEnvVars`.

## License

[WTFPL](LICENSE) — do what the fuck you want to.


