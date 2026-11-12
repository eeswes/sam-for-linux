# SAM for Linux

Steam Achievement Manager for Linux — unlock and manage Steam game achievements without launching the game.

Inspired by [gibbed's SteamAchievementManager](https://github.com/gibbed/SteamAchievementManager), built natively for Linux.

## Why?

Steam no longer allows running the same game on multiple devices simultaneously. If you're using Steam on Linux and want to manage achievements without launching the game (e.g., to avoid conflicts with another device), this tool lets you do that directly through the Steam client's C API.

## Requirements

- **Python 3.10+**
- **Steam client** must be running and logged in
- **PyQt6** (for GUI mode)
- **libsteam_api.so** — not shipped with this project; the tool locates a copy
  already on your machine (see Installation)

## Installation

```bash
# Clone the repository
git clone https://github.com/eeswes/sam-for-linux
cd sam-for-linux

# PyQt6 is the only dependency (install via system package or pip)
# Arch Linux:
sudo pacman -S python-pyqt6

# Or via pip:
pip install PyQt6

# Recommended: keep your own copy of the Steam API library
python main.py install-lib
```

`libsteam_api.so` is not shipped with this project, and recent Steam clients no
longer keep one under their own directories — it normally lives inside each game's
install directory. `install-lib` copies the best one it can find
to `~/.local/lib/sam-linux/libsteam_api.so` so the tool keeps working no matter
which games you later install, update, or uninstall. Steam game updates have
removed a game's bundled copy before, which silently broke achievement lookups.

Library resolution order:

1. `SAM_LIBSTEAM_API` env var / pin from the GUI (File → Set libsteam_api.so…)
2. `~/.local/lib/sam-linux/libsteam_api.so` — created by `install-lib`
3. `vendor/libsteam_api.so` in this project
4. Steam install directories
5. `steamapps/common/` game directories (last resort)

Check what is in use and where it came from at any time:

```bash
python main.py find-lib
python main.py find-lib --all
```

### Do not commit libsteam_api.so

`install-lib` copies a file that already exists on **your** machine to another
location on **your** machine. That is a private copy and is not redistribution.

Committing `libsteam_api.so` into this repository would be a different matter: it
would hand every clone recipient a copy of Valve's proprietary binary, which the
Steamworks SDK agreement does not authorize an unrelated project to redistribute.
`.gitignore` blocks `vendor/` and `*.so` for this reason. Keep the project
source-only, and let each user run `install-lib` against their own local Steam.

## Legal and account risk

This project exists to modify achievement state for games the user does not
develop, which is outside the intended use of the Steamworks API. Understand before
using or publishing it:

- The Steam Subscriber Agreement and the Steamworks SDK agreement both constrain
  what the client API may be used for.
- Games with anti-cheat (VAC, EAC, BattlEye) may treat this as tampering. VAC
  bans affect the whole account, not just one game.
- Server-validated achievements will not stick, and repeated failures against a
  protected title is exactly the traffic pattern detection looks for.

If you publish this, keep it source-only, keep the disclaimer above prominent, and
do not bundle or link against any Valve binary.

## Usage

### GUI Mode

```bash
python main.py gui
```

This launches a graphical interface where you can:
- Browse installed games — tools, Proton and runtimes are hidden by default
  (File → Show Tools & Runtimes brings them back)
- View all achievements with their unlock status
- Click checkboxes to toggle achievements
- Use batch operations (Unlock All, Lock All, Invert)
- Filter and search achievements

### CLI Mode

```bash
# List installed games
python main.py list-games

# List games including tools, runtimes and other non-game content
python main.py list-games --all

# List achievements for a game
python main.py list <appid>

# Unlock a specific achievement
python main.py unlock <appid> <achievement_name>

# Unlock all achievements
python main.py unlock <appid> --all

# Lock a specific achievement
python main.py lock <appid> <achievement_name>

# Lock all achievements
python main.py lock <appid> --all

# Reset all stats and achievements
python main.py reset <appid>

# Find libsteam_api.so location
python main.py find-lib

# Enable verbose logging
python main.py -v list <appid>
```

### Examples

```bash
# List games
python main.py list-games

# View achievements for Counter-Strike 2 (AppID 730)
python main.py list 730

# Unlock all achievements for a game (with confirmation)
python main.py unlock 730 --all

# Unlock a specific achievement
python main.py unlock 730 PLAY_CS2

# Lock an achievement
python main.py lock 730 PLAY_CS2
```

## How It Works

1. The tool loads a `libsteam_api.so` copy — its own stable one if `install-lib`
   has been run, otherwise one found under the Steam installation
2. It sets the target game's AppID via environment variables
3. It initializes the Steam C API (flat API) using `ctypes`
4. It calls `ISteamUserStats` methods to read and modify achievements
5. Changes are synced to Steam servers via `StoreStats()`

The Steam client must be running for the API to work. The tool does NOT launch the game — it only communicates with the Steam client process.

## Important Notes

- **Use at your own risk.** Manipulating achievements may violate Steam's Terms of Service for some games.
- The Steam client must be running and you must be logged in.
- You must own the game on your Steam account.
- Some games may have server-side achievement validation that cannot be bypassed with this tool.
- Do not use this tool for games with anti-cheat systems that monitor achievement changes.
- Titles that define no achievements still appear in the game list — selecting one
  reports "No achievements". Filtering those out would need a Steam API round trip
  per installed game, which does not scale.

## Troubleshooting

### "libsteam_api.so not found"
Run `python main.py install-lib` while at least one Steam game that bundles the
library is installed. To point at a copy directly instead:
```bash
python main.py --lib /path/to/libsteam_api.so list <appid>
# or
export SAM_LIBSTEAM_API=/path/to/libsteam_api.so
```

### "Failed to initialize the Steam API"
The Steam client must be running and you must be logged in.

If it still fails, the automatically chosen copy may be unusable. List every
candidate and pick one explicitly:

```bash
python main.py find-lib --all
python main.py install-lib --from /path/to/libsteam_api.so
python main.py --lib /path/to/libsteam_api.so list <appid>
```

An explicit pin (env var, or GUI File → Set libsteam_api.so…, saved to
`~/.config/sam-linux/config.json`) overrides everything above.

Game copies of `libsteam_api.so` differ: some export `SteamAPI_Init`, newer ones only
`SteamAPI_InitSafe`, and runtime-embedded copies may reject `SteamAPI_InitFlat`. The
tool tries all three in that order, so a working copy is normally found automatically.
Note that a Steam game update can delete or replace its bundled copy — that is what
makes pinning useful.

### Achievements not syncing
After modifying achievements, the tool calls `StoreStats()` to sync with Steam servers. If you're offline, changes will be stored locally and synced when you go online.

## Project Structure

```
sam-for-linux/
├── main.py              # Entry point
├── LICENSE              # zlib license
├── requirements.txt     # Python dependencies
├── sam/
│   ├── __init__.py
│   ├── steam_api.py     # Steam C API wrapper (ctypes)
│   ├── steam_finder.py  # Find libsteam_api.so, Steam installs and games
│   ├── models.py        # Data models
│   ├── cli.py           # CLI interface
│   └── gui.py           # PyQt6 GUI
└── test/
    └── test_steam_api.py
```

## License

zlib — see [LICENSE](LICENSE), the same license the original SAM uses.

That covers this project's own source only. `libsteam_api.so` and `steamclient.so`
are proprietary to Valve Corporation, are not distributed here, and stay under
their own terms.

## Credits

- [gibbed/SteamAchievementManager](https://github.com/gibbed/SteamAchievementManager) — Original Windows SAM
- Valve Corporation — Steamworks SDK
