"""Tests for SAM for Linux."""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sam.models import Game
from sam.steam_finder import (
    find_steam_path,
    find_libsteam_api,
    list_installed_games,
    collect_lib_candidates,
    has_usable_init,
)


class TestModels(unittest.TestCase):
    def test_game_creation(self):
        game = Game(appid=730, name="Counter-Strike 2")
        self.assertEqual(game.appid, 730)
        self.assertEqual(game.name, "Counter-Strike 2")
        self.assertEqual(str(game), "Counter-Strike 2 (730)")

    def test_game_without_install_path(self):
        game = Game(appid=480, name="Half-Life 2")
        self.assertEqual(game.install_path, "")


class TestSteamFinder(unittest.TestCase):
    def test_find_steam_path(self):
        path = find_steam_path()
        if path:
            self.assertTrue(os.path.isdir(path))
            self.assertTrue(os.path.isdir(os.path.join(path, "steamapps")))

    def test_find_libsteam_api(self):
        lib = find_libsteam_api()
        if lib:
            self.assertTrue(os.path.isfile(lib))
            self.assertTrue(lib.endswith(".so"))

    def test_list_installed_games(self):
        games = list_installed_games()
        self.assertIsInstance(games, list)
        if games:
            game = games[0]
            self.assertIsInstance(game, Game)
            self.assertGreater(game.appid, 0)
            self.assertTrue(game.name)


class TestLibrarySelection(unittest.TestCase):
    def test_selected_lib_has_usable_init(self):
        lib = find_libsteam_api()
        if lib is None:
            self.skipTest("No libsteam_api.so available on this machine")
        self.assertTrue(
            has_usable_init(lib),
            f"{lib} exports neither SteamAPI_Init nor SteamAPI_InitSafe",
        )

    def test_candidates_all_exist(self):
        for path in collect_lib_candidates():
            self.assertTrue(os.path.isfile(path))

    def test_env_override_wins(self):
        lib = find_libsteam_api()
        if lib is None:
            self.skipTest("No libsteam_api.so available on this machine")
        with patch.dict(os.environ, {"SAM_LIBSTEAM_API": lib}):
            self.assertEqual(find_libsteam_api(), lib)

    def test_filtering_hides_tools(self):
        filtered = list_installed_games()
        every = list_installed_games(include_tools=True)
        self.assertLessEqual(len(filtered), len(every))
        for game in filtered:
            self.assertNotIn("Proton", game.name)
            self.assertNotIn("Steam Linux Runtime", game.name)

    def test_stable_copy_preferred_over_game_dirs(self):
        """Lookup must not depend on any single game staying installed."""
        import sam.steam_finder as sf

        if not os.path.isfile(sf.LIB_USER_PATH):
            self.skipTest("No stable copy installed; run install-lib")

        real_steam = sf.find_steam_path
        real_folders = sf.get_library_folders
        try:
            sf.find_steam_path = lambda: None
            sf.get_library_folders = lambda: []
            self.assertEqual(sf.find_libsteam_api(), sf.LIB_USER_PATH)
        finally:
            sf.find_steam_path = real_steam
            sf.get_library_folders = real_folders

    def test_install_lib_rejects_unusable_library(self):
        from sam.steam_finder import install_lib

        with self.assertRaises(ValueError):
            install_lib(__file__)


class TestSteamAPI(unittest.TestCase):
    @patch("sam.steam_api.os.path.isfile", return_value=True)
    @patch("sam.steam_api.ctypes.CDLL")
    def test_api_instantiation(self, mock_cdll, mock_isfile):
        mock_lib = MagicMock()
        mock_cdll.return_value = mock_lib

        from sam.steam_api import SteamAPI

        api = SteamAPI("/fake/lib.so")
        self.assertIsNotNone(api)
        self.assertFalse(api.is_initialized)


if __name__ == "__main__":
    unittest.main(verbosity=2)
