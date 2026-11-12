"""Find Steam installation, library folders, and installed games."""

import json
import os
import re
import glob
from sam.models import Game

CONFIG_PATH = os.path.expanduser("~/.config/sam-linux/config.json")


def _read_config() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_lib_override(lib_path: str | None):
    """Pin a specific libsteam_api.so so game reinstalls cannot break lookup."""
    config = _read_config()
    if lib_path is None:
        config.pop("libsteam_api", None)
    else:
        config["libsteam_api"] = lib_path
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
    except OSError:
        pass


def lib_override() -> str | None:
    """A library path explicitly chosen by the user, via env var or config."""
    env = os.environ.get("SAM_LIBSTEAM_API")
    if env and os.path.isfile(env):
        return env

    pinned = _read_config().get("libsteam_api")
    if pinned and os.path.isfile(pinned):
        return pinned
    return None


def find_steam_path() -> str | None:
    candidates = [
        os.path.expanduser("~/.local/share/Steam"),
        os.path.expanduser("~/.steam/steam"),
        os.path.expanduser("~/.steam/root"),
        "/usr/share/steam",
    ]
    for path in candidates:
        real = os.path.realpath(path)
        if os.path.isdir(real) and os.path.isdir(os.path.join(real, "steamapps")):
            return real
    return None


def find_libsteam_api() -> str | None:
    """Find libsteam_api.so, preferring copies with a usable init entry point.

    Search order puts game-independent locations first so that uninstalling or
    updating any single game cannot take the tool down with it.
    """
    override = lib_override()
    if override:
        return override

    candidates = collect_lib_candidates()

    if not candidates:
        return None

    for candidate in candidates:
        if has_usable_init(candidate):
            return candidate

    return candidates[0]


LIB_USER_PATH = os.path.expanduser("~/.local/lib/sam-linux/libsteam_api.so")
LIB_VENDOR_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "vendor",
    "libsteam_api.so",
)


def collect_lib_candidates() -> list[str]:
    """Every libsteam_api.so this tool can find, most stable location first."""
    candidates = []

    for stable in (LIB_USER_PATH, LIB_VENDOR_PATH):
        if os.path.isfile(stable):
            candidates.append(stable)

    steam_path = find_steam_path()
    if steam_path:
        for subdir in ["ubuntu12_64", "ubuntu12_32", "linux64", "linux32"]:
            candidate = os.path.join(steam_path, subdir, "libsteam_api.so")
            if os.path.isfile(candidate):
                candidates.append(candidate)

    library_folders = get_library_folders()
    for folder in library_folders:
        common_dir = os.path.join(folder, "steamapps", "common")
        if not os.path.isdir(common_dir):
            continue
        for root, dirs, files in os.walk(common_dir):
            depth = root.replace(common_dir, "").count(os.sep)
            if depth > 4:
                dirs.clear()
                continue
            if "libsteam_api.so" in files:
                candidates.append(os.path.join(root, "libsteam_api.so"))

    return candidates


def _is_elf_shared_object(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"\x7fELF"
    except OSError:
        return False


def lib_origin(path: str | None) -> str:
    """Describe where a library path came from, for status and diagnostics."""
    if not path:
        return "not found"
    if path == lib_override():
        return "pinned override"
    if path == LIB_USER_PATH:
        return "stable local copy (game-independent)"
    if path == LIB_VENDOR_PATH:
        return "vendored copy (game-independent)"
    if f"{os.sep}steamapps{os.sep}" in path:
        return "game install dir (breaks if that game is updated/removed)"
    return "Steam install"


def install_lib(source: str | None = None) -> str:
    """Copy a usable libsteam_api.so to a stable, game-independent location."""
    import shutil

    if source is None:
        source = find_libsteam_api()
        if source is None:
            raise FileNotFoundError(
                "No libsteam_api.so found to copy. Steam may not be installed."
            )
    if not os.path.isfile(source):
        raise FileNotFoundError(f"Not a file: {source}")
    if not _is_elf_shared_object(source):
        raise ValueError(f"{source} is not an ELF shared object")
    if not has_usable_init(source):
        raise ValueError(
            f"{source} exports neither SteamAPI_Init nor SteamAPI_InitSafe "
            "and would not be usable. Pick a different copy."
        )

    os.makedirs(os.path.dirname(LIB_USER_PATH), exist_ok=True)
    if os.path.isfile(LIB_USER_PATH):
        backup = LIB_USER_PATH + ".bak"
        shutil.copy2(LIB_USER_PATH, backup)
    shutil.copy2(source, LIB_USER_PATH)
    os.chmod(LIB_USER_PATH, 0o755)
    return LIB_USER_PATH


USABLE_INIT_SYMBOLS = {"SteamAPI_Init", "SteamAPI_InitSafe"}


def has_usable_init(lib_path: str) -> bool:
    """Check exported symbols without loading the library into this process."""
    symbols = _dynamic_symbols(lib_path)
    return symbols is None or bool(symbols & USABLE_INIT_SYMBOLS)


def _dynamic_symbols(lib_path: str) -> set[str] | None:
    for reader in (_symbols_via_nm, _symbols_via_readelf):
        symbols = reader(lib_path)
        if symbols is not None:
            return symbols
    return None


def _symbols_via_nm(lib_path: str) -> set[str] | None:
    import subprocess

    try:
        out = subprocess.run(
            ["nm", "-D", "--defined-only", lib_path],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return {line.split()[-1] for line in out.stdout.splitlines() if line.strip()}


def _symbols_via_readelf(lib_path: str) -> set[str] | None:
    import subprocess

    try:
        out = subprocess.run(
            ["readelf", "--dyn-syms", "--wide", lib_path],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    names = set()
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) < 8 or not parts[0].endswith(":"):
            continue
        if parts[3] == "FUNC" and parts[4] == "GLOBAL" and parts[5] == "DEFAULT":
            names.add(parts[7])
    return names


def get_library_folders() -> list[str]:
    steam_path = find_steam_path()
    if not steam_path:
        return []

    vdf_path = os.path.join(steam_path, "steamapps", "libraryfolders.vdf")
    if not os.path.isfile(vdf_path):
        return [steam_path]

    try:
        with open(vdf_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except OSError:
        return [steam_path]

    folders = []
    for match in re.finditer(r'"path"\s+"([^"]+)"', content):
        path = match.group(1)
        if os.path.isdir(path):
            folders.append(path)

    return folders if folders else [steam_path]


def _parse_acf_simple(filepath: str) -> dict[str, str]:
    result = {}
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except OSError:
        return result

    for match in re.finditer(r'"(\w+)"\s+"([^"]*)"', content):
        result[match.group(1)] = match.group(2)

    return result


def list_installed_games(include_tools: bool = False) -> list[Game]:
    games = []
    seen_appids = set()
    library_folders = get_library_folders()

    for folder in library_folders:
        apps_dir = os.path.join(folder, "steamapps")
        if not os.path.isdir(apps_dir):
            continue

        for manifest in glob.glob(os.path.join(apps_dir, "appmanifest_*.acf")):
            info = _parse_acf_simple(manifest)
            appid_str = info.get("appid")
            name = info.get("name", "").strip()

            if not appid_str or not name:
                continue

            appid = int(appid_str)
            if appid in seen_appids:
                continue
            seen_appids.add(appid)

            state_flags = info.get("StateFlags", "0")
            if int(state_flags) & 4 == 0:
                continue

            if not include_tools and _is_tool_or_non_game(name, appid):
                continue

            games.append(Game(appid=appid, name=name, install_path=folder))

    return sorted(games, key=lambda g: g.name.lower())


def _is_tool_or_non_game(name: str, appid: int) -> bool:
    """Check if an app is a tool, runtime, soundtrack, or other non-game content."""
    name_lower = name.lower()

    tool_patterns = [
        "proton",
        "steam linux runtime",
        "steam runtime",
        "steamworks common",
        "steam controller",
        "steam debug",
        "directx",
        "vcredist",
        "redistributable",
        "compatibility tool",
        "battleye",
        "easyanticheat",
        "dotnet",
        "xna framework",
        "physx",
        "openvr",
        "openxr",
        "vulkan",
        "dxc",
        "agility sdk",
    ]

    for pattern in tool_patterns:
        if pattern in name_lower:
            return True

    non_game_suffixes = [
        " ep",
        " soundtrack",
        " original soundtrack",
        " ost",
        " beta",
        " demo",
        " editor",
        " sdk",
        " server",
        " dedicated server",
        " tools",
    ]

    for suffix in non_game_suffixes:
        if name_lower.endswith(suffix):
            return True

    known_tool_appids = {
        228980, 1070560, 1391110, 1628350, 4183110,
        1161040, 1826330, 2230260, 1493710, 2180100,
        4628710, 3086180, 3127680,
    }
    if appid in known_tool_appids:
        return True

    return False
