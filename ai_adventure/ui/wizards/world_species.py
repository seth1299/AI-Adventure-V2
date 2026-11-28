"""Wizard page for choosing and defining the world's species roster."""

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QMenu, QMessageBox, QPlainTextEdit, QPushButton,
    QVBoxLayout, QWizard, QWizardPage,
)

from ai_adventure.world_species import SpeciesLibrary, SpeciesProfile, bundled_species, normalize_world_species


class WorldSpeciesPage(QWizardPage):
    roster_changed = Signal()

    def __init__(self, wizard: QWizard, saved_species_dir: Path | None) -> None:
        super().__init__(wizard)
        self.setTitle("World Species")
        self.setSubTitle("Choose which species inhabit your world.")
        self.library = SpeciesLibrary(saved_species_dir) if saved_species_dir is not None else None
        self._bundled = sorted(bundled_species(), key=lambda profile: profile["name"].casefold())
        self._profiles: list[SpeciesProfile] = []

        hint = QLabel("Leave the list empty to let the AI decide which species fit the setting.")
        hint.setWordWrap(True)
        self.roster = QListWidget()
        self.roster.setAccessibleName("Species in this world")
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setAccessibleName("Selected species details")
        self.add_button = QPushButton("Add New Species", self)
        self.add_menu = QMenu(self.add_button)
        self.add_button.setMenu(self.add_menu)
        self.edit_button = QPushButton("Edit…", self)
        self.remove_button = QPushButton("Remove", self)

        self.add_menu.aboutToShow.connect(self._populate_menu)
        self.roster.currentRowChanged.connect(self._show_details)
        self.roster.itemDoubleClicked.connect(self._edit_selected)
        self.edit_button.clicked.connect(self._edit_selected)
        self.remove_button.clicked.connect(self._remove_selected)

        controls = QHBoxLayout()
        for button in (self.add_button, self.edit_button, self.remove_button):
            button.setMinimumSize(button.sizeHint())
            controls.addWidget(button)
        controls.addStretch()
        content = QHBoxLayout()
        content.addWidget(self.roster, 1)
        content.addWidget(self.details, 2)
        layout = QVBoxLayout(self)
        layout.addWidget(hint)
        layout.addLayout(controls)
        layout.addLayout(content, 1)
        self._show_details(-1)

    def species(self) -> list[SpeciesProfile]:
        return normalize_world_species(self._profiles)

    def set_species(self, profiles: object) -> None:
        self._profiles = normalize_world_species(profiles)
        self._refresh(0)

    def _refresh(self, row: int) -> None:
        self.roster.clear()
        self.roster.addItems([profile["name"] for profile in self._profiles])
        self.roster.setCurrentRow(min(row, len(self._profiles) - 1))
        self.roster_changed.emit()

    def _show_details(self, row: int) -> None:
        selected = 0 <= row < len(self._profiles)
        self.edit_button.setEnabled(selected)
        self.remove_button.setEnabled(selected)
        if not selected:
            self.details.clear()
            return
        profile = self._profiles[row]
        genders = ", ".join(profile["genders"])
        genres = ", ".join(profile["typical_genres"])
        self.details.setPlainText(
            f"{profile['name']}\n\n"
            f"Genders: {genders or 'Unspecified'}\n"
            f"Typical maturation age: {profile['typical_maturation_age'] or 'Unspecified'}\n"
            f"Typical lifespan: {profile['typical_lifespan'] or 'Unspecified'}\n"
            f"Typical height: {profile['typical_height'] or 'Unspecified'}\n"
            f"Typical weight: {profile['typical_weight'] or 'Unspecified'}\n"
            f"Typical genres: {genres or 'Unspecified'}\n\n"
            f"{profile['description']}"
        )

    def _populate_menu(self) -> None:
        self.add_menu.clear()
        self.add_menu.addAction("Create Custom Species…", lambda: self._edit_species())
        included = {profile["name"].casefold() for profile in self._profiles}
        saved = self.library.list() if self.library is not None else []
        for title, profiles in (("Saved Species", saved), ("Bundled Species", self._bundled)):
            self.add_menu.addSection(title)
            if not profiles:
                self.add_menu.addAction("No species available").setEnabled(False)
            for profile in profiles:
                action = self.add_menu.addAction(profile["name"])
                action.setEnabled(profile["name"].casefold() not in included)
                action.triggered.connect(lambda checked=False, choice=profile: self._add_species(choice))

    def _add_species(self, profile: SpeciesProfile) -> None:
        self._profiles.append(profile)
        self._refresh(len(self._profiles) - 1)

    def _remove_selected(self) -> None:
        row = self.roster.currentRow()
        if row >= 0:
            self._profiles.pop(row)
            self._refresh(row)

    def _edit_selected(self) -> None:
        row = self.roster.currentRow()
        if row >= 0:
            self._edit_species(row)

    def _edit_species(self, row: int | None = None) -> None:
        profile = self._profiles[row] if row is not None else None
        dialog = QDialog(self)
        dialog.setWindowTitle("Edit Species" if profile else "Create Custom Species")
        dialog.resize(540, 420)
        name = QLineEdit(profile["name"] if profile else "")
        name.setObjectName("speciesName")
        genders = QLineEdit(", ".join(profile["genders"]) if profile else "")
        genders.setObjectName("speciesGenders")
        genders.setPlaceholderText("Separate multiple entries with commas")
        maturation = QLineEdit(profile["typical_maturation_age"] if profile else "")
        maturation.setObjectName("speciesMaturation")
        maturation.setPlaceholderText("For example, around 18 years")
        lifespan = QLineEdit(profile["typical_lifespan"] if profile else "")
        lifespan.setObjectName("speciesLifespan")
        lifespan.setPlaceholderText("For example, 70–90 years")
        height = QLineEdit(profile["typical_height"] if profile else "")
        height.setObjectName("speciesHeight")
        height.setPlaceholderText("For example, 150–180 cm")
        weight = QLineEdit(profile["typical_weight"] if profile else "")
        weight.setObjectName("speciesWeight")
        weight.setPlaceholderText("For example, 120–180 pounds")
        genres = QLineEdit(", ".join(profile["typical_genres"]) if profile else "")
        genres.setObjectName("speciesGenres")
        genres.setPlaceholderText("Separate multiple entries with commas")
        description = QPlainTextEdit(profile["description"] if profile else "")
        description.setObjectName("speciesDescription")
        description.setTabChangesFocus(True)
        reusable = QCheckBox("Save to Species Library")
        reusable.setChecked(profile is None)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel, parent=dialog,
        )
        for button in buttons.buttons():
            button.setMinimumSize(button.sizeHint())
        save = buttons.button(QDialogButtonBox.StandardButton.Save)
        save.setEnabled(bool(name.text().strip()))
        name.textChanged.connect(lambda text: save.setEnabled(bool(text.strip())))
        buttons.rejected.connect(dialog.reject)

        form = QFormLayout(dialog)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.addRow("Species name", name)
        form.addRow("Genders", genders)
        form.addRow("Typical maturation age", maturation)
        form.addRow("Typical lifespan", lifespan)
        form.addRow("Typical height", height)
        form.addRow("Typical weight", weight)
        form.addRow("Typical genres", genres)
        form.addRow("Species description", description)
        if self.library is not None:
            form.addRow(reusable)
        form.addRow(buttons)

        def save_profile() -> None:
            [updated] = normalize_world_species([{
                "name": name.text(), "genders": genders.text(),
                "typical_maturation_age": maturation.text(), "typical_lifespan": lifespan.text(),
                "typical_height": height.text(), "typical_weight": weight.text(),
                "typical_genres": genres.text(),
                "description": description.toPlainText(),
            }])
            key = updated["name"].casefold()
            if any(i != row and other["name"].casefold() == key for i, other in enumerate(self._profiles)):
                QMessageBox.warning(dialog, "Species Already Added", "Use a different species name for this world.")
                return
            if self.library is not None and reusable.isChecked():
                if any(saved["name"].casefold() == key for saved in self.library.list()):
                    answer = QMessageBox.question(
                        dialog, "Replace Saved Species",
                        f"Replace the saved definition for {updated['name']} in your Species Library?",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No,
                    )
                    if answer != QMessageBox.StandardButton.Yes:
                        return
                try:
                    self.library.save(updated)
                except (OSError, ValueError) as error:
                    QMessageBox.warning(dialog, "Could Not Save Species", str(error))
                    return
            if row is None:
                self._add_species(updated)
            else:
                self._profiles[row] = updated
                self._refresh(row)
            dialog.accept()

        buttons.accepted.connect(save_profile)
        dialog.exec()
        dialog.deleteLater()
