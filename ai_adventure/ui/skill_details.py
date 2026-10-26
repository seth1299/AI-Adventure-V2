"""Player-facing skill details and icon controls."""

from ai_adventure.ui.common import *  # noqa: F401,F403
from ai_adventure.ui.crafting_details import CraftingDetailsDialog


class SkillDetailsDialog(CraftingDetailsDialog):
    def __init__(self, *, skill, modifier, image_path=None, on_select_image=None,
                 on_create_image=None, parent=None, survival_modifier=0):
        level = int(skill.get("level", 1))
        super().__init__(
            title=str(skill.get("name", "Skill")),
            sections=[
                ("Related Stat", str(skill.get("attribute", "Intelligence"))),
                ("Training", f"{_skill_level_label(level)} · Level {level} · +{level} skill bonus"),
                ("XP Progress", _skill_xp_progress_label(skill)),
                ("Typical Check Bonus", f"{modifier:+d} stat modifier + {level} training" +
                 (f" {survival_modifier:+d} survival" if survival_modifier else "") +
                 f" = {modifier + level + survival_modifier:+d}"),
                ("Description", str(skill.get("description", ""))),
            ], parent=parent,
        )
        self.icon_preview = QLabel()
        self.icon_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        _set_generated_image(self.icon_preview, image_path, maximum_width=128,
                             maximum_height=128, accessible_name=f"{skill.get('name', 'Skill')} icon")
        layout = self.layout()
        layout.insertWidget(1, self.icon_preview)
        controls = QHBoxLayout()
        if on_select_image is not None:
            button = QPushButton("Select Icon...")
            button.setObjectName("skillSelectIconButton")
            button.clicked.connect(on_select_image)
            controls.addWidget(button)
        if on_create_image is not None:
            button = QPushButton("Generate New Icon")
            button.setObjectName("skillGenerateIconButton")
            button.clicked.connect(on_create_image)
            controls.addWidget(button)
        layout.insertLayout(2, controls)
