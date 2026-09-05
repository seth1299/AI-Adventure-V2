from __future__ import annotations

from ai_adventure.ui.common import *  # noqa: F401,F403
from ai_adventure.ui.dialogues import *  # noqa: F401,F403
from ai_adventure.ui.screens.combat import CombatScreen
from ai_adventure.ui.screens.merchant import MerchantScreen
from ai_adventure.ui.workers.visual_assets import _VisualAssetCoordinator
from ai_adventure.ui.screens.alchemy import *  # noqa: F401,F403
from ai_adventure.ui.screens.bestiary import *  # noqa: F401,F403
from ai_adventure.ui.screens.calendar import *  # noqa: F401,F403
from ai_adventure.ui.screens.character import *  # noqa: F401,F403
from ai_adventure.ui.screens.combat import *  # noqa: F401,F403
from ai_adventure.ui.screens.inventory import *  # noqa: F401,F403
from ai_adventure.ui.screens.magic import *  # noqa: F401,F403
from ai_adventure.ui.screens.npcs import *  # noqa: F401,F403
from ai_adventure.ui.screens.notes import *  # noqa: F401,F403
from ai_adventure.ui.screens.party import *  # noqa: F401,F403
from ai_adventure.ui.screens.settings import *  # noqa: F401,F403
from ai_adventure.ui.screens.skills import *  # noqa: F401,F403
from ai_adventure.ui.screens.story import *  # noqa: F401,F403
from ai_adventure.ui.screens.tasks import *  # noqa: F401,F403
from ai_adventure.ui.screens.travel import *  # noqa: F401,F403


class _DetachableTabBar(QTabBar):
    """Movable tab bar that requests detachment after an outside drag release."""

    detach_requested = Signal(str, QPoint)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pressed_tab_key = ""
        self._press_global_position = QPoint()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Remembers the stable key for the tab being dragged."""

        index = self.tabAt(event.position().toPoint())
        self._pressed_tab_key = (
            str(self.tabData(index) or "") if index >= 0 else ""
        )
        self._press_global_position = event.globalPosition().toPoint()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Detaches a dragged tab when the pointer is released outside the bar."""

        global_position = event.globalPosition().toPoint()
        dragged_far_enough = (
            global_position - self._press_global_position
        ).manhattanLength() >= QApplication.startDragDistance()
        released_outside = not self.rect().contains(event.position().toPoint())
        tab_key = self._pressed_tab_key
        super().mouseReleaseEvent(event)
        self._pressed_tab_key = ""

        if tab_key and dragged_far_enough and released_outside:
            self.detach_requested.emit(tab_key, global_position)


class _DetachedTabWindow(QMainWindow):
    """Independent, non-modal window containing one detached game screen."""

    return_requested = Signal(str)

    def __init__(
        self,
        tab_key: str,
        title: str,
        screen: QWidget,
        parent: QWidget,
    ) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.tab_key = tab_key
        self.setWindowTitle(title)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setCentralWidget(screen)
        # QTabWidget.removeTab() leaves its page explicitly hidden. Reparenting
        # the page as a central widget does not clear that hidden state, so make
        # the transferred screen visible before this window is shown.
        screen.show()
        self.resize(1000, 720)

    def closeEvent(self, event: Any) -> None:
        """Returns the screen to the main tab strip before destroying the window."""

        self.takeCentralWidget()
        self.return_requested.emit(self.tab_key)
        event.accept()


class GameShell(QWidget):
    """In-game shell containing the core play screens."""

    def __init__(
        self,
        on_return_to_menu,
        *,
        on_theme_changed=None,
        sound_manager: SoundManagerProtocol | None = None,
        narration_player: NarrationPlayerProtocol | None = None,
        tts_enabled: bool = True,
        on_app_tts_settings_saved: Callable[[dict[str, Any]], None] | None = None,
        global_tts_settings_provider: Callable[[], dict[str, Any]] | None = None,
        custom_voice_storage_path: Path | str | None = None,
        gemini_api_key_path: Path | str | None = None,
        generated_images_dir: Path | str | None = None,
        playtesting_tools: bool = False,
        ai_enabled: bool = True,
    ) -> None:
        """
        Args:
            on_return_to_menu: Callback for returning to the Main Menu.
        """

        super().__init__()

        self.on_return_to_menu = on_return_to_menu
        self.on_theme_changed = on_theme_changed
        self.sound_manager = sound_manager
        self.narration_player = narration_player
        self.tts_enabled = bool(tts_enabled)
        self.on_app_tts_settings_saved = on_app_tts_settings_saved
        self.global_tts_settings_provider = global_tts_settings_provider
        self.custom_voice_storage_path = custom_voice_storage_path
        self.gemini_api_key_path = (
            Path(gemini_api_key_path).expanduser().resolve()
            if gemini_api_key_path is not None
            else None
        )
        self.generated_images_dir = (
            Path(generated_images_dir).expanduser().resolve()
            if generated_images_dir is not None
            else Path.cwd() / "images"
        )
        self.playtesting_tools = bool(playtesting_tools)
        self.ai_enabled = bool(ai_enabled)
        self.repository: SaveRepository | None = None
        self.visual_asset_coordinator = _VisualAssetCoordinator(
            images_dir=self.generated_images_dir,
            api_key_path=(
                self.gemini_api_key_path or Path.cwd() / ".gemini_api_key.txt"
            ),
            enabled=(
                self.ai_enabled
                and not self.playtesting_tools
                and gemini_api_key_path is not None
                and generated_images_dir is not None
            ),
            parent=self,
        )
        self.visual_asset_coordinator.assets_changed.connect(
            self._handle_visual_assets_changed
        )
        self.title_label = QLabel("No Save Loaded")
        self.title_label.setStyleSheet("font-size: 18px; font-weight: bold;")

        self.menu_button = QPushButton("Main Menu")
        self.menu_button.clicked.connect(self.on_return_to_menu)

        top_bar = QHBoxLayout()
        top_bar.addWidget(self.title_label)
        top_bar.addStretch()
        top_bar.addWidget(self.menu_button)

        self.tabs = QTabWidget()
        self.tab_bar = _DetachableTabBar(self.tabs)
        self.tabs.setTabBar(self.tab_bar)
        self.tabs.setMovable(True)
        self.tabs.setTabsClosable(True)
        self.tabs.setDocumentMode(True)
        self._tab_specs: dict[str, tuple[RepositoryBackedWidget, str, bool]] = {}
        self._tab_order: list[str] = []
        self._detached_windows: dict[str, _DetachedTabWindow] = {}
        self._tab_notifications: set[str] = set()
        self._tab_content_signatures: dict[str, str] = {}
        self._smart_hidden_tabs: set[str] = set()

        self.story_screen = StoryScreen(
            sound_manager=self.sound_manager,
            narration_player=self.narration_player,
            api_key_path=self.gemini_api_key_path,
        )
        self.character_screen = CharacterScreen(
            playtesting_tools=self.playtesting_tools,
            tts_enabled=self.tts_enabled,
        )
        self.travel_screen = TravelScreen(
            on_travel_requested=self._submit_travel_request,
        )
        self.bestiary_screen = BestiaryScreen()
        self.calendar_screen = CalendarScreen(playtesting_tools=self.playtesting_tools)
        self.inventory_screen = InventoryScreen(
            playtesting_tools=self.playtesting_tools,
        )
        self.merchant_screen = MerchantScreen()
        self.combat_screen = CombatScreen(
            playtesting_tools=self.playtesting_tools,
        )
        self.npcs_screen = NpcsScreen()
        self.party_screen = PartyScreen()
        self.active_tasks_screen = ActiveTasksScreen()
        self.skills_screen = SkillsScreen()
        self.magic_screen = MagicScreen()
        self.alchemy_screen = AlchemyNotebookScreen(
            playtesting_tools=self.playtesting_tools,
        )
        self.notes_screen = NotesScreen()
        self.settings_screen = SettingsScreen(
            on_audio_settings_changed=self._apply_audio_settings,
            on_theme_changed=self._apply_theme,
            tts_enabled=self.tts_enabled,
            voice_options=_narrator_voice_options(self.narration_player),
            on_sample_voice=self._sample_narrator_voice,
            on_app_tts_settings_saved=self.on_app_tts_settings_saved,
            global_tts_settings_provider=self.global_tts_settings_provider,
            custom_voice_storage_path=self.custom_voice_storage_path,
            ai_enabled=self.ai_enabled,
            sound_manager=self.sound_manager,
            music_enabled=not self.playtesting_tools,
            playtesting_tools=self.playtesting_tools,
        )

        self.screens: list[RepositoryBackedWidget] = [
            self.story_screen,
            self.character_screen,
            self.travel_screen,
            self.bestiary_screen,
            self.calendar_screen,
            self.inventory_screen,
            self.merchant_screen,
            self.combat_screen,
            self.npcs_screen,
            self.party_screen,
            self.active_tasks_screen,
            self.skills_screen,
            self.magic_screen,
            self.alchemy_screen,
            self.notes_screen,
            self.settings_screen,
        ]

        for screen in self.screens:
            screen.set_visual_assets_dir(self.generated_images_dir)
            screen.on_repository_changed = self._handle_screen_repository_changed

        visible_tabs = (
            [
                ("character", self.character_screen, "Character", True),
                ("calendar", self.calendar_screen, "Calendar", True),
                ("inventory", self.inventory_screen, "Inventory", True),
                ("merchant", self.merchant_screen, "Merchant", True),
                ("magic", self.magic_screen, "Magic", True),
                ("combat", self.combat_screen, "Combat", True),
                ("party", self.party_screen, "Party", True),
                ("settings", self.settings_screen, "Settings", True),
            ]
            if self.playtesting_tools
            else [
                ("conversation", self.story_screen, "Conversation", False),
                ("character", self.character_screen, "Character", True),
                ("travel", self.travel_screen, "Travel", True),
                ("bestiary", self.bestiary_screen, "Bestiary", True),
                ("calendar", self.calendar_screen, "Calendar", True),
                ("inventory", self.inventory_screen, "Inventory", True),
                ("merchant", self.merchant_screen, "Merchant", True),
                ("combat", self.combat_screen, "Combat", True),
                ("party", self.party_screen, "Party", True),
                ("npcs", self.npcs_screen, "NPCs", True),
                ("skills", self.skills_screen, "Skills", True),
                ("magic", self.magic_screen, "Magic", True),
                ("crafting", self.alchemy_screen, "Crafting", True),
                ("notes", self.notes_screen, "Notes", True),
                ("settings", self.settings_screen, "Settings", True),
            ]
        )
        for tab_key, screen, label, closable in visible_tabs:
            self._register_tab(tab_key, screen, label, closable)

        self.add_tab_button = QPushButton("+")
        self.add_tab_button.setToolTip("Restore a closed tab")
        self.add_tab_button.setFixedWidth(34)
        self.add_tab_button.setStyleSheet("font-size: 18px; font-weight: 700;")
        self.add_tab_menu = QMenu(self.add_tab_button)
        self.add_tab_menu.aboutToShow.connect(self._rebuild_add_tab_menu)
        self.add_tab_button.setMenu(self.add_tab_menu)
        self.tabs.setCornerWidget(
            self.add_tab_button,
            Qt.Corner.TopRightCorner,
        )

        self.tabs.tabCloseRequested.connect(self._close_tab)
        self.tab_bar.detach_requested.connect(self._detach_tab)
        self.tab_bar.tabMoved.connect(lambda _from, _to: self._sync_protected_tab_button())
        self.tabs.currentChanged.connect(self._handle_tab_changed)
        self._sync_protected_tab_button()

        layout = QVBoxLayout()
        layout.addLayout(top_bar)
        layout.addWidget(self.tabs)

        self.setLayout(layout)

    def _register_tab(
        self,
        tab_key: str,
        screen: RepositoryBackedWidget,
        label: str,
        closable: bool,
    ) -> None:
        """Registers and initially opens one restorable game tab."""

        self._tab_specs[tab_key] = (screen, label, closable)
        self._tab_order.append(tab_key)
        index = self.tabs.addTab(screen, label)
        self.tab_bar.setTabData(index, tab_key)

    def _tab_key_for_screen(self, screen: RepositoryBackedWidget) -> str:
        """Returns the stable tab key registered for a screen."""

        for tab_key, (registered, _label, _closable) in self._tab_specs.items():
            if registered is screen:
                return tab_key
        return ""

    def _display_tab_label(self, tab_key: str) -> str:
        """Returns a tab label with its unread-change marker, when present."""

        spec = self._tab_specs.get(tab_key)
        if spec is None:
            return tab_key
        label = spec[1]
        return f"{label} •" if tab_key in self._tab_notifications else label

    def _sync_tab_notification_display(self, tab_key: str) -> None:
        """Updates the docked tab or detached title for one notification state."""

        index = self._tab_index_for_key(tab_key)
        if index >= 0:
            self.tabs.setTabText(index, self._display_tab_label(tab_key))
            self.tabs.setTabToolTip(
                index,
                "Updated since you last viewed this tab."
                if tab_key in self._tab_notifications
                else "",
            )
        window = self._detached_windows.get(tab_key)
        if window is not None:
            window.setWindowTitle(
                self._detached_window_title(self._display_tab_label(tab_key))
            )

    def _mark_tab_changed(self, tab_key: str) -> None:
        """Marks a non-focused tab as having newly changed visible content."""

        if not tab_key:
            return
        current_index = self.tabs.currentIndex()
        if current_index >= 0 and self._tab_index_for_key(tab_key) == current_index:
            return
        self._tab_notifications.add(tab_key)
        self._sync_tab_notification_display(tab_key)

    def _clear_tab_changed(self, tab_key: str) -> None:
        """Clears one tab's unread-change marker after it receives focus."""

        if tab_key not in self._tab_notifications:
            return
        self._tab_notifications.discard(tab_key)
        self._sync_tab_notification_display(tab_key)
        self._rebuild_add_tab_menu()

    def _tab_index_for_key(self, tab_key: str) -> int:
        """Returns the current docked index for a stable tab key."""

        for index in range(self.tabs.count()):
            if str(self.tab_bar.tabData(index) or "") == tab_key:
                return index
        return -1

    def _sync_protected_tab_button(self) -> None:
        """Removes the close affordance from the protected Conversation tab."""

        for index in range(self.tabs.count()):
            tab_key = str(self.tab_bar.tabData(index) or "")
            spec = self._tab_specs.get(tab_key)
            if spec is not None and not spec[2]:
                self.tab_bar.setTabButton(
                    index,
                    QTabBar.ButtonPosition.RightSide,
                    None,
                )

    @Slot(int)
    def _close_tab(self, index: int) -> None:
        """Hides one closable tab until the player restores it from the plus menu."""

        tab_key = str(self.tab_bar.tabData(index) or "")
        spec = self._tab_specs.get(tab_key)
        if spec is None or not spec[2]:
            return

        screen = self.tabs.widget(index)
        self.tabs.removeTab(index)
        if screen is not None:
            screen.hide()
        self._rebuild_add_tab_menu()

    def _rebuild_add_tab_menu(self) -> None:
        """Lists every tab that is currently neither docked nor detached."""

        self.add_tab_menu.clear()
        missing_keys = [
            tab_key
            for tab_key in self._tab_order
            if self._tab_index_for_key(tab_key) < 0
            and tab_key not in self._detached_windows
        ]
        if not missing_keys:
            action = self.add_tab_menu.addAction("All tabs are open")
            action.setEnabled(False)
            return

        for tab_key in missing_keys:
            _screen, _label, _closable = self._tab_specs[tab_key]
            action = self.add_tab_menu.addAction(self._display_tab_label(tab_key))
            action.triggered.connect(
                lambda _checked=False, key=tab_key: self._restore_tab(key)
            )

    def _restore_tab(self, tab_key: str) -> None:
        """Returns one closed or detached screen to the main tab strip."""

        spec = self._tab_specs.get(tab_key)
        if spec is None:
            return

        existing_index = self._tab_index_for_key(tab_key)
        if existing_index >= 0:
            self.tabs.setCurrentIndex(existing_index)
            return

        screen, _label, _closable = spec
        screen.setParent(self.tabs)
        index = self.tabs.addTab(screen, self._display_tab_label(tab_key))
        self.tab_bar.setTabData(index, tab_key)
        screen.show()
        self.tabs.setCurrentIndex(index)
        self._smart_hidden_tabs.discard(tab_key)
        self._clear_tab_changed(tab_key)
        self._sync_protected_tab_button()

    @Slot(str, QPoint)
    def _detach_tab(self, tab_key: str, global_position: QPoint) -> None:
        """Moves one docked tab into an independent, maximizable window."""

        index = self._tab_index_for_key(tab_key)
        spec = self._tab_specs.get(tab_key)
        if index < 0 or spec is None or tab_key in self._detached_windows:
            return

        self._clear_tab_changed(tab_key)
        screen, label, _closable = spec
        self.tabs.removeTab(index)
        window = _DetachedTabWindow(
            tab_key,
            self._detached_window_title(label),
            screen,
            self,
        )
        window.return_requested.connect(self._return_detached_tab)
        self._detached_windows[tab_key] = window
        window.move(global_position - QPoint(80, 24))
        window.show()

    @Slot(str)
    def _return_detached_tab(self, tab_key: str) -> None:
        """Redocks a screen when its independent window is closed."""

        self._detached_windows.pop(tab_key, None)
        self._restore_tab(tab_key)

    def _detached_window_title(self, label: str) -> str:
        """Builds a useful title for a detached game-screen window."""

        adventure_title = self.title_label.text().strip()
        if not adventure_title or adventure_title == "No Save Loaded":
            return f"{label} - AI Adventure"
        return f"{label} - {adventure_title}"

    def _refresh_detached_window_titles(self) -> None:
        """Keeps detached windows aligned with the active save title."""

        for tab_key, window in self._detached_windows.items():
            spec = self._tab_specs.get(tab_key)
            if spec is not None:
                window.setWindowTitle(self._detached_window_title(spec[1]))

    def hideEvent(self, event: Any) -> None:
        """Hides detached screens when the game shell itself leaves view."""

        for window in self._detached_windows.values():
            window.hide()
        super().hideEvent(event)

    def showEvent(self, event: Any) -> None:
        """Restores detached screens when the player returns to the game shell."""

        super().showEvent(event)
        for window in self._detached_windows.values():
            window.show()

    def set_repository(
        self,
        repository: SaveRepository | None,
        *,
        initially_hide_empty_tabs: bool = False,
    ) -> None:
        """
        Sets the active save repository for every screen.

        Args:
            repository: Active save repository, or None when returning to menu.
        """

        self._restore_all_smart_hidden_tabs()
        self.repository = repository
        self._tab_notifications.clear()

        if repository is None:
            self.title_label.setText("No Save Loaded")
        else:
            title = repository.get_meta("title", default="Untitled Adventure")
            self.title_label.setText(title)

        self._refresh_detached_window_titles()

        for screen in self.screens:
            screen.set_repository(repository)

        for tab_key in self._tab_specs:
            self._sync_tab_notification_display(tab_key)

        if repository is not None and initially_hide_empty_tabs:
            self._hide_empty_starting_tabs(repository)

        self._tab_content_signatures = {
            tab_key: _screen_content_signature(screen)
            for tab_key, (screen, _label, _closable) in self._tab_specs.items()
        }
        self._apply_audio_settings()
        self.visual_asset_coordinator.scan(repository)

    def _hide_empty_starting_tabs(self, repository: SaveRepository) -> None:
        """Hides tabs that the new-game configuration says are initially irrelevant."""

        setup = repository.get_setting("new_game.setup", {})
        if not isinstance(setup, dict):
            setup = {}
        starting_npcs = setup.get("starting_npcs", [])
        starting_party = setup.get("starting_party_npc_ids", [])
        magic = setup.get("magic", {})
        if not isinstance(magic, dict):
            magic = {}
        requested_spells = magic.get("starting_spell_requests", [])
        starting_spells = magic.get("starting_spells", [])
        combat = setup.get("combat", {})
        if not isinstance(combat, dict):
            combat = {}
        combat_focus = str(
            combat.get("focus", repository.get_setting("combat.focus", "balanced"))
            or "balanced"
        ).strip().casefold()
        should_hide = {
            "npcs": not repository.list_player_visible_npcs()
            and not (isinstance(starting_npcs, list) and starting_npcs),
            "party": not repository.list_party_members()
            and not (isinstance(starting_party, list) and starting_party),
            "magic": not repository.list_character_spells()
            and not (isinstance(requested_spells, list) and requested_spells)
            and not (isinstance(starting_spells, list) and starting_spells),
            "combat": combat_focus == "low" and not repository.is_combat_active(),
        }
        for tab_key, hidden in should_hide.items():
            if not hidden:
                continue
            index = self._tab_index_for_key(tab_key)
            if index < 0 or tab_key in self._detached_windows:
                continue
            screen = self.tabs.widget(index)
            self.tabs.removeTab(index)
            if screen is not None:
                screen.hide()
            self._smart_hidden_tabs.add(tab_key)
        self._sync_protected_tab_button()
        self._rebuild_add_tab_menu()

    def _restore_all_smart_hidden_tabs(self) -> None:
        """Restores tabs hidden only by the previous new-game empty-state rule."""

        for tab_key in list(self._smart_hidden_tabs):
            self._dock_smart_hidden_tab(tab_key)
        self._smart_hidden_tabs.clear()

    def _dock_smart_hidden_tab(self, tab_key: str) -> None:
        """Docks a smart-hidden tab without stealing focus from the player."""

        spec = self._tab_specs.get(tab_key)
        if spec is None or self._tab_index_for_key(tab_key) >= 0:
            self._smart_hidden_tabs.discard(tab_key)
            return
        screen, _label, _closable = spec
        screen.setParent(self.tabs)
        index = self.tabs.addTab(screen, self._display_tab_label(tab_key))
        self.tab_bar.setTabData(index, tab_key)
        screen.show()
        self._smart_hidden_tabs.discard(tab_key)
        self._tab_content_signatures[tab_key] = _screen_content_signature(screen)
        self._sync_protected_tab_button()

    def _reveal_populated_smart_hidden_tabs(self) -> None:
        """Reveals startup-hidden tabs once their underlying content appears."""

        repository = self.repository
        if repository is None:
            return
        setup = repository.get_setting("new_game.setup", {})
        if not isinstance(setup, dict):
            setup = {}
        magic = setup.get("magic", {})
        if not isinstance(magic, dict):
            magic = {}
        world_contains_magic = bool(magic.get("world_contains_magic", True))
        combat = setup.get("combat", {})
        if not isinstance(combat, dict):
            combat = {}
        combat_focus = str(combat.get("focus", "balanced") or "balanced").casefold()
        has_content = {
            "npcs": bool(repository.list_player_visible_npcs()),
            "party": bool(repository.list_party_members()),
            "magic": world_contains_magic and bool(repository.list_character_spells()),
            "combat": combat_focus == "low" and repository.is_combat_active(),
        }
        for tab_key in list(self._smart_hidden_tabs):
            if has_content.get(tab_key, False):
                self._dock_smart_hidden_tab(tab_key)
        self._rebuild_add_tab_menu()

    def refresh_screens(
        self,
        *,
        exclude: set[RepositoryBackedWidget] | None = None,
    ) -> None:
        """Refreshes tabs from saved data while preserving each screen's local state."""

        excluded_screens = exclude or set()
        previous_signatures = dict(self._tab_content_signatures)

        for screen in self.screens:
            if screen in excluded_screens:
                continue

            screen.refresh()

        changed_source_keys = {
            self._tab_key_for_screen(screen) for screen in excluded_screens
        }
        for tab_key, (screen, _label, _closable) in self._tab_specs.items():
            signature = _screen_content_signature(screen)
            previous = previous_signatures.get(tab_key)
            self._tab_content_signatures[tab_key] = signature
            if (
                previous is not None
                and previous != signature
                and tab_key not in changed_source_keys
            ):
                self._mark_tab_changed(tab_key)
        self._reveal_populated_smart_hidden_tabs()
        self.visual_asset_coordinator.scan(self.repository)

    @Slot()
    def _handle_visual_assets_changed(self) -> None:
        """Refreshes image-bearing surfaces after one background generation completes."""

        self.refresh_screens()

    def _handle_screen_repository_changed(self, source: RepositoryBackedWidget) -> None:
        """Refreshes tabs after a screen or event changes repository data."""

        self._apply_audio_settings()
        self.refresh_screens(exclude={source})

    def _handle_tab_changed(self, index: int) -> None:
        """Handles tab-focus refreshes and clears unread-change markers."""

        if index < 0:
            return

        tab_key = str(self.tab_bar.tabData(index) or "")
        self._clear_tab_changed(tab_key)

        if self.tabs.widget(index) == self.calendar_screen:
            self.calendar_screen.return_to_current_month()

        if self.tabs.widget(index) == self.travel_screen:
            self.travel_screen.refresh()

        screen = self.tabs.widget(index)
        if isinstance(screen, RepositoryBackedWidget) and tab_key:
            self._tab_content_signatures[tab_key] = _screen_content_signature(screen)

    def _submit_travel_request(
        self,
        destination: dict[str, Any],
        player_context: str,
    ) -> bool:
        """Routes a Travel tab action through the ordinary story-turn pipeline."""

        return self.story_screen.submit_travel_request(destination, player_context)

    def _apply_audio_settings(self) -> None:
        """Applies saved audio settings to the active audio managers."""

        if self.repository is None:
            if self.sound_manager is not None:
                self.sound_manager.stop_music()
                self.sound_manager.stop_sound_effect()
            if self.narration_player is not None:
                self.narration_player.stop()
            return

        _apply_audio_settings_to_managers(
            self.repository,
            sound_manager=self.sound_manager,
            narration_player=self.narration_player,
        )

    def _sample_narrator_voice(
        self,
        voice: str,
        volume: int,
        speed: int = DEFAULT_TTS_SPEED_PERCENT,
    ) -> bool:
        """Plays a local narrator voice sample."""

        if self.narration_player is None or not hasattr(self.narration_player, "play_sample"):
            return False

        return bool(
            self.narration_player.play_sample(
                voice=normalize_narrator_voice_spec(voice),
                volume=volume,
                speed=speed,
            )
        )

    def _apply_theme(self) -> None:
        """Notifies the main window that save-specific theme settings changed."""

        if self.on_theme_changed is not None:
            self.on_theme_changed()
