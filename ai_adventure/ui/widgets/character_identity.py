"""Shared controls for a player character's identity."""

from PySide6.QtCore import QSignalBlocker
from ai_adventure.character_identity import MAX_CHARACTER_AGE, resolve_character_species
from ai_adventure.ui.primitives import _NoWheelComboBox, _NoWheelSpinBox
from ai_adventure.world_species import SpeciesProfile


class CharacterSpeciesCombo(_NoWheelComboBox):
    def set_species(self, roster: list[SpeciesProfile], selected: str, *, default_to_roster: bool = True) -> None:
        names = [profile["name"] for profile in roster]
        if default_to_roster:
            selected = resolve_character_species(selected, roster)
        if selected and selected not in names:
            names.append(selected)
        with QSignalBlocker(self):
            self.clear()
            if not selected:
                self.addItem("Not specified", "")
            for name in names:
                self.addItem(name, name)
            self.setCurrentIndex(self.findData(selected))
        self.setEnabled(bool(names))


class CharacterAgeInput(_NoWheelSpinBox):
    def __init__(self) -> None:
        super().__init__()
        self.setRange(0, MAX_CHARACTER_AGE)
        self.setSpecialValueText("Not specified")
        self.setToolTip("Enter a positive whole number of years.")
        self.setKeyboardTracking(False)

    def age(self) -> int | None:
        return self.value() or None
