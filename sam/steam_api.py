"""Steam C API wrapper using ctypes - uses the flat API exported by libsteam_api.so."""

import ctypes
import os
import logging

from sam.steam_finder import find_libsteam_api

logger = logging.getLogger(__name__)

USERSTATS_VERSIONS = [
    "SteamAPI_SteamUserStats_v013",
    "SteamAPI_SteamUserStats_v012",
    "SteamAPI_SteamUserStats_v011",
]


class SteamAPIError(Exception):
    pass


class SteamAPI:
    def __init__(self, lib_path: str | None = None):
        if lib_path is None:
            lib_path = find_libsteam_api()
        if lib_path is None:
            raise SteamAPIError(
                "libsteam_api.so not found. Make sure Steam is installed."
            )
        if not os.path.isfile(lib_path):
            raise SteamAPIError(f"libsteam_api.so not found at: {lib_path}")

        logger.info("Loading Steam API from: %s", lib_path)
        self._lib = ctypes.CDLL(lib_path)
        self._user_stats = None
        self._initialized = False
        self._appid = None
        self._funcs = {}
        self._init_attempts: list[str] = []

    def _get_func(self, name, restype, argtypes):
        try:
            func = getattr(self._lib, name)
            func.restype = restype
            func.argtypes = argtypes
            return func
        except AttributeError:
            logger.debug("Function not found: %s", name)
            return None

    def _run_init(self) -> bool:
        """Try each init entry point until one succeeds.

        Newer game-shipped copies of libsteam_api.so drop SteamAPI_Init and keep
        only SteamAPI_InitSafe; SteamAPI_InitFlat additionally refuses to run
        outside the Steam launch context for some titles, so it is tried last.
        """
        self._init_attempts = []

        for name in ("SteamAPI_Init", "SteamAPI_InitSafe"):
            fn = self._get_func(name, ctypes.c_bool, [])
            if fn is None:
                continue
            self._init_attempts.append(name)
            if fn():
                logger.info("Steam API initialized via %s", name)
                return True

        fn = self._get_func(
            "SteamAPI_InitFlat", ctypes.c_bool, [ctypes.c_char_p, ctypes.c_int32]
        )
        if fn is not None:
            self._init_attempts.append("SteamAPI_InitFlat")
            err_buf = ctypes.create_string_buffer(1024)
            if fn(err_buf, 1024):
                logger.info("Steam API initialized via SteamAPI_InitFlat")
                return True
            err_msg = err_buf.value.decode("utf-8", errors="replace").strip()
            if err_msg:
                logger.debug("SteamAPI_InitFlat error: %s", err_msg)

        return False

    def initialize(self, appid: int) -> bool:
        if self._initialized:
            self.shutdown()

        self._appid = appid
        os.environ["SteamAppId"] = str(appid)
        os.environ["SteamGameId"] = str(appid)

        if not self._run_init():
            os.environ.pop("SteamAppId", None)
            os.environ.pop("SteamGameId", None)
            raise SteamAPIError(
                "Failed to initialize the Steam API for app "
                f"{appid}. Tried: {', '.join(self._init_attempts)}. "
                "Make sure the Steam client is running and you are logged in."
            )

        for version_name in USERSTATS_VERSIONS:
            fn = self._get_func(version_name, ctypes.c_void_p, [])
            if fn is not None:
                ptr = fn()
                if ptr:
                    self._user_stats = ptr
                    logger.info(
                        "Got ISteamUserStats via %s (ptr=%#x)", version_name, ptr
                    )
                    break

        if not self._user_stats:
            self._raw_shutdown()
            raise SteamAPIError("Could not obtain ISteamUserStats interface")

        self._bind_functions()
        self._initialized = True
        self.request_current_stats()
        return True

    def _bind_functions(self):
        p = ctypes.c_void_p
        us = self._user_stats

        self._funcs["request_current_stats"] = self._get_func(
            "SteamAPI_ISteamUserStats_RequestCurrentStats", ctypes.c_bool, [p]
        )
        self._funcs["get_num_achievements"] = self._get_func(
            "SteamAPI_ISteamUserStats_GetNumAchievements", ctypes.c_uint32, [p]
        )
        self._funcs["get_achievement_name"] = self._get_func(
            "SteamAPI_ISteamUserStats_GetAchievementName",
            ctypes.c_char_p,
            [p, ctypes.c_uint32],
        )
        self._funcs["get_achievement"] = self._get_func(
            "SteamAPI_ISteamUserStats_GetAchievement",
            ctypes.c_bool,
            [p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_bool)],
        )
        self._funcs["get_achievement_and_unlock_time"] = self._get_func(
            "SteamAPI_ISteamUserStats_GetAchievementAndUnlockTime",
            ctypes.c_bool,
            [
                p,
                ctypes.c_char_p,
                ctypes.POINTER(ctypes.c_bool),
                ctypes.POINTER(ctypes.c_uint64),
            ],
        )
        self._funcs["set_achievement"] = self._get_func(
            "SteamAPI_ISteamUserStats_SetAchievement",
            ctypes.c_bool,
            [p, ctypes.c_char_p],
        )
        self._funcs["clear_achievement"] = self._get_func(
            "SteamAPI_ISteamUserStats_ClearAchievement",
            ctypes.c_bool,
            [p, ctypes.c_char_p],
        )
        self._funcs["store_stats"] = self._get_func(
            "SteamAPI_ISteamUserStats_StoreStats", ctypes.c_bool, [p]
        )
        self._funcs["reset_all_stats"] = self._get_func(
            "SteamAPI_ISteamUserStats_ResetAllStats",
            ctypes.c_bool,
            [p, ctypes.c_bool],
        )
        self._funcs["get_achievement_icon"] = self._get_func(
            "SteamAPI_ISteamUserStats_GetAchievementIcon",
            ctypes.c_int,
            [p, ctypes.c_char_p],
        )
        self._funcs["get_achievement_display_attribute"] = self._get_func(
            "SteamAPI_ISteamUserStats_GetAchievementDisplayAttribute",
            ctypes.c_char_p,
            [p, ctypes.c_char_p, ctypes.c_char_p],
        )
        self._funcs["indicate_achievement_progress"] = self._get_func(
            "SteamAPI_ISteamUserStats_IndicateAchievementProgress",
            ctypes.c_bool,
            [p, ctypes.c_char_p, ctypes.c_uint32, ctypes.c_uint32],
        )
        self._funcs["run_callbacks"] = self._get_func(
            "SteamAPI_RunCallbacks", None, []
        )
        self._funcs["shutdown"] = self._get_func("SteamAPI_Shutdown", None, [])
        self._funcs["is_steam_running"] = self._get_func(
            "SteamAPI_IsSteamRunning", ctypes.c_bool, []
        )

    def _ensure_init(self):
        if not self._initialized or not self._user_stats:
            raise SteamAPIError("Steam API not initialized. Call initialize() first.")

    def request_current_stats(self) -> bool:
        self._ensure_init()
        fn = self._funcs.get("request_current_stats")
        if fn:
            return fn(self._user_stats)
        return False

    def get_num_achievements(self) -> int:
        self._ensure_init()
        fn = self._funcs.get("get_num_achievements")
        if fn:
            return fn(self._user_stats)
        return 0

    def get_achievement_name(self, index: int) -> str | None:
        self._ensure_init()
        fn = self._funcs.get("get_achievement_name")
        if fn:
            result = fn(self._user_stats, index)
            return result.decode("utf-8") if result else None
        return None

    def get_achievement(self, name: str) -> bool | None:
        self._ensure_init()
        fn = self._funcs.get("get_achievement")
        if not fn:
            return None
        achieved = ctypes.c_bool(False)
        ok = fn(self._user_stats, name.encode("utf-8"), ctypes.byref(achieved))
        return achieved.value if ok else None

    def get_achievement_and_unlock_time(self, name: str) -> tuple[bool | None, int]:
        self._ensure_init()
        fn = self._funcs.get("get_achievement_and_unlock_time")
        if not fn:
            achieved = self.get_achievement(name)
            return (achieved, 0)
        achieved = ctypes.c_bool(False)
        unlock_time = ctypes.c_uint64(0)
        ok = fn(
            self._user_stats,
            name.encode("utf-8"),
            ctypes.byref(achieved),
            ctypes.byref(unlock_time),
        )
        return (achieved.value if ok else None, unlock_time.value if ok else 0)

    def get_achievement_display_name(self, api_name: str) -> str:
        self._ensure_init()
        fn = self._funcs.get("get_achievement_display_attribute")
        if fn:
            result = fn(
                self._user_stats,
                api_name.encode("utf-8"),
                b"name",
            )
            return result.decode("utf-8") if result else api_name
        return api_name

    def get_achievement_description(self, api_name: str) -> str:
        self._ensure_init()
        fn = self._funcs.get("get_achievement_display_attribute")
        if fn:
            result = fn(
                self._user_stats,
                api_name.encode("utf-8"),
                b"desc",
            )
            return result.decode("utf-8") if result else ""
        return ""

    def get_achievement_icon_handle(self, api_name: str) -> int:
        self._ensure_init()
        fn = self._funcs.get("get_achievement_icon")
        if fn:
            return fn(self._user_stats, api_name.encode("utf-8"))
        return 0

    def set_achievement(self, name: str) -> bool:
        self._ensure_init()
        fn = self._funcs.get("set_achievement")
        if fn:
            return fn(self._user_stats, name.encode("utf-8"))
        return False

    def clear_achievement(self, name: str) -> bool:
        self._ensure_init()
        fn = self._funcs.get("clear_achievement")
        if fn:
            return fn(self._user_stats, name.encode("utf-8"))
        return False

    def store_stats(self) -> bool:
        self._ensure_init()
        run_cb = self._funcs.get("run_callbacks")
        if run_cb:
            run_cb()
        fn = self._funcs.get("store_stats")
        if fn:
            return fn(self._user_stats)
        return False

    def reset_all_stats(self, achievements_too: bool = True) -> bool:
        self._ensure_init()
        fn = self._funcs.get("reset_all_stats")
        if fn:
            return fn(self._user_stats, ctypes.c_bool(achievements_too))
        return False

    def get_all_achievements(self) -> list[dict]:
        self._ensure_init()
        count = self.get_num_achievements()
        achievements = []
        for i in range(count):
            api_name = self.get_achievement_name(i)
            if not api_name:
                continue
            achieved, unlock_time = self.get_achievement_and_unlock_time(api_name)
            display_name = self.get_achievement_display_name(api_name)
            description = self.get_achievement_description(api_name)
            icon_handle = self.get_achievement_icon_handle(api_name)
            achievements.append(
                {
                    "api_name": api_name,
                    "display_name": display_name,
                    "description": description,
                    "achieved": achieved if achieved is not None else False,
                    "unlock_time": unlock_time,
                    "icon_handle": icon_handle,
                }
            )
        return achievements

    def shutdown(self):
        if self._initialized:
            self._raw_shutdown()
            self._initialized = False
            self._user_stats = None
            self._appid = None
            self._funcs.clear()

    def _raw_shutdown(self):
        try:
            fn = self._funcs.get("shutdown") or self._get_func(
                "SteamAPI_Shutdown", None, []
            )
            if fn:
                fn()
        except Exception:
            pass
        os.environ.pop("SteamAppId", None)
        os.environ.pop("SteamGameId", None)

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    @property
    def appid(self) -> int | None:
        return self._appid

    def __del__(self):
        try:
            self.shutdown()
        except Exception:
            pass
