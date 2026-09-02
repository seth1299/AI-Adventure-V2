from __future__ import annotations

from ai_adventure.ui.common import *  # noqa: F401,F403
from ai_adventure.ui.dialogues import *  # noqa: F401,F403


class CombatScreen(RepositoryBackedWidget):
    """Deterministic saved combat manager."""

    def __init__(self, *, playtesting_tools: bool = False) -> None:
        super().__init__()

        self.playtesting_tools = bool(playtesting_tools)
        self._scheduled_npc_actor_id = ""
        self._scheduled_npc_repository: SaveRepository | None = None
        self.npc_turn_timer = QTimer(self)
        self.npc_turn_timer.setSingleShot(True)
        self.npc_turn_timer.setInterval(NPC_TURN_DELAY_MS)
        self.npc_turn_timer.timeout.connect(self._resolve_scheduled_npc_turn)
        self.status_label = QLabel("No active combat.")
        self.combatants_table = _AppTableWidget(0, 11)
        self.combatants_table.setHorizontalHeaderLabels(
            [
                "Turn",
                "Name",
                "Team",
                "Initiative",
                "Health",
                "Armor",
                "To Hit",
                "Threat",
                "Ammo",
                "Damage",
                "Loot/Status",
            ]
        )
        self.combatants_table.horizontalHeader().setStretchLastSection(True)
        self.combatants_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.combatants_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)

        self.target_combo = QComboBox()
        self.attack_button = QPushButton("Attack / Resolve Turn")
        self.attack_button.clicked.connect(self._resolve_current_turn)
        self.end_turn_button = QPushButton("End Turn")
        self.end_turn_button.clicked.connect(self._end_turn_without_attack)
        self.reload_button = QPushButton("Reload / End Turn")
        self.reload_button.clicked.connect(self._reload_current_weapon)
        self.resolve_button = QPushButton("Mark Combat Resolved")
        self.resolve_button.clicked.connect(self._resolve_combat_manually)
        self.team_combo = QComboBox()
        self.team_combo.addItem("Enemy", "enemy")
        self.team_combo.addItem("Player Party", "party")
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Bandit, wolf, guard ally...")
        self.health_input = QSpinBox()
        self.health_input.setRange(1, 9999)
        self.health_input.setValue(8)
        self.armor_input = QSpinBox()
        self.armor_input.setRange(1, 99)
        self.armor_input.setValue(10)
        self.to_hit_input = QSpinBox()
        self.to_hit_input.setRange(-99, 99)
        self.to_hit_input.setValue(0)
        self.initiative_input = QSpinBox()
        self.initiative_input.setRange(-99, 99)
        self.personality_combo = QComboBox()

        for personality in COMBAT_PERSONALITIES:
            self.personality_combo.addItem(personality.title(), personality)

        self.ammunition_type_input = QLineEdit()
        self.ammunition_type_input.setPlaceholderText(
            "Optional, e.g. 9mm Round"
        )
        self.clip_size_input = QSpinBox()
        self.clip_size_input.setRange(0, 9999)
        self.clip_ammo_input = QSpinBox()
        self.clip_ammo_input.setRange(0, 9999)
        self.clip_size_input.valueChanged.connect(self._sync_clip_inputs)
        self.bullets_per_attack_input = QSpinBox()
        self.bullets_per_attack_input.setRange(1, 9999)
        self.reserve_ammo_input = QSpinBox()
        self.reserve_ammo_input.setRange(0, 999999)
        self.damage_input = QLineEdit("1d6")
        self.loot_input = QLineEdit()
        self.loot_input.setPlaceholderText("Optional loot names separated by commas")
        self.add_combatant_button = QPushButton("Add Combatant")
        self.add_combatant_button.clicked.connect(self._add_combatant)
        self.start_button = QPushButton("Start Combat")
        self.start_button.clicked.connect(self._start_combat)

        self.adjust_target_combo = QComboBox()
        self.adjust_amount_input = QSpinBox()
        self.adjust_amount_input.setRange(1, 9999)
        self.adjust_amount_input.setValue(1)
        self.damage_button = QPushButton("Apply Damage")
        self.damage_button.clicked.connect(lambda: self._adjust_health(-self.adjust_amount_input.value()))
        self.heal_button = QPushButton("Heal")
        self.heal_button.clicked.connect(lambda: self._adjust_health(self.adjust_amount_input.value()))

        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)

        action_group = QGroupBox("Current Turn")
        action_layout = QFormLayout()
        action_layout.addRow("Target:", self.target_combo)
        action_layout.addRow(
            _button_row(
                self.attack_button,
                self.reload_button,
                self.end_turn_button,
                self.resolve_button,
            )
        )
        action_group.setLayout(action_layout)

        self.add_group = QGroupBox("Combatants")
        add_layout = QFormLayout()
        add_layout.addRow("Team:", self.team_combo)
        add_layout.addRow("Name:", self.name_input)
        add_layout.addRow("Health:", self.health_input)
        add_layout.addRow("Armor Rating:", self.armor_input)
        add_layout.addRow("To-Hit Bonus:", self.to_hit_input)
        add_layout.addRow("Initiative Bonus:", self.initiative_input)
        add_layout.addRow("Personality:", self.personality_combo)
        add_layout.addRow("Ammunition Type:", self.ammunition_type_input)
        add_layout.addRow("Clip Size:", self.clip_size_input)
        add_layout.addRow("Loaded Ammo:", self.clip_ammo_input)
        add_layout.addRow("Bullets / Attack:", self.bullets_per_attack_input)
        add_layout.addRow("Reserve Ammo:", self.reserve_ammo_input)
        add_layout.addRow("Damage:", self.damage_input)
        add_layout.addRow("Loot:", self.loot_input)
        add_layout.addRow(_button_row(self.start_button, self.add_combatant_button))
        self.add_group.setLayout(add_layout)

        self.adjust_group = QGroupBox("Damage and Recovery")
        adjust_layout = QFormLayout()
        adjust_layout.addRow("Combatant:", self.adjust_target_combo)
        adjust_layout.addRow("Amount:", self.adjust_amount_input)
        adjust_layout.addRow(_button_row(self.damage_button, self.heal_button))
        self.adjust_group.setLayout(adjust_layout)

        self.resolve_button.setVisible(self.playtesting_tools)
        self.add_group.setVisible(self.playtesting_tools)
        self.adjust_group.setVisible(self.playtesting_tools)

        controls = QVBoxLayout()
        controls.addWidget(action_group)
        controls.addWidget(self.add_group)
        controls.addWidget(self.adjust_group)
        controls.addStretch()
        controls_widget = QWidget()
        controls_widget.setLayout(controls)
        controls_scroll = QScrollArea()
        controls_scroll.setWidgetResizable(True)
        controls_scroll.setWidget(controls_widget)

        main_row = QHBoxLayout()
        main_row.addWidget(self.combatants_table, stretch=2)
        main_row.addWidget(controls_scroll, stretch=1)

        layout = QVBoxLayout()
        layout.addWidget(self.status_label)
        layout.addLayout(main_row)
        layout.addWidget(QLabel("Combat Log"))
        layout.addWidget(self.log_output)
        self.setLayout(layout)

    def set_repository(self, repository: SaveRepository | None) -> None:
        """Cancels delayed actions before changing the active save."""

        self._cancel_scheduled_npc_turn()
        super().set_repository(repository)

    def refresh(self) -> None:
        """Reloads saved combat state."""

        repository = self.repository()

        if repository is None:
            self._cancel_scheduled_npc_turn()
            self.status_label.setText("No active combat.")
            self.combatants_table.setRowCount(0)
            self.target_combo.clear()
            self.adjust_target_combo.clear()
            self.log_output.clear()
            self._sync_buttons(False)
            return

        combat_state = repository.get_combat_state()
        self._render_combat_state(combat_state)
        if not combat_state.get("active") and self._uses_narrative_combat(repository):
            self.status_label.setText(
                "Narrative combat is enabled. Gemini resolves fights in Story."
            )

    def _schedule_npc_turn(self, combat_state: dict[str, Any]) -> None:
        """Schedules the current NPC to act after the reading delay."""

        repository = self.repository()
        combatants = combat_state.get("combatants", [])

        if (
            repository is None
            or not combat_state.get("active")
            or not combatants
        ):
            self._cancel_scheduled_npc_turn()
            return

        actor = combatants[int(combat_state.get("turn_index", 0))]
        actor_id = str(actor.get("id", ""))

        if actor_id == "player" or actor.get("defeated"):
            self._cancel_scheduled_npc_turn()
            return

        if (
            self.npc_turn_timer.isActive()
            and self._scheduled_npc_actor_id == actor_id
            and self._scheduled_npc_repository is repository
        ):
            return

        self.npc_turn_timer.stop()
        self._scheduled_npc_actor_id = actor_id
        self._scheduled_npc_repository = repository
        self.npc_turn_timer.start(NPC_TURN_DELAY_MS)

    def _cancel_scheduled_npc_turn(self) -> None:
        """Cancels any NPC action waiting on the reading delay."""

        if hasattr(self, "npc_turn_timer"):
            self.npc_turn_timer.stop()
        self._scheduled_npc_actor_id = ""
        self._scheduled_npc_repository = None

    def _resolve_scheduled_npc_turn(self) -> None:
        """Resolves the still-current NPC after its delay expires."""

        self.npc_turn_timer.stop()
        repository = self.repository()
        expected_repository = self._scheduled_npc_repository
        expected_actor_id = self._scheduled_npc_actor_id
        self._scheduled_npc_actor_id = ""
        self._scheduled_npc_repository = None

        if repository is None or repository is not expected_repository:
            return

        combat_state = repository.get_combat_state()
        combatants = combat_state.get("combatants", [])

        if not combat_state.get("active") or not combatants:
            return

        actor = combatants[int(combat_state.get("turn_index", 0))]

        if (
            str(actor.get("id", "")) != expected_actor_id
            or expected_actor_id == "player"
            or actor.get("defeated")
        ):
            self.refresh()
            return

        self._resolve_current_turn()

    def _start_combat(self) -> None:
        """Starts deterministic combat with the player and first opponent."""

        repository = self.repository()

        if repository is None:
            return

        if self._uses_narrative_combat(repository):
            self.status_label.setText(
                "Narrative combat is enabled. Gemini resolves fights in Story."
            )
            return

        state = StateManager(repository).load_state()
        inventory_items = repository.list_inventory_items()
        equipment = repository.get_player_equipment()
        attack_skill = equipped_weapon_attack_skill(equipment, inventory_items)
        weapon_profile = equipped_weapon_combat_profile(
            equipment,
            inventory_items,
        )
        armor_rating = armor_rating_from_equipment(equipment, inventory_items)
        player = {
            "id": "player",
            "name": state.player.name or "Player",
            "team": "party",
            "current_health": max(0, int(state.player.health_current)),
            "max_health": max(1, int(state.player.health_max)),
            "armor_rating": armor_rating,
            "to_hit_bonus": attack_bonus_from_skills(
                attack_skill,
                repository.list_skills(),
            ),
            "initiative_bonus": _safe_int(
                repository.get_setting("player.initiative_bonus", 0),
                0,
            ),
            "personality": "balanced",
            **weapon_profile,
            "clip_ammo": self._stored_player_clip_ammo(
                repository,
                weapon_profile,
            ),
            "reserve_ammo": 0,
            "damage": equipped_weapon_damage(equipment, inventory_items),
            "status_effects": [],
            "loot": [],
            "defeated": int(state.player.health_current) <= 0,
        }
        enemy = self._combatant_from_inputs(
            default_team="enemy",
            fallback_name="Enemy",
            use_selected_team=False,
        )
        combatants = roll_combat_initiative(
            [player, enemy],
            rng=random,
        )
        initiative_order = ", ".join(
            (
                f"{combatant_display_name(combatant)} "
                f"({combatant['initiative_total']})"
            )
            for combatant in combatants
        )
        combat_state = {
            "active": True,
            "round": 1,
            "turn_index": 0,
            "combatants": combatants,
            "log": [
                f"Combat begins: {player['name']} faces {enemy['name']}.",
                f"Initiative order: {initiative_order}.",
            ],
        }
        repository.set_combat_state(combat_state)
        repository.append_history("system", "Combat started.")
        self.refresh()
        self.notify_repository_changed()

    def _add_combatant(self) -> None:
        """Adds a party member or enemy to active combat."""

        repository = self.repository()

        if repository is None:
            return

        combat_state = repository.get_combat_state()

        if not combat_state.get("active"):
            self._start_combat()
            return

        current_actor_id = str(
            combat_state["combatants"][int(combat_state["turn_index"])].get(
                "id",
                "",
            )
        )
        combatant = self._combatant_from_inputs(
            default_team=str(self.team_combo.currentData() or "enemy"),
            fallback_name="Combatant",
            index=len(combat_state["combatants"]) + 1,
        )
        roll_combat_initiative([combatant], rng=random)
        combat_state["combatants"].append(combatant)
        combat_state["combatants"].sort(
            key=lambda entry: (
                -int(entry.get("initiative_total", 0)),
                -int(entry.get("initiative_bonus", 0)),
                str(entry.get("id", "")),
            )
        )
        combat_state = normalize_combat_state(combat_state)
        combat_state["turn_index"] = next(
            (
                index
                for index, entry in enumerate(combat_state["combatants"])
                if str(entry.get("id", "")) == current_actor_id
            ),
            0,
        )
        added_combatant = next(
            (
                entry
                for entry in combat_state["combatants"]
                if str(entry.get("id", "")) == str(combatant["id"])
            ),
            combatant,
        )
        combat_state["log"].append(
            f"{combatant_display_name(added_combatant)} joins the fight "
            f"with initiative {added_combatant.get('initiative_total', 0)}."
        )
        repository.set_combat_state(combat_state)
        self.refresh()
        self.notify_repository_changed()

    def _resolve_current_turn(self) -> None:
        """Resolves the current combatant's attack."""

        repository = self.repository()

        if repository is None:
            return

        combat_state = repository.get_combat_state()

        if not combat_state.get("active"):
            return

        self._cancel_scheduled_npc_turn()
        combatants = combat_state["combatants"]
        turn_index = int(combat_state["turn_index"])
        actor = combatants[turn_index]

        if actor.get("defeated"):
            self._advance_turn(combat_state)
            repository.set_combat_state(combat_state)
            self.refresh()
            self.notify_repository_changed()
            return

        if str(actor.get("id", "")) != "player":
            self._resolve_npc_turn(repository, combat_state, actor)
            return

        target = self._target_for_actor(actor, combatants)

        if target is None:
            self._resolve_combat(repository, combat_state)
            return

        if not self._consume_attack_ammunition(repository, actor):
            combat_state["log"].append(
                f"{combatant_display_name(actor)} cannot attack: reload "
                f"{actor.get('ammunition_type_required', 'ammunition')} first."
            )
            repository.set_combat_state(combat_state)
            self.refresh()
            return

        self._perform_attack(combat_state, actor, target)
        self._finish_combat_action(repository, combat_state)

    def _perform_attack(
        self,
        combat_state: dict[str, Any],
        actor: dict[str, Any],
        target: dict[str, Any],
    ) -> None:
        """Rolls and applies one attack."""

        attack_roll = random.randint(1, 20)
        to_hit_bonus = int(actor.get("to_hit_bonus", 0))
        attack_total = attack_roll + to_hit_bonus
        target_armor = int(target.get("armor_rating", 10))
        hit = attack_roll == 20 or (
            attack_roll != 1
            and attack_total >= target_armor
        )
        roll_detail = (
            f"{attack_roll}{to_hit_bonus:+d}={attack_total}"
            if to_hit_bonus
            else str(attack_roll)
        )

        if hit:
            damage, damage_detail = roll_damage_expression(actor.get("damage", DEFAULT_UNARMED_DAMAGE))
            target["current_health"] = max(0, int(target["current_health"]) - damage)
            target["defeated"] = target["current_health"] <= 0
            combat_state["log"].append(
                f"{combatant_display_name(actor)} hits "
                f"{combatant_display_name(target)} with {roll_detail} vs AR {target_armor}, "
                f"dealing {damage} damage [{damage_detail}]."
            )

            if target["defeated"]:
                combat_state["log"].append(
                    f"{combatant_display_name(target)} is defeated."
                )
        else:
            combat_state["log"].append(
                f"{combatant_display_name(actor)} misses "
                f"{combatant_display_name(target)} with {roll_detail} "
                f"vs AR {target_armor}."
            )

    def _finish_combat_action(
        self,
        repository: SaveRepository,
        combat_state: dict[str, Any],
    ) -> None:
        """Persists an action and advances unless combat ended."""

        combatants = combat_state["combatants"]
        self._sync_player_health_from_combat(repository, combat_state)

        if combat_team_defeated(combatants, "enemy") or combat_team_defeated(combatants, "party"):
            self._resolve_combat(repository, combat_state)
            return

        self._advance_turn(combat_state)
        repository.set_combat_state(combat_state)
        self.refresh()
        self.notify_repository_changed()

    def _resolve_npc_turn(
        self,
        repository: SaveRepository,
        combat_state: dict[str, Any],
        actor: dict[str, Any],
    ) -> None:
        """Resolves one NPC turn with deterministic personality rules."""

        target = self._npc_target_for_actor(
            actor,
            combat_state["combatants"],
        )

        if target is None:
            self._resolve_combat(repository, combat_state)
            return

        if actor.get("personality") == "intelligent":
            hit_chance = attack_hit_probability(
                int(actor.get("to_hit_bonus", 0)),
                int(target.get("armor_rating", 10)),
            )
            max_health = max(1, int(target.get("max_health", 1)))
            wounded_percent = round(
                (1.0 - (int(target.get("current_health", 0)) / max_health))
                * 100
            )
            combat_state["log"].append(
                f"{combatant_display_name(actor)} selects "
                f"{combatant_display_name(target)}: "
                f"{round(hit_chance * 100)}% hit chance, "
                f"{wounded_percent}% wounded."
            )
        else:
            combat_state["log"].append(
                f"{combatant_display_name(actor)} targets "
                f"{combatant_display_name(target)} based on its "
                f"{target.get('threat_level', 0)}% Threat Level."
            )

        if not self._consume_attack_ammunition(repository, actor):
            loaded = self._reload_actor_ammunition(repository, actor)

            if loaded > 0:
                combat_state["log"].append(
                    f"{combatant_display_name(actor)} reloads {loaded} "
                    f"{actor.get('ammunition_type_required', 'rounds')}."
                )
            else:
                combat_state["log"].append(
                    f"{combatant_display_name(actor)} is out of "
                    f"{actor.get('ammunition_type_required', 'ammunition')}."
                )

            self._finish_combat_action(repository, combat_state)
            return

        self._perform_attack(combat_state, actor, target)
        self._finish_combat_action(repository, combat_state)

    def _npc_target_for_actor(
        self,
        actor: dict[str, Any],
        combatants: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Selects by threat unless the NPC uses intelligent tactical targeting."""

        enemy_team = "party" if actor.get("team") == "enemy" else "enemy"
        candidates = [
            combatant
            for combatant in combatants
            if combatant.get("team") == enemy_team
            and not combatant.get("defeated")
        ]

        if not candidates:
            return None

        if actor.get("personality") != "intelligent":
            threat_levels = calculate_team_threat_levels(
                combatants,
                enemy_team,
            )
            roll = random.randint(1, 100)
            cumulative = 0

            for candidate in candidates:
                threat = threat_levels.get(
                    str(candidate.get("id", "")),
                    0,
                )
                candidate["threat_level"] = threat
                cumulative += threat

                if roll <= cumulative:
                    return candidate

            return candidates[-1]

        def target_score(target: dict[str, Any]) -> tuple[float, float, int]:
            hit_probability = attack_hit_probability(
                int(actor.get("to_hit_bonus", 0)),
                int(target.get("armor_rating", 10)),
            )
            max_health = max(1, int(target.get("max_health", 1)))
            current_health = max(0, int(target.get("current_health", 0)))
            wounded_ratio = 1.0 - (current_health / max_health)
            combined_score = (hit_probability * 0.65) + (wounded_ratio * 0.35)
            return combined_score, wounded_ratio, -current_health

        return max(candidates, key=target_score)

    def _consume_attack_ammunition(
        self,
        repository: SaveRepository,
        actor: dict[str, Any],
    ) -> bool:
        """Consumes loaded rounds for an attack when the weapon requires them."""

        ammunition_type = str(
            actor.get("ammunition_type_required", "")
        ).strip()

        if not ammunition_type:
            return True

        bullets_per_attack = max(1, int(actor.get("bullets_per_attack", 1)))
        clip_ammo = max(0, int(actor.get("clip_ammo", 0)))

        if clip_ammo < bullets_per_attack:
            return False

        actor["clip_ammo"] = clip_ammo - bullets_per_attack

        if str(actor.get("id", "")) == "player":
            self._persist_player_clip_ammo(repository, actor)

        return True

    def _reload_current_weapon(self) -> None:
        """Reloads the current actor and consumes the turn."""

        repository = self.repository()

        if repository is None:
            return

        combat_state = repository.get_combat_state()

        if not combat_state.get("active"):
            return

        actor = combat_state["combatants"][int(combat_state["turn_index"])]
        loaded = self._reload_actor_ammunition(repository, actor)

        if loaded <= 0:
            combat_state["log"].append(
                f"{combatant_display_name(actor)} cannot reload."
            )
            repository.set_combat_state(combat_state)
            self.refresh()
            return

        combat_state["log"].append(
            f"{combatant_display_name(actor)} reloads {loaded} "
            f"{actor.get('ammunition_type_required', 'rounds')}."
        )
        self._finish_combat_action(repository, combat_state)

    def _reload_actor_ammunition(
        self,
        repository: SaveRepository,
        actor: dict[str, Any],
    ) -> int:
        """Moves reserve ammunition into an actor's clip."""

        ammunition_type = str(
            actor.get("ammunition_type_required", "")
        ).strip()
        clip_size = max(0, int(actor.get("clip_size", 0)))
        clip_ammo = max(0, int(actor.get("clip_ammo", 0)))
        needed = max(0, clip_size - clip_ammo)

        if not ammunition_type or needed <= 0:
            return 0

        if str(actor.get("id", "")) == "player":
            loaded = self._consume_inventory_ammunition(
                repository,
                ammunition_type,
                needed,
            )
        else:
            reserve_ammo = max(0, int(actor.get("reserve_ammo", 0)))
            loaded = min(needed, reserve_ammo)
            actor["reserve_ammo"] = reserve_ammo - loaded

        actor["clip_ammo"] = clip_ammo + loaded

        if str(actor.get("id", "")) == "player":
            self._persist_player_clip_ammo(repository, actor)

        return loaded

    @staticmethod
    def _consume_inventory_ammunition(
        repository: SaveRepository,
        ammunition_type: str,
        amount: int,
    ) -> int:
        """Consumes matching ammunition stacks from inventory."""

        remaining = max(0, amount)
        consumed = 0

        for item in repository.list_inventory_items():
            metadata = item_metadata(item)

            if str(metadata.get("item_type", "")).casefold() != "ammunition":
                continue
            if (
                str(metadata.get("ammunition_type", "")).casefold()
                != ammunition_type.casefold()
            ):
                continue

            available = max(0, int(item.get("quantity", 0)))
            used = min(remaining, available)

            if used <= 0:
                continue

            repository.remove_inventory_item(str(item.get("name", "")), used)
            consumed += used
            remaining -= used

            if remaining <= 0:
                break

        return consumed

    @staticmethod
    def _stored_player_clip_ammo(
        repository: SaveRepository,
        weapon_profile: dict[str, Any],
    ) -> int:
        """Loads the durable clip count for the equipped player weapon."""

        clip_size = max(0, int(weapon_profile.get("clip_size", 0)))
        weapon_name = str(weapon_profile.get("weapon_name", "")).casefold()
        stored_clips = repository.get_setting("player.weapon_clip_ammo", {})

        if not isinstance(stored_clips, dict) or not weapon_name:
            return clip_size

        return max(
            0,
            min(
                clip_size,
                _safe_int(stored_clips.get(weapon_name, clip_size), clip_size),
            ),
        )

    @staticmethod
    def _persist_player_clip_ammo(
        repository: SaveRepository,
        actor: dict[str, Any],
    ) -> None:
        """Stores the player's loaded rounds by weapon name."""

        weapon_name = str(actor.get("weapon_name", "")).casefold()

        if not weapon_name:
            return

        stored_clips = repository.get_setting("player.weapon_clip_ammo", {})
        clean_clips = dict(stored_clips) if isinstance(stored_clips, dict) else {}
        clean_clips[weapon_name] = max(0, int(actor.get("clip_ammo", 0)))
        repository.set_setting("player.weapon_clip_ammo", clean_clips)

    def _end_turn_without_attack(self) -> None:
        """Skips the active combatant's turn."""

        repository = self.repository()

        if repository is None:
            return

        combat_state = repository.get_combat_state()

        if not combat_state.get("active"):
            return

        actor = combat_state["combatants"][int(combat_state["turn_index"])]
        combat_state["log"].append(
            f"{combatant_display_name(actor)} holds position."
        )
        self._advance_turn(combat_state)
        repository.set_combat_state(combat_state)
        self.refresh()
        self.notify_repository_changed()

    def _resolve_combat_manually(self) -> None:
        """Marks combat resolved without more attacks."""

        repository = self.repository()

        if repository is None:
            return

        combat_state = repository.get_combat_state()

        if not combat_state.get("active"):
            return

        combat_state["log"].append("Combat is marked resolved.")
        self._sync_player_health_from_combat(repository, combat_state)
        self._clear_resolved_battlefield(combat_state)
        repository.set_combat_state(combat_state)
        repository.append_history("system", "Combat resolved.")
        self.refresh()
        self.notify_repository_changed()

    def _adjust_health(self, delta: int) -> None:
        """Applies direct damage or healing to a combatant."""

        repository = self.repository()

        if repository is None:
            return

        combat_state = repository.get_combat_state()

        if not combat_state.get("combatants"):
            return

        combatant_id = str(self.adjust_target_combo.currentData() or "")

        for combatant in combat_state["combatants"]:
            if combatant.get("id") != combatant_id:
                continue

            old_health = int(combatant["current_health"])
            new_health = max(0, min(old_health + delta, int(combatant["max_health"])))
            combatant["current_health"] = new_health
            combatant["defeated"] = new_health <= 0
            verb = "heals" if delta > 0 else "takes"
            combat_state["log"].append(
                f"{combatant_display_name(combatant)} {verb} {abs(delta)}; "
                f"health is now "
                f"{new_health}/{combatant['max_health']}."
            )
            break

        self._sync_player_health_from_combat(repository, combat_state)

        if combat_state.get("active") and (
            combat_team_defeated(combat_state["combatants"], "enemy")
            or combat_team_defeated(combat_state["combatants"], "party")
        ):
            self._resolve_combat(repository, combat_state)
            return

        repository.set_combat_state(combat_state)
        self.refresh()
        self.notify_repository_changed()

    def _combatant_from_inputs(
        self,
        *,
        default_team: str,
        fallback_name: str,
        index: int = 1,
        use_selected_team: bool = True,
    ) -> dict[str, Any]:
        """Builds a combatant from the input row."""

        name = self.name_input.text().strip() or fallback_name
        damage = normalize_damage_expression(self.damage_input.text(), default="1d6")
        team = str(self.team_combo.currentData() or default_team) if use_selected_team else default_team

        if team not in {"party", "enemy"}:
            team = default_team

        ammunition_type = self.ammunition_type_input.text().strip()
        clip_size = self.clip_size_input.value() if ammunition_type else 0
        return {
            "id": f"{team}-{index}-{_slug_for_id(name)}",
            "name": name,
            "team": team,
            "current_health": self.health_input.value(),
            "max_health": self.health_input.value(),
            "armor_rating": self.armor_input.value(),
            "to_hit_bonus": self.to_hit_input.value(),
            "initiative_bonus": self.initiative_input.value(),
            "personality": self.personality_combo.currentData() or "balanced",
            "weapon_name": "",
            "ammunition_type_required": ammunition_type,
            "clip_size": clip_size,
            "clip_ammo": min(self.clip_ammo_input.value(), clip_size),
            "bullets_per_attack": (
                min(self.bullets_per_attack_input.value(), clip_size)
                if ammunition_type and clip_size > 0
                else 0
            ),
            "reserve_ammo": self.reserve_ammo_input.value(),
            "damage": damage,
            "status_effects": [],
            "loot": _split_loot_items(self.loot_input.text()) if team == "enemy" else [],
            "defeated": False,
        }

    def _sync_clip_inputs(self, clip_size: int) -> None:
        """Keeps playtesting clip controls inside the selected capacity."""

        self.clip_ammo_input.setMaximum(max(0, clip_size))
        self.bullets_per_attack_input.setMaximum(max(1, clip_size))

        if clip_size > 0 and self.clip_ammo_input.value() == 0:
            self.clip_ammo_input.setValue(clip_size)

    def _target_for_actor(
        self,
        actor: dict[str, Any],
        combatants: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Returns the selected or automatic attack target."""

        enemy_team = "party" if actor.get("team") == "enemy" else "enemy"

        if actor.get("team") == "party":
            selected_id = str(self.target_combo.currentData() or "")

            for combatant in combatants:
                if combatant.get("id") == selected_id and not combatant.get("defeated"):
                    return combatant

        for combatant in combatants:
            if combatant.get("team") == enemy_team and not combatant.get("defeated"):
                return combatant

        return None

    def _advance_turn(self, combat_state: dict[str, Any]) -> None:
        """Moves to the next living combatant."""

        old_index = int(combat_state["turn_index"])
        new_index = next_living_index(combat_state["combatants"], old_index)

        if new_index <= old_index:
            combat_state["round"] = int(combat_state.get("round", 1)) + 1

        combat_state["turn_index"] = new_index

    def _resolve_combat(
        self,
        repository: SaveRepository,
        combat_state: dict[str, Any],
    ) -> None:
        """Finishes combat, stores state, and grants defeated-enemy loot."""

        party_defeated = combat_team_defeated(combat_state["combatants"], "party")
        enemies_defeated = combat_team_defeated(combat_state["combatants"], "enemy")

        if enemies_defeated and not party_defeated:
            granted_loot: list[str] = []

            for combatant in combat_state["combatants"]:
                if combatant.get("team") != "enemy" or not combatant.get("defeated"):
                    continue

                for loot_name in combatant.get("loot", []):
                    repository.add_inventory_item(
                        loot_name,
                        "Loot",
                        1,
                        (
                            "Loot recovered from "
                            f"{combatant_display_name(combatant)}."
                        ),
                        0,
                    )
                    granted_loot.append(loot_name)

            if granted_loot:
                combat_state["log"].append("Recovered loot: " + ", ".join(granted_loot) + ".")

            combat_state["log"].append("Combat resolved: victory.")
            repository.append_history("system", "Combat resolved: victory.")
        elif party_defeated:
            combat_state["log"].append("Combat resolved: party defeated.")
            repository.append_history("system", "Combat resolved: party defeated.")
        else:
            combat_state["log"].append("Combat resolved.")
            repository.append_history("system", "Combat resolved.")

        self._sync_player_health_from_combat(repository, combat_state)
        self._clear_resolved_battlefield(combat_state)
        repository.set_combat_state(combat_state)
        self.refresh()
        self.notify_repository_changed()

    def _sync_player_health_from_combat(
        self,
        repository: SaveRepository,
        combat_state: dict[str, Any],
    ) -> None:
        """Persists player health from the player combatant."""

        for combatant in combat_state.get("combatants", []):
            if combatant.get("id") == "player":
                repository.set_setting("player.health_current", int(combatant["current_health"]))
                repository.set_setting("player.health_max", int(combatant["max_health"]))
                repository.set_setting("player.armor_rating", int(combatant["armor_rating"]))
                repository.set_state_value(
                    "condition",
                    "Incapacitated" if int(combatant["current_health"]) <= 0 else "Healthy",
                )
                self._persist_player_clip_ammo(repository, combatant)
                continue

            npc_id = str(combatant.get("npc_id", "") or "").strip()
            if npc_id and combatant.get("team") == "party":
                current_health = int(combatant.get("current_health", -1))
                max_health = int(combatant.get("max_health", -1))
                repository.upsert_party_member(
                    npc_id,
                    status=(
                        "Incapacitated"
                        if current_health <= 0
                        else "Wounded"
                        if max_health >= 0 and current_health < max_health
                        else "Active"
                    ),
                    health_current=current_health,
                    health_max=max_health,
                    armor_class=int(combatant.get("armor_rating", -1)),
                )

    @staticmethod
    def _clear_resolved_battlefield(combat_state: dict[str, Any]) -> None:
        """Clears active participants while preserving the completed combat log."""

        combat_state["active"] = False
        combat_state["round"] = 1
        combat_state["turn_index"] = 0
        combat_state["combatants"] = []

    def _render_combat_state(self, combat_state: dict[str, Any]) -> None:
        """Renders saved combat state."""

        active = bool(combat_state.get("active", False))
        combatants = combat_state.get("combatants", []) if active else []
        current_id = ""

        if active and combatants:
            turn_index = int(combat_state.get("turn_index", 0))
            actor = combatants[turn_index]
            current_id = str(actor.get("id", ""))
            status = (
                f"Round {combat_state.get('round', 1)} - "
                f"{combatant_display_name(actor)}'s turn"
            )

            if current_id != "player":
                status += " (acting automatically in 2 seconds...)"

            self.status_label.setText(status)
        else:
            self.status_label.setText("No active combat.")

        self.combatants_table.setRowCount(len(combatants))

        for row_index, combatant in enumerate(combatants):
            current_marker = "->" if combatant.get("id") == current_id else ""
            status_bits = []

            if combatant.get("defeated"):
                status_bits.append("Defeated")

            if combatant.get("status_effects"):
                status_bits.extend(str(effect) for effect in combatant.get("status_effects", []))

            loot_text = ", ".join(str(item) for item in combatant.get("loot", []))

            if loot_text:
                status_bits.append(f"Loot: {loot_text}")

            self.combatants_table.setItem(row_index, 0, _table_item(current_marker))
            self.combatants_table.setItem(
                row_index,
                1,
                _table_item(combatant_display_name(combatant)),
            )
            self.combatants_table.setItem(row_index, 2, _table_item(str(combatant["team"])))
            self.combatants_table.setItem(
                row_index,
                3,
                _table_item(
                    f"{combatant.get('initiative_total', 0)} "
                    f"({combatant.get('initiative_roll', 0)}"
                    f"{int(combatant.get('initiative_bonus', 0)):+d})"
                ),
            )
            self.combatants_table.setItem(
                row_index,
                4,
                _table_item(f"{combatant['current_health']}/{combatant['max_health']}"),
            )
            self.combatants_table.setItem(row_index, 5, _table_item(str(combatant["armor_rating"])))
            to_hit_bonus = int(combatant.get("to_hit_bonus", 0))
            self.combatants_table.setItem(
                row_index,
                6,
                _table_item(f"{to_hit_bonus:+d}"),
            )
            self.combatants_table.setItem(
                row_index,
                7,
                _table_item(f"{combatant.get('threat_level', 0)}%"),
            )
            ammunition_type = str(
                combatant.get("ammunition_type_required", "")
            )
            ammo_text = (
                f"{combatant.get('clip_ammo', 0)}/"
                f"{combatant.get('clip_size', 0)} {ammunition_type}"
                if ammunition_type
                else "-"
            )
            self.combatants_table.setItem(
                row_index,
                8,
                _table_item(ammo_text),
            )
            self.combatants_table.setItem(row_index, 9, _table_item(str(combatant["damage"])))
            self.combatants_table.setItem(row_index, 10, _table_item("; ".join(status_bits)))

        self.combatants_table.resizeColumnsToContents()
        self._populate_target_combos(combat_state)
        self.log_output.setPlainText("\n".join(str(entry) for entry in combat_state.get("log", [])))
        self.log_output.moveCursor(self.log_output.textCursor().MoveOperation.End)
        self._sync_buttons(active)
        self._schedule_npc_turn(combat_state)

    def _populate_target_combos(self, combat_state: dict[str, Any]) -> None:
        """Reloads target dropdowns from combatants."""

        self.target_combo.clear()
        self.adjust_target_combo.clear()
        combatants = (
            combat_state.get("combatants", [])
            if combat_state.get("active")
            else []
        )
        actor = None

        if combat_state.get("active") and combatants:
            actor = combatants[int(combat_state.get("turn_index", 0))]

        for combatant in combatants:
            if combatant.get("defeated"):
                continue

            label = (
                f"{combatant_display_name(combatant)} "
                f"({combatant['team']})"
            )
            self.adjust_target_combo.addItem(label, combatant["id"])

            if actor is None:
                continue

            if combatant.get("team") != actor.get("team"):
                self.target_combo.addItem(label, combatant["id"])

    def _sync_buttons(self, combat_active: bool) -> None:
        """Enables combat controls for the active state."""

        repository = self.repository()
        narrative_combat = bool(
            repository
            and not combat_active
            and self._uses_narrative_combat(repository)
        )
        combat_state = (
            repository.get_combat_state()
            if repository is not None and combat_active
            else {}
        )
        combatants = combat_state.get("combatants", [])
        actor = (
            combatants[int(combat_state.get("turn_index", 0))]
            if combatants
            else None
        )
        player_turn = bool(
            combat_active
            and actor is not None
            and actor.get("id") == "player"
        )
        self.attack_button.setText("Attack / Resolve Turn")
        self.attack_button.setEnabled(player_turn)
        self.end_turn_button.setEnabled(player_turn)
        self.reload_button.setEnabled(player_turn)
        self.target_combo.setEnabled(player_turn)
        manual_action_visible = not combat_active or player_turn
        self.attack_button.setVisible(manual_action_visible)
        self.end_turn_button.setVisible(manual_action_visible)
        self.reload_button.setVisible(manual_action_visible)
        self.resolve_button.setEnabled(combat_active)
        self.add_combatant_button.setEnabled(
            repository is not None and not narrative_combat
        )
        self.start_button.setEnabled(
            repository is not None and not combat_active and not narrative_combat
        )
        self.damage_button.setEnabled(bool(self.adjust_target_combo.count()))
        self.heal_button.setEnabled(bool(self.adjust_target_combo.count()))

    @staticmethod
    def _uses_narrative_combat(repository: SaveRepository) -> bool:
        """Returns whether this save delegates combat resolution to Gemini."""

        preferences = normalize_combat_preferences(
            repository.get_setting(
                "combat.preferences",
                {
                    "resolution_mode": repository.get_setting(
                        "combat.resolution_mode", "strict"
                    ),
                    "focus": repository.get_setting("combat.focus", "balanced"),
                },
            )
        )
        return preferences["resolution_mode"] == "narrative"
