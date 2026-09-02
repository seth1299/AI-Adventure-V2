from __future__ import annotations

from ai_adventure.ui.common import *  # noqa: F401,F403
from ai_adventure.ui.dialogues import *  # noqa: F401,F403


class TravelScreen(RepositoryBackedWidget):
    """Player-facing map knowledge, route estimates, and travel requests."""

    def __init__(
        self,
        *,
        on_travel_requested: Callable[[dict[str, Any], str], bool] | None = None,
    ) -> None:
        super().__init__()

        self.on_travel_requested = on_travel_requested
        self.location_list = QListWidget()
        self.location_list.currentItemChanged.connect(self._display_selected_location)

        self.details_output = QTextEdit()
        self.details_output.setReadOnly(True)

        self.location_image_label = QLabel()
        self.location_image_label.setObjectName("travelLocationImage")
        self.location_image_label.hide()

        self.travel_context_input = QTextEdit()
        self.travel_context_input.setPlaceholderText("Optional details for the GM")
        self.travel_context_input.setMaximumHeight(92)

        self.travel_button = QPushButton("Travel")
        self.travel_button.clicked.connect(self._request_travel)
        self.travel_button.setEnabled(False)

        list_layout = QVBoxLayout()
        list_layout.addWidget(QLabel("Known Locations"))
        list_layout.addWidget(self.location_list)

        details_layout = QVBoxLayout()
        details_layout.addWidget(self.location_image_label)
        details_layout.addWidget(self.details_output)
        details_layout.addWidget(QLabel("Travel Context"))
        details_layout.addWidget(self.travel_context_input)
        details_layout.addWidget(self.travel_button)

        layout = QHBoxLayout()
        layout.addLayout(list_layout, 1)
        layout.addLayout(details_layout, 2)
        self.setLayout(layout)

    def refresh(self) -> None:
        """Reloads known locations while preserving the visible selection."""

        repository = self.repository()
        current_location_name = ""
        selected_name = self._selected_location_name()
        self.location_list.blockSignals(True)
        self.location_list.clear()

        if repository is None:
            self.location_list.blockSignals(False)
            self.location_image_label.hide()
            self.details_output.clear()
            self.travel_button.setEnabled(False)
            return

        current_location_name = StateManager(repository).load_state().world.location
        locations = repository.ensure_travel_locations()

        for location in sorted(
            locations,
            key=lambda value: str(value.get("name", "")).casefold(),
        ):
            name = str(location.get("name", "")).strip()

            if not name:
                continue

            display_name = name
            if name.casefold() == current_location_name.casefold():
                display_name = f"{name} (Currently here)"

            item = QListWidgetItem(display_name)
            item.setData(
                Qt.ItemDataRole.UserRole,
                location,
            )
            item.setData(Qt.ItemDataRole.UserRole + 1, name)
            self.location_list.addItem(item)

        self.location_list.blockSignals(False)

        if self.location_list.count() == 0:
            self.location_image_label.hide()
            self.details_output.setPlainText("No locations are known yet.")
            self.travel_button.setEnabled(False)
            return

        target_name = current_location_name or selected_name
        target_row = 0

        for row in range(self.location_list.count()):
            item = self.location_list.item(row)

            item_name = (
                str(item.data(Qt.ItemDataRole.UserRole + 1) or "")
                if item is not None
                else ""
            )

            if item_name.casefold() == target_name.casefold():
                target_row = row
                break

        self.location_list.setCurrentRow(target_row)
        self._display_selected_location()

    def _selected_location_name(self) -> str:
        """Returns the currently selected location name, when any."""

        current_item = self.location_list.currentItem()
        if current_item is None:
            return ""

        return str(
            current_item.data(Qt.ItemDataRole.UserRole + 1)
            or current_item.text()
        ).strip()

    def _selected_location_data(self) -> dict[str, Any] | None:
        """Returns the selected location's persisted data."""

        current_item = self.location_list.currentItem()

        if current_item is None:
            return None

        raw_location = current_item.data(Qt.ItemDataRole.UserRole)
        return dict(raw_location) if isinstance(raw_location, dict) else None

    def _display_selected_location(self, *_args: Any) -> None:
        """Shows selected details and a mathematically calculated route estimate."""

        repository = self.repository()
        raw_destination = self._selected_location_data()
        destination = normalize_known_location(raw_destination)

        if repository is None or destination is None:
            self.location_image_label.hide()
            self.details_output.clear()
            self.travel_button.setEnabled(False)
            return

        state = StateManager(repository).load_state()
        origin = normalize_known_location(
            repository.find_travel_location(state.world.location)
        )
        estimate = calculate_travel_estimate(
            origin,
            destination,
            move_speed_mph=state.travel.move_speed_mph,
            travel_mode=state.travel.travel_mode,
            speed_multiplier=state.travel.speed_multiplier,
        )

        sections = [f"# {destination.name}"]

        if destination.description:
            sections.append(destination.description)

        travel_lines = [
            "## Travel",
            f"**From:** {state.world.location or 'Current location'}",
            f"**Distance:** {format_distance(estimate.distance_miles)}",
            f"**Estimated time:** {format_travel_time(estimate.estimated_minutes)}",
            (
                f"**Travel mode:** {estimate.travel_mode} "
                f"({estimate.effective_speed_mph:g} mph)"
            ),
        ]

        if not estimate.is_available:
            travel_lines.append(
                "A route estimate will be available once both locations have map positions."
            )

        sections.append("\n\n".join(travel_lines))

        conditions = []
        if destination.terrain:
            conditions.append(f"**Terrain:** {destination.terrain}")
        if destination.travel_notes:
            conditions.append(f"**Route notes:** {destination.travel_notes}")
        if conditions:
            sections.append("## Conditions\n\n" + "\n\n".join(conditions))

        _set_markdown_text(self.details_output, "\n\n".join(sections))
        location_asset = repository.get_visual_asset(
            "location",
            str(destination.location_id or destination.name).casefold(),
        )
        _set_generated_image(
            self.location_image_label,
            self.visual_asset_path(location_asset),
            maximum_width=560,
            maximum_height=280,
            accessible_name=f"Generated view of {destination.name}",
        )
        can_travel = (
            estimate.is_available
            and destination.name.casefold() != state.world.location.casefold()
            and self.on_travel_requested is not None
        )
        self.travel_button.setEnabled(can_travel)

    def _request_travel(self) -> None:
        """Sends the selected planned route through the normal story input flow."""

        destination = self._selected_location_data()

        if destination is None or self.on_travel_requested is None:
            return

        if self.on_travel_requested(
            destination,
            self.travel_context_input.toPlainText().strip(),
        ):
            self.travel_context_input.clear()
