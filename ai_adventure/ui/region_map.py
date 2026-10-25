"""Regional map with application-rendered markers and readable location text."""
from __future__ import annotations

from collections import defaultdict
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QBrush, QFont, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QDialog, QGraphicsScene, QGraphicsView, QHBoxLayout,
    QLabel, QPushButton, QTextBrowser, QVBoxLayout,
)

from ai_adventure.region_map import regional_map_layout, regional_map_request


class RegionalMapDialog(QDialog):
    """Free vector map with optional generated or uploaded terrain artwork."""

    def __init__(self, screen):
        super().__init__(screen)
        self.screen = screen
        self.repository = screen.repository()
        self.setWindowTitle("Regional Map")
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.setSizeGripEnabled(True)
        self.resize(1200, 820)
        self.scene = QGraphicsScene(self)
        self.view = QGraphicsView(self.scene)
        self.view.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.legend = QTextBrowser()
        self.legend.setMinimumWidth(290)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.generate_button = QPushButton("Generate Map Background")
        self.generate_button.clicked.connect(self._generate)
        upload = QPushButton("Select Map Background...")
        upload.clicked.connect(self._upload)
        copy = QPushButton("Copy Map Prompt")
        copy.clicked.connect(self._copy_prompt)
        fit = QPushButton("Fit Map")
        fit.clicked.connect(self._fit)
        zoom_in, zoom_out = QPushButton("Zoom In"), QPushButton("Zoom Out")
        zoom_in.clicked.connect(lambda: self.view.scale(1.25, 1.25))
        zoom_out.clicked.connect(lambda: self.view.scale(0.8, 0.8))
        controls = QHBoxLayout()
        for button in (self.generate_button, upload, copy, fit, zoom_in, zoom_out):
            controls.addWidget(button)
        body = QHBoxLayout()
        body.addWidget(self.view, 3)
        body.addWidget(self.legend, 1)
        layout = QVBoxLayout(self)
        layout.addLayout(controls)
        layout.addWidget(self.status)
        layout.addLayout(body)
        self._signature = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(1000)
        self.refresh()

    def showEvent(self, event):
        super().showEvent(event)
        self._fit()

    def _fit(self):
        self.view.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def _copy_prompt(self):
        QApplication.clipboard().setText(regional_map_request(self.repository).external_prompt)

    def _generate(self):
        self.screen.create_visual_asset("region_map", "region")
        self.refresh()

    def _upload(self):
        self.screen.choose_visual_asset("region_map", "region")
        self.refresh()

    def refresh(self):
        request = regional_map_request(self.repository)
        asset = self.repository.get_visual_asset_by_id(request.asset_id)
        current = self.repository.get_state_value("location", "")
        signature = (request.asset_id, current, str(asset),
                     self.screen.visual_asset_generation_enabled())
        if signature == self._signature:
            return
        self._signature = signature
        self.generate_button.setVisible(self.screen.visual_asset_generation_enabled())
        busy = bool(asset and asset.get("status") in {"queued", "generating"})
        self.generate_button.setEnabled(not busy)
        self.scene.clear()
        self.scene.setSceneRect(0, 0, 1000, 1000)
        self.scene.setBackgroundBrush(QColor("#e8e2ce"))
        path = self.screen.visual_asset_path(asset) if asset and asset.get("status") == "ready" else None
        if path:
            pixmap = QPixmap(str(path)).scaled(1000, 1000, Qt.AspectRatioMode.IgnoreAspectRatio,
                                               Qt.TransformationMode.SmoothTransformation)
            self.scene.addPixmap(pixmap)
        points, unmapped, span = regional_map_layout(self.repository.ensure_travel_locations())
        groups = defaultdict(list)
        lines = ["KNOWN REGION", "North is up. Distances are straight-line map miles.",
                 "Numbered markers match the locations below. Shared markers mean shared map positions, not arrival.", ""]
        for number, (place, x, y) in enumerate(sorted(points, key=lambda point: point[0].name.casefold()), 1):
            groups[(x, y)].append((number, place))
            here = " — CURRENTLY HERE" if place.name.casefold() == str(current).casefold() else ""
            lines.extend([f"{number}. {place.name}{here}",
                          f"Within: {place.parent_location}" if place.parent_location else "",
                          f"Terrain: {place.terrain or 'Not recorded'}",
                          f"Map position: ({place.x_miles:g}, {place.y_miles:g}) miles",
                          place.description, place.travel_notes, ""])
        for (x, y), entries in groups.items():
            here = any(p.name.casefold() == str(current).casefold() for _, p in entries)
            self.scene.addEllipse(x - 9, y - 9, 18, 18, QPen(QColor("#ffffff"), 2),
                                  QBrush(QColor("#b82c27" if here else "#214f75")))
            label = self.scene.addText(", ".join(str(n) for n, _ in entries), QFont("Segoe UI", 15, QFont.Weight.Bold))
            label.setDefaultTextColor(QColor("#111111"))
            # A solid backing keeps marker text legible over any artwork.
            rect = label.boundingRect().translated(x + 13, y - 17)
            backing = self.scene.addRect(rect, QPen(Qt.PenStyle.NoPen), QBrush(QColor("#fffbea")))
            label.setPos(x + 13, y - 17)
            label.setZValue(2)
            backing.setZValue(1)
        north = self.scene.addText("N ↑", QFont("Segoe UI", 18, QFont.Weight.Bold))
        north.setDefaultTextColor(QColor("#111111"))
        north.setPos(25, 20)
        if span:
            self.scene.addLine(35, 950, 135, 950, QPen(QColor("#111111"), 3))
            scale = self.scene.addText(f"{span / 7.8:.2f} miles", QFont("Segoe UI", 12))
            scale.setDefaultTextColor(QColor("#111111"))
            scale.setPos(35, 955)
        if unmapped:
            lines.extend(["POSITIONS UNKNOWN OR CONFLICTING", "These places are not plotted:", ""])
            for place in unmapped:
                lines.extend([place.name + (" — CURRENTLY HERE" if place.name.casefold() == str(current).casefold() else ""),
                              f"Within: {place.parent_location}" if place.parent_location else "",
                              place.description, ""])
        self.legend.setPlainText("\n".join(lines))
        status = "Map background is generating…" if busy else "Labels and markers are rendered by the application. Terrain artwork is illustrative."
        if asset and asset.get("status") == "failed":
            status = "Map background failed: " + str(asset.get("error_message", "Unknown error"))
        self.status.setText(status)
