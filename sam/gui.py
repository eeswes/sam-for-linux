"""PyQt6 graphical interface for SAM for Linux."""

import sys
import os
from functools import partial

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize
from PyQt6.QtGui import QAction, QColor, QBrush
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QSplitter,
    QListWidget,
    QListWidgetItem,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QToolBar,
    QStatusBar,
    QMessageBox,
    QProgressBar,
    QComboBox,
    QAbstractItemView,
    QMenu,
    QFileDialog,
)

from sam.steam_api import SteamAPI, SteamAPIError
from sam.steam_finder import (
    list_installed_games,
    find_libsteam_api,
    save_lib_override,
    lib_origin,
)
from sam.models import Game

COL_API_NAME = 0
COL_DISPLAY_NAME = 1
COL_DESCRIPTION = 2
COL_STATUS = 3
NUM_COLS = 4

ROLE_API_NAME = Qt.ItemDataRole.UserRole
ROLE_ACHIEVED = Qt.ItemDataRole.UserRole + 1
ROLE_GAME = Qt.ItemDataRole.UserRole + 2


class AchievementLoader(QThread):
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, appid, lib_path=None):
        super().__init__()
        self.appid = appid
        self.lib_path = lib_path

    def run(self):
        try:
            api = SteamAPI(self.lib_path)
            api.initialize(self.appid)
            achievements = api.get_all_achievements()
            api.shutdown()
            self.finished.emit(achievements)
        except Exception as e:
            self.error.emit(str(e))


class GameListLoader(QThread):
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, include_tools=False):
        super().__init__()
        self.include_tools = include_tools

    def run(self):
        try:
            games = list_installed_games(include_tools=self.include_tools)
            self.finished.emit(games)
        except Exception as e:
            self.error.emit(str(e))


class AchievementWorker(QThread):
    finished = pyqtSignal(bool, str)
    error = pyqtSignal(str)

    def __init__(self, appid, operations, lib_path=None):
        super().__init__()
        self.appid = appid
        self.operations = operations
        self.lib_path = lib_path

    def run(self):
        try:
            api = SteamAPI(self.lib_path)
            api.initialize(self.appid)
            for op_name, op_type in self.operations:
                if op_type == "set":
                    api.set_achievement(op_name)
                elif op_type == "clear":
                    api.clear_achievement(op_name)
            ok = api.store_stats()
            api.shutdown()
            self.finished.emit(ok, "OK" if ok else "Store failed")
        except Exception as e:
            self.error.emit(str(e))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SAM for Linux - Steam Achievement Manager")
        self.setMinimumSize(1000, 650)

        self._lib_path = find_libsteam_api()
        self._current_game = None
        self._current_appid = None
        self._loader_thread = None
        self._active_workers = []
        self._game_loader = None
        self._suppress_toggle = False
        self._include_tools = False

        self._build_ui()
        self._build_menu()
        self._build_toolbar()
        self._load_games()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(6, 6, 6, 6)

        top_bar = QHBoxLayout()

        top_bar.addWidget(QLabel("Search:"))
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Filter games...")
        self._search_input.textChanged.connect(self._filter_games)
        self._search_input.setClearButtonEnabled(True)
        top_bar.addWidget(self._search_input)

        top_bar.addWidget(QLabel("  AppID:"))
        self._appid_input = QLineEdit()
        self._appid_input.setPlaceholderText("Enter AppID")
        self._appid_input.setMaximumWidth(120)
        self._appid_input.returnPressed.connect(self._load_game_by_appid)
        top_bar.addWidget(self._appid_input)

        btn_open_appid = QPushButton("Open")
        btn_open_appid.clicked.connect(self._load_game_by_appid)
        top_bar.addWidget(btn_open_appid)

        layout.addLayout(top_bar)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel("Installed Games"))
        self._game_list = QListWidget()
        self._game_list.currentItemChanged.connect(self._on_game_selected)
        left_layout.addWidget(self._game_list)
        splitter.addWidget(left_panel)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)

        ach_header = QHBoxLayout()
        self._game_title_label = QLabel("Select a game")
        self._game_title_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        ach_header.addWidget(self._game_title_label)
        ach_header.addStretch()

        self._ach_search = QLineEdit()
        self._ach_search.setPlaceholderText("Filter achievements...")
        self._ach_search.setMaximumWidth(250)
        self._ach_search.textChanged.connect(self._filter_achievements)
        self._ach_search.setClearButtonEnabled(True)
        self._ach_search.setEnabled(False)
        ach_header.addWidget(self._ach_search)

        self._status_filter = QComboBox()
        self._status_filter.addItems(["All", "Unlocked", "Locked"])
        self._status_filter.setEnabled(False)
        self._status_filter.currentTextChanged.connect(self._filter_achievements)
        ach_header.addWidget(self._status_filter)

        right_layout.addLayout(ach_header)

        self._ach_table = QTableWidget()
        self._ach_table.setColumnCount(NUM_COLS)
        self._ach_table.setHorizontalHeaderLabels(
            ["API Name", "Name", "Description", "Status"]
        )
        self._ach_table.horizontalHeader().setSectionResizeMode(
            COL_API_NAME, QHeaderView.ResizeMode.ResizeToContents
        )
        self._ach_table.horizontalHeader().setSectionResizeMode(
            COL_DISPLAY_NAME, QHeaderView.ResizeMode.Interactive
        )
        self._ach_table.horizontalHeader().setSectionResizeMode(
            COL_DESCRIPTION, QHeaderView.ResizeMode.Stretch
        )
        self._ach_table.horizontalHeader().setSectionResizeMode(
            COL_STATUS, QHeaderView.ResizeMode.ResizeToContents
        )
        self._ach_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._ach_table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self._ach_table.cellChanged.connect(self._on_cell_changed)
        self._ach_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._ach_table.customContextMenuRequested.connect(self._show_context_menu)
        right_layout.addWidget(self._ach_table)

        self._progress = QProgressBar()
        self._progress.setVisible(False)
        self._progress.setRange(0, 0)
        right_layout.addWidget(self._progress)

        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)

        layout.addWidget(splitter)

        self._statusbar = QStatusBar()
        self.setStatusBar(self._statusbar)
        if self._lib_path:
            origin = lib_origin(self._lib_path)
            self._statusbar.showMessage(f"Steam API: {self._lib_path}  [{origin}]")
        else:
            self._statusbar.showMessage(
                "WARNING: libsteam_api.so not found - run install-lib"
            )

    def _build_menu(self):
        menubar = self.menuBar()

        file_menu = menubar.addMenu("&File")

        refresh_action = QAction("&Refresh Games", self)
        refresh_action.setShortcut("F5")
        refresh_action.triggered.connect(self._load_games)
        file_menu.addAction(refresh_action)

        self._show_tools_action = QAction("Show &Tools && Runtimes", self)
        self._show_tools_action.setCheckable(True)
        self._show_tools_action.setChecked(False)
        self._show_tools_action.toggled.connect(self._toggle_show_tools)
        file_menu.addAction(self._show_tools_action)

        file_menu.addSeparator()

        lib_action = QAction("Set &libsteam_api.so...", self)
        lib_action.triggered.connect(self._choose_library)
        file_menu.addAction(lib_action)

        file_menu.addSeparator()

        quit_action = QAction("&Quit", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        help_menu = menubar.addMenu("&Help")
        about_action = QAction("&About", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    def _toggle_show_tools(self, checked):
        self._include_tools = checked
        self._load_games()

    def _choose_library(self):
        start_dir = os.path.dirname(self._lib_path) if self._lib_path else ""

        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select libsteam_api.so",
            start_dir,
            "Steam API library (libsteam_api.so);;All files (*)",
        )
        if not path:
            return

        save_lib_override(path)
        self._lib_path = path
        self._statusbar.showMessage(
            f"Steam API: {path}  [{lib_origin(path)}]", 8000
        )

        if self._current_game:
            self._load_achievements(self._current_game)

    def _build_toolbar(self):
        toolbar = QToolBar("Actions")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(20, 20))
        self.addToolBar(toolbar)

        self._btn_unlock_selected = QPushButton("Unlock Selected")
        self._btn_unlock_selected.clicked.connect(self._unlock_selected)
        self._btn_unlock_selected.setEnabled(False)
        toolbar.addWidget(self._btn_unlock_selected)

        self._btn_lock_selected = QPushButton("Lock Selected")
        self._btn_lock_selected.clicked.connect(self._lock_selected)
        self._btn_lock_selected.setEnabled(False)
        toolbar.addWidget(self._btn_lock_selected)

        toolbar.addSeparator()

        self._btn_unlock_all = QPushButton("Unlock All")
        self._btn_unlock_all.clicked.connect(self._unlock_all)
        self._btn_unlock_all.setEnabled(False)
        toolbar.addWidget(self._btn_unlock_all)

        self._btn_lock_all = QPushButton("Lock All")
        self._btn_lock_all.clicked.connect(self._lock_all)
        self._btn_lock_all.setEnabled(False)
        toolbar.addWidget(self._btn_lock_all)

        toolbar.addSeparator()

        self._btn_invert = QPushButton("Invert")
        self._btn_invert.clicked.connect(self._invert_selection)
        self._btn_invert.setEnabled(False)
        toolbar.addWidget(self._btn_invert)

        toolbar.addSeparator()

        self._btn_reset = QPushButton("Reset All Stats")
        self._btn_reset.clicked.connect(self._reset_all)
        self._btn_reset.setEnabled(False)
        toolbar.addWidget(self._btn_reset)

    def _set_actions_enabled(self, enabled: bool):
        for btn in [
            self._btn_unlock_selected,
            self._btn_lock_selected,
            self._btn_unlock_all,
            self._btn_lock_all,
            self._btn_invert,
            self._btn_reset,
            self._ach_search,
            self._status_filter,
        ]:
            btn.setEnabled(enabled)

    def _load_games(self):
        self._game_list.clear()
        self._game_list.addItem("Loading games...")

        self._game_loader = GameListLoader(include_tools=self._include_tools)
        self._game_loader.finished.connect(self._on_games_loaded)
        self._game_loader.error.connect(self._on_games_error)
        self._game_loader.start()

    def _on_games_loaded(self, games):
        self._game_list.clear()
        for game in games:
            item = QListWidgetItem(str(game))
            item.setData(ROLE_GAME, game)
            self._game_list.addItem(item)
        self._statusbar.showMessage(f"Loaded {len(games)} game(s)", 5000)

    def _on_games_error(self, error):
        self._game_list.clear()
        self._game_list.addItem(f"Error: {error}")
        self._statusbar.showMessage(f"Error loading games: {error}")

    def _filter_games(self, text):
        text_lower = text.lower()
        for i in range(self._game_list.count()):
            item = self._game_list.item(i)
            if item.data(ROLE_GAME) is None:
                continue
            visible = text_lower in item.text().lower()
            item.setHidden(not visible)

    def _on_game_selected(self, current, previous):
        if current is None:
            return
        game = current.data(ROLE_GAME)
        if game is None:
            return
        self._load_achievements(game)

    def _load_game_by_appid(self):
        text = self._appid_input.text().strip()
        if not text:
            return
        try:
            appid = int(text)
        except ValueError:
            QMessageBox.warning(self, "Invalid Input", "Please enter a numeric AppID.")
            return
        game = Game(appid=appid, name=f"App {appid}")
        self._load_achievements(game)

    def _load_achievements(self, game: Game):
        if self._loader_thread and self._loader_thread.isRunning():
            self._loader_thread.quit()
            self._loader_thread.wait()

        self._current_game = game
        self._current_appid = game.appid
        self._game_title_label.setText(f"Loading: {game.name} ({game.appid})...")
        self._ach_table.setRowCount(0)
        self._set_actions_enabled(False)
        self._progress.setVisible(True)

        self._loader_thread = AchievementLoader(game.appid, self._lib_path)
        self._loader_thread.finished.connect(self._on_achievements_loaded)
        self._loader_thread.error.connect(self._on_achievements_error)
        self._loader_thread.start()

    def _on_achievements_loaded(self, achievements):
        self._progress.setVisible(False)

        if not achievements:
            self._game_title_label.setText(
                f"{self._current_game.name} ({self._current_appid}) - No achievements"
            )
            self._statusbar.showMessage("No achievements found for this game", 5000)
            return

        unlocked = sum(1 for a in achievements if a["achieved"])
        total = len(achievements)
        self._game_title_label.setText(
            f"{self._current_game.name} ({self._current_appid}) "
            f"- {unlocked}/{total} unlocked"
        )

        self._suppress_toggle = True
        self._ach_table.setRowCount(len(achievements))

        for row, ach in enumerate(achievements):
            api_name_item = QTableWidgetItem(ach["api_name"])
            api_name_item.setFlags(
                api_name_item.flags() & ~Qt.ItemFlag.ItemIsEditable
            )
            api_name_item.setData(ROLE_API_NAME, ach["api_name"])
            api_name_item.setData(ROLE_ACHIEVED, ach["achieved"])
            self._ach_table.setItem(row, COL_API_NAME, api_name_item)

            display_item = QTableWidgetItem(ach.get("display_name", ""))
            display_item.setFlags(display_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._ach_table.setItem(row, COL_DISPLAY_NAME, display_item)

            desc_item = QTableWidgetItem(ach.get("description", ""))
            desc_item.setFlags(desc_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._ach_table.setItem(row, COL_DESCRIPTION, desc_item)

            status_item = QTableWidgetItem()
            status_item.setFlags(
                status_item.flags() | Qt.ItemFlag.ItemIsUserCheckable
            )
            status_item.setCheckState(
                Qt.CheckState.Checked if ach["achieved"] else Qt.CheckState.Unchecked
            )
            self._ach_table.setItem(row, COL_STATUS, status_item)

            self._apply_row_color(row, ach["achieved"])

        self._suppress_toggle = False
        self._set_actions_enabled(True)
        self._statusbar.showMessage(
            f"Loaded {total} achievement(s) for {self._current_game.name}", 5000
        )

    def _on_achievements_error(self, error):
        self._progress.setVisible(False)
        self._game_title_label.setText("Error loading achievements")
        self._set_actions_enabled(False)
        QMessageBox.critical(
            self,
            "Error",
            f"Failed to load achievements:\n{error}",
        )

    def _apply_row_color(self, row: int, achieved: bool):
        color = QColor(50, 180, 50, 30) if achieved else QColor(0, 0, 0, 0)
        brush = QBrush(color)
        for col in range(NUM_COLS):
            item = self._ach_table.item(row, col)
            if item:
                item.setBackground(brush)

    def _on_cell_changed(self, row, col):
        if self._suppress_toggle:
            return
        if col != COL_STATUS:
            return

        item = self._ach_table.item(row, COL_STATUS)
        if item is None:
            return

        api_name_item = self._ach_table.item(row, COL_API_NAME)
        if api_name_item is None:
            return

        api_name = api_name_item.data(ROLE_API_NAME)
        new_state = item.checkState()
        is_now_achieved = new_state == Qt.CheckState.Checked

        self._apply_single_achievement(api_name, is_now_achieved, row)

    def _apply_single_achievement(self, api_name: str, achieve: bool, row: int):
        if not self._current_appid:
            return

        op = "set" if achieve else "clear"
        worker = AchievementWorker(
            self._current_appid, [(api_name, op)], self._lib_path
        )
        worker.finished.connect(partial(self._on_single_op_done, row, achieve))
        worker.error.connect(partial(self._on_single_op_error, row, achieve))
        worker.finished.connect(lambda *a: self._cleanup_worker(worker))
        worker.error.connect(lambda *a: self._cleanup_worker(worker))
        self._active_workers.append(worker)
        worker.start()

    def _cleanup_worker(self, worker):
        if worker in self._active_workers:
            self._active_workers.remove(worker)
        worker.deleteLater()

    def _on_single_op_done(self, row, desired_achieve, ok, msg):
        if ok:
            self._apply_row_color(row, desired_achieve)
            api_item = self._ach_table.item(row, COL_API_NAME)
            if api_item:
                api_item.setData(ROLE_ACHIEVED, desired_achieve)
            self._update_title_count()
            self._statusbar.showMessage("Achievement updated", 3000)
        else:
            self._revert_row(row, not desired_achieve)
            self._statusbar.showMessage("Failed to update achievement", 5000)

    def _on_single_op_error(self, row, desired_achieve, error):
        self._revert_row(row, not desired_achieve)
        QMessageBox.warning(self, "Error", f"Failed to update achievement:\n{error}")

    def _revert_row(self, row: int, achieved: bool):
        self._suppress_toggle = True
        item = self._ach_table.item(row, COL_STATUS)
        if item:
            item.setCheckState(
                Qt.CheckState.Checked if achieved else Qt.CheckState.Unchecked
            )
        self._apply_row_color(row, achieved)
        api_item = self._ach_table.item(row, COL_API_NAME)
        if api_item:
            api_item.setData(ROLE_ACHIEVED, achieved)
        self._suppress_toggle = False

    def _update_title_count(self):
        if not self._current_game:
            return
        unlocked = 0
        total = self._ach_table.rowCount()
        for row in range(total):
            item = self._ach_table.item(row, COL_API_NAME)
            if item and item.data(ROLE_ACHIEVED):
                unlocked += 1
        self._game_title_label.setText(
            f"{self._current_game.name} ({self._current_appid}) "
            f"- {unlocked}/{total} unlocked"
        )

    def _get_selected_rows(self) -> list[int]:
        rows = set()
        for idx in self._ach_table.selectedIndexes():
            rows.add(idx.row())
        return sorted(rows)

    def _collect_operations(self, rows: list[int], achieve: bool) -> list[tuple]:
        ops = []
        for row in rows:
            api_item = self._ach_table.item(row, COL_API_NAME)
            if api_item is None:
                continue
            api_name = api_item.data(ROLE_API_NAME)
            currently_achieved = api_item.data(ROLE_ACHIEVED)
            if currently_achieved == achieve:
                continue
            op = "set" if achieve else "clear"
            ops.append((api_name, op))
        return ops

    def _batch_operation(self, achieve: bool, label: str):
        rows = list(range(self._ach_table.rowCount()))
        ops = self._collect_operations(rows, achieve)
        if not ops:
            self._statusbar.showMessage(f"Nothing to {label.lower()}", 3000)
            return

        reply = QMessageBox.question(
            self,
            "Confirm",
            f"{label} {len(ops)} achievement(s)?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._run_batch(ops)

    def _selection_operation(self, achieve: bool, label: str):
        rows = self._get_selected_rows()
        if not rows:
            QMessageBox.information(self, "Info", "No rows selected.")
            return

        ops = self._collect_operations(rows, achieve)
        if not ops:
            self._statusbar.showMessage(
                f"Selected achievements already {label.lower()}ed", 3000
            )
            return

        self._run_batch(ops)

    def _run_batch(self, ops: list[tuple]):
        if not self._current_appid:
            return

        self._set_actions_enabled(False)
        self._progress.setVisible(True)

        worker = AchievementWorker(self._current_appid, ops, self._lib_path)
        worker.finished.connect(partial(self._on_batch_done, ops))
        worker.error.connect(self._on_batch_error)
        worker.finished.connect(lambda *a: self._cleanup_worker(worker))
        worker.error.connect(lambda *a: self._cleanup_worker(worker))
        self._active_workers.append(worker)
        worker.start()

    def _on_batch_done(self, ops, ok, msg):
        self._progress.setVisible(False)
        self._set_actions_enabled(True)

        if ok:
            desired = {name: op_type == "set" for name, op_type in ops}
            for row in range(self._ach_table.rowCount()):
                api_item = self._ach_table.item(row, COL_API_NAME)
                if api_item is None:
                    continue
                api_name = api_item.data(ROLE_API_NAME)
                if api_name in desired:
                    self._revert_row(row, desired[api_name])
            self._update_title_count()
            self._statusbar.showMessage(
                f"Updated {len(ops)} achievement(s)", 5000
            )
        else:
            QMessageBox.warning(
                self, "Error", f"Batch operation failed: {msg}"
            )

    def _on_batch_error(self, error):
        self._progress.setVisible(False)
        self._set_actions_enabled(True)
        QMessageBox.critical(self, "Error", f"Batch operation failed:\n{error}")

    def _unlock_selected(self):
        self._selection_operation(True, "Unlock")

    def _lock_selected(self):
        self._selection_operation(False, "Lock")

    def _unlock_all(self):
        self._batch_operation(True, "Unlock")

    def _lock_all(self):
        self._batch_operation(False, "Lock")

    def _invert_selection(self):
        rows = self._get_selected_rows() or list(range(self._ach_table.rowCount()))
        ops = []
        for row in rows:
            api_item = self._ach_table.item(row, COL_API_NAME)
            if api_item is None:
                continue
            api_name = api_item.data(ROLE_API_NAME)
            currently_achieved = api_item.data(ROLE_ACHIEVED)
            ops.append((api_name, "clear" if currently_achieved else "set"))

        if not ops:
            self._statusbar.showMessage("Nothing to invert", 3000)
            return

        self._run_batch(ops)

    def _reset_all(self):
        if not self._current_appid:
            return

        reply = QMessageBox.warning(
            self,
            "Confirm Reset",
            f"This will reset ALL stats and achievements for "
            f"{self._current_game.name} ({self._current_appid}).\n\n"
            f"This action cannot be undone. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            api = SteamAPI(self._lib_path)
            api.initialize(self._current_appid)
            ok = api.reset_all_stats(achievements_too=True)
            if ok:
                api.store_stats()
            api.shutdown()

            if ok:
                self._load_achievements(self._current_game)
                self._statusbar.showMessage("All stats reset", 5000)
            else:
                QMessageBox.warning(self, "Error", "Failed to reset stats.")
        except SteamAPIError as e:
            QMessageBox.critical(self, "Error", str(e))

    def _filter_achievements(self, _=None):
        text = self._ach_search.text().lower()
        status = self._status_filter.currentText()

        for row in range(self._ach_table.rowCount()):
            api_item = self._ach_table.item(row, COL_API_NAME)
            display_item = self._ach_table.item(row, COL_DISPLAY_NAME)
            desc_item = self._ach_table.item(row, COL_DESCRIPTION)

            if api_item is None:
                continue

            match_text = True
            if text:
                searchable = " ".join(
                    [
                        api_item.text() or "",
                        display_item.text() if display_item else "",
                        desc_item.text() if desc_item else "",
                    ]
                ).lower()
                match_text = text in searchable

            match_status = True
            if status == "Unlocked":
                match_status = api_item.data(ROLE_ACHIEVED) is True
            elif status == "Locked":
                match_status = not api_item.data(ROLE_ACHIEVED)

            self._ach_table.setRowHidden(row, not (match_text and match_status))

    def _show_context_menu(self, pos):
        row = self._ach_table.rowAt(pos.y())
        if row < 0:
            return

        api_item = self._ach_table.item(row, COL_API_NAME)
        if api_item is None:
            return

        achieved = api_item.data(ROLE_ACHIEVED)
        api_name = api_item.data(ROLE_API_NAME)

        menu = QMenu(self)

        if achieved:
            lock_action = menu.addAction("Lock this achievement")
            lock_action.triggered.connect(
                partial(self._apply_single_achievement, api_name, False, row)
            )
        else:
            unlock_action = menu.addAction("Unlock this achievement")
            unlock_action.triggered.connect(
                partial(self._apply_single_achievement, api_name, True, row)
            )

        menu.addSeparator()

        copy_name = menu.addAction("Copy API name")
        copy_name.triggered.connect(
            lambda: QApplication.clipboard().setText(api_name)
        )

        menu.exec(self._ach_table.viewport().mapToGlobal(pos))

    def _show_about(self):
        QMessageBox.about(
            self,
            "About SAM for Linux",
            "<h3>SAM for Linux</h3>"
            "<p>Steam Achievement Manager for Linux</p>"
            "<p>Inspired by "
            '<a href="https://github.com/gibbed/SteamAchievementManager">'
            "gibbed's SAM</a></p>"
            "<p>Uses the Steam flat C API via ctypes.</p>"
            "<p><b>WARNING:</b> Use at your own risk. Misuse may result in "
            "account restrictions.</p>",
        )

    def closeEvent(self, event):
        if self._loader_thread and self._loader_thread.isRunning():
            self._loader_thread.quit()
            self._loader_thread.wait(2000)
        for worker in self._active_workers:
            if worker.isRunning():
                worker.quit()
                worker.wait(2000)
        self._active_workers.clear()
        if self._game_loader and self._game_loader.isRunning():
            self._game_loader.quit()
            self._game_loader.wait(2000)
        event.accept()


def run_gui():
    app = QApplication(sys.argv)
    app.setApplicationName("SAM for Linux")
    # No setStyle(): the widget theme follows the system Qt platform theme.

    window = MainWindow()
    window.show()

    sys.exit(app.exec())
