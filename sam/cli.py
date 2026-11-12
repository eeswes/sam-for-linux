"""Command-line interface for SAM for Linux."""

import argparse
import sys
import logging
import time

from sam.steam_api import SteamAPI, SteamAPIError
from sam.steam_finder import (
    list_installed_games,
    find_libsteam_api,
    collect_lib_candidates,
    has_usable_init,
    lib_origin,
    install_lib,
)


def cmd_list_games(args):
    games = list_installed_games(include_tools=args.all)
    if not games:
        print("No installed games found.")
        return

    print(f"Found {len(games)} installed game(s):\n")
    for game in games:
        print(f"  {game.appid:>10}  {game.name}")


def cmd_list_achievements(args):
    appid = args.appid
    api = SteamAPI(args.lib)
    try:
        api.initialize(appid)
        achievements = api.get_all_achievements()

        if not achievements:
            print(f"No achievements found for app {appid}.")
            return

        unlocked = sum(1 for a in achievements if a["achieved"])
        total = len(achievements)

        print(f"\nAchievements for app {appid} ({unlocked}/{total} unlocked):\n")
        for ach in achievements:
            status = "✓" if ach["achieved"] else "✗"
            name = ach["display_name"] or ach["api_name"]
            desc = ach["description"]
            print(f"  [{status}] {ach['api_name']}")
            print(f"      {name}: {desc}")
            if ach["unlock_time"]:
                t = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ach["unlock_time"]))
                print(f"      Unlocked: {t}")
            print()
    finally:
        api.shutdown()


def cmd_unlock(args):
    appid = args.appid
    api = SteamAPI(args.lib)
    try:
        api.initialize(appid)

        if args.all:
            achievements = api.get_all_achievements()
            locked = [a for a in achievements if not a["achieved"]]
            if not locked:
                print("All achievements are already unlocked.")
                return
            if not args.yes:
                print(f"About to unlock {len(locked)} achievement(s) for app {appid}.")
                confirm = input("Continue? [y/N] ").strip().lower()
                if confirm != "y":
                    print("Aborted.")
                    return

            for ach in locked:
                ok = api.set_achievement(ach["api_name"])
                status = "OK" if ok else "FAIL"
                print(f"  [{status}] {ach['api_name']}")

            stored = api.store_stats()
            print(f"\nStore stats: {'OK' if stored else 'FAILED'}")
            print(f"Unlocked {len(locked)} achievement(s).")

        else:
            name = args.achievement
            if not name:
                print("Error: specify an achievement name or use --all")
                sys.exit(1)

            ok = api.set_achievement(name)
            if ok:
                stored = api.store_stats()
                print(f"Unlocked '{name}': {'OK' if stored else 'store failed'}")
            else:
                print(f"Failed to unlock '{name}'")
    finally:
        api.shutdown()


def cmd_lock(args):
    appid = args.appid
    api = SteamAPI(args.lib)
    try:
        api.initialize(appid)

        if args.all:
            achievements = api.get_all_achievements()
            unlocked = [a for a in achievements if a["achieved"]]
            if not unlocked:
                print("All achievements are already locked.")
                return
            if not args.yes:
                print(f"About to lock {len(unlocked)} achievement(s) for app {appid}.")
                confirm = input("Continue? [y/N] ").strip().lower()
                if confirm != "y":
                    print("Aborted.")
                    return

            for ach in unlocked:
                ok = api.clear_achievement(ach["api_name"])
                status = "OK" if ok else "FAIL"
                print(f"  [{status}] {ach['api_name']}")

            stored = api.store_stats()
            print(f"\nStore stats: {'OK' if stored else 'FAILED'}")
            print(f"Locked {len(unlocked)} achievement(s).")

        else:
            name = args.achievement
            if not name:
                print("Error: specify an achievement name or use --all")
                sys.exit(1)

            ok = api.clear_achievement(name)
            if ok:
                stored = api.store_stats()
                print(f"Locked '{name}': {'OK' if stored else 'store failed'}")
            else:
                print(f"Failed to lock '{name}'")
    finally:
        api.shutdown()


def cmd_reset(args):
    appid = args.appid
    if not args.yes:
        print(f"WARNING: This will reset ALL stats and achievements for app {appid}.")
        confirm = input("Are you sure? Type 'yes' to confirm: ").strip().lower()
        if confirm != "yes":
            print("Aborted.")
            return

    api = SteamAPI(args.lib)
    try:
        api.initialize(appid)
        ok = api.reset_all_stats(achievements_too=True)
        if ok:
            api.store_stats()
            print("All stats and achievements have been reset.")
        else:
            print("Failed to reset stats.")
    finally:
        api.shutdown()


def cmd_gui(args):
    from sam.gui import run_gui
    run_gui()


def cmd_find_lib(args):
    if args.all:
        candidates = collect_lib_candidates()
        if not candidates:
            print("No libsteam_api.so found.")
            print("Fix: python main.py install-lib")
            sys.exit(1)
        current = find_libsteam_api()
        print(f"{len(candidates)} candidate(s):\n")
        for path in candidates:
            usable = "usable init" if has_usable_init(path) else "no usable init"
            marker = "->" if path == current else "  "
            print(f"  {marker} {path}")
            print(f"       [{usable}] {lib_origin(path)}")
        return

    path = find_libsteam_api()
    if path:
        print(f"Using: {path}")
        print(f"       {lib_origin(path)}")
        if "steamapps" in path:
            print()
            print("WARNING: this copy lives inside a game install directory.")
            print("         Run 'python main.py install-lib' to keep an independent copy.")
        return

    print("libsteam_api.so not found.")
    print("Searched: stable local copy, vendor/, Steam install, and all game dirs.")
    print("Fix: install a Steam game that bundles libsteam_api.so, then run")
    print("     'python main.py install-lib', or pin one with")
    print("     export SAM_LIBSTEAM_API=/path/to/libsteam_api.so")
    sys.exit(1)


def cmd_install_lib(args):
    try:
        dest = install_lib(args.from_lib)
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"Installed: {dest}")
    print("The tool now uses this copy regardless of which games are installed.")
    if find_libsteam_api() != dest:
        print("NOTE: a pinned override takes priority. Clear ~/.config/sam-linux/config.json")
        print("      or unset SAM_LIBSTEAM_API if you want this copy to be used.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sam-linux",
        description="Steam Achievement Manager for Linux",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose logging"
    )
    parser.add_argument(
        "--lib", type=str, default=None, help="Path to libsteam_api.so"
    )

    sub = parser.add_subparsers(dest="command", help="Available commands")

    p_games = sub.add_parser("list-games", help="List installed Steam games")
    p_games.add_argument(
        "--all", action="store_true", help="Include tools, runtimes, and non-game content"
    )

    p_list = sub.add_parser("list", help="List achievements for a game")
    p_list.add_argument("appid", type=int, help="Steam App ID")

    p_unlock = sub.add_parser("unlock", help="Unlock achievement(s)")
    p_unlock.add_argument("appid", type=int, help="Steam App ID")
    p_unlock.add_argument("achievement", nargs="?", help="Achievement API name")
    p_unlock.add_argument("--all", action="store_true", help="Unlock all achievements")
    p_unlock.add_argument("-y", "--yes", action="store_true", help="Skip confirmation")

    p_lock = sub.add_parser("lock", help="Lock achievement(s)")
    p_lock.add_argument("appid", type=int, help="Steam App ID")
    p_lock.add_argument("achievement", nargs="?", help="Achievement API name")
    p_lock.add_argument("--all", action="store_true", help="Lock all achievements")
    p_lock.add_argument("-y", "--yes", action="store_true", help="Skip confirmation")

    p_reset = sub.add_parser("reset", help="Reset all stats and achievements")
    p_reset.add_argument("appid", type=int, help="Steam App ID")
    p_reset.add_argument("-y", "--yes", action="store_true", help="Skip confirmation")

    sub.add_parser("gui", help="Launch graphical interface")

    p_findlib = sub.add_parser("find-lib", help="Show which libsteam_api.so is used")
    p_findlib.add_argument(
        "--all", action="store_true", help="List every candidate library found"
    )

    p_install = sub.add_parser(
        "install-lib",
        help="Copy libsteam_api.so to a stable location, independent of any game",
    )
    p_install.add_argument(
        "--from",
        dest="from_lib",
        metavar="PATH",
        default=None,
        help="Specific library to copy (default: best currently detected)",
    )

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    log_level = logging.DEBUG if args.verbose else logging.WARNING
    logging.basicConfig(level=log_level, format="%(levelname)s: %(message)s")

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    commands = {
        "list-games": cmd_list_games,
        "list": cmd_list_achievements,
        "unlock": cmd_unlock,
        "lock": cmd_lock,
        "reset": cmd_reset,
        "gui": cmd_gui,
        "find-lib": cmd_find_lib,
        "install-lib": cmd_install_lib,
    }

    try:
        commands[args.command](args)
    except SteamAPIError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nAborted.")
        sys.exit(130)
