import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from ai_adventure.ai import gemini_service as g
from ai_adventure.ai.new_game_repair_context import repair_context, repair_fields


class NewGameRepairScopeTests(unittest.TestCase):
    def setUp(self):
        self.schema = {
            "type": "object", "properties": {
                "bestiary": {"type": "array", "items": {"type": "object", "properties": {
                    "creature_id": {"type": "string"}, "name": {"type": "string"}},
                    "required": ["creature_id", "name"], "additionalProperties": False}},
                "character": {"type": "object"}, "starting_items": {"type": "array"},
                "world_summary": {"type": "string"},
            }, "required": ["bestiary", "character", "starting_items", "world_summary"], "additionalProperties": False,
        }
        self.candidate = {"bestiary": [{"creature_id": "wolf"}],
            "character": {"biography": "PRIVATE_CHARACTER_SENTINEL"},
            "starting_items": [{"name": "UNRELATED_ITEM_SENTINEL"}], "world_summary": "A misty forest."}
        self.setup = {"setup": {"specified_genre": "Fantasy", "world_context": "A forest realm.",
            "character": {"biography": "PRIVATE_CHARACTER_SENTINEL"},
            "starter_items": [{"name": "UNRELATED_ITEM_SENTINEL"}]},
            "requirements": {"bestiary": "Use stable creature IDs.", "starter_inventory": "UNRELATED_ITEM_RULE"}}
        self.prompt = "FULL_PHASE_CONTEXT " + json.dumps(self.candidate)
        self.good = {"bestiary": [{"creature_id": "wolf", "name": "Forest wolf"}]}

    def run_helper(self, responses):
        request = Mock(side_effect=[SimpleNamespace(text=value if isinstance(value, str) else json.dumps(value))
                                    for value in responses])
        with patch.object(g, "_generate_content_with_retry", request):
            result = g._generate_new_game_response_with_quality_retry(object(), model=g.DEFAULT_GEMINI_MODEL,
                prompt=self.prompt, response_schema=self.schema, config={"response_json_schema": self.schema},
                validation_schema=self.schema, setup_packet=self.setup)
        return json.loads(result), request

    def test_bestiary_repair_omits_character_inventory_and_preserves_them_on_merge(self):
        self.candidate["character"]["biography"] += "x" * 10000
        self.prompt = "FULL_PHASE_CONTEXT " + json.dumps(self.candidate)
        result, request = self.run_helper([self.candidate, self.good])
        repair = request.call_args.kwargs
        self.assertEqual(set(repair["config"]["response_json_schema"]["properties"]), {"bestiary"})
        self.assertIn("$.bestiary[0].name is required", repair["contents"])
        self.assertIn("A misty forest", repair["contents"])
        for unrelated in ("PRIVATE_CHARACTER_SENTINEL", "UNRELATED_ITEM_SENTINEL", "UNRELATED_ITEM_RULE", "FULL_PHASE_CONTEXT"):
            self.assertNotIn(unrelated, repair["contents"])
        self.assertEqual(result, {**self.candidate, **self.good})
        self.assertLess(len(repair["contents"]), len(self.prompt))

    def test_partial_response_cannot_overwrite_valid_siblings(self):
        result, request = self.run_helper([self.candidate, {**self.good, "character": {"biography": "tampered"}}, self.good])
        self.assertEqual(request.call_count, 3)
        self.assertIn("$.character is not allowed", request.call_args.kwargs["contents"])
        self.assertEqual(result["character"], self.candidate["character"])
        self.assertEqual(set(request.call_args.kwargs["config"]["response_json_schema"]["properties"]), {"bestiary"})

    def test_multiple_known_fields_select_union_of_relevant_dependencies(self):
        fields = repair_fields(["$.bestiary[0].name is required", "$.known_crafting_recipes[0].stages[1].work_amount expected at least 1"],
                               {"bestiary": {}, "known_crafting_recipes": {}})
        self.assertEqual(fields, ["bestiary", "known_crafting_recipes"])
        context = repair_context(self.setup, {"world_summary": "Known world", "locations": [{"name": "Forest", "terrain": "Woods"}],
            "character": {"biography": "PRIVATE_CHARACTER_SENTINEL"}}, self.candidate, fields, [], 1)
        self.assertIn("starting_items", context["related_state"])
        self.assertNotIn("character", context["setup"])
        self.assertNotIn("character", context["related_state"])

    def test_malformed_json_falls_back_to_full_phase_then_can_narrow(self):
        result, request = self.run_helper(['{"truncated":', self.candidate, self.good])
        self.assertIn("FULL_PHASE_CONTEXT", request.call_args_list[1].kwargs["contents"])
        self.assertNotIn("FULL_PHASE_CONTEXT", request.call_args_list[2].kwargs["contents"])
        self.assertEqual(result["character"], self.candidate["character"])

    def test_unknown_paths_and_root_errors_do_not_guess_a_scope(self):
        for errors in (["$.character.name is required"], ["response was not valid JSON"],
                       ["$ expected object"], ["$.unknown.name is required"],
                       ["$.bestiary[0].name is required", "$.character.name is required"]):
            self.assertIsNone(repair_fields(errors, self.schema["properties"]))

    def test_four_failed_scoped_repairs_exhaust_budget_without_accepting_partial_data(self):
        with patch.object(g, "_generate_content_with_retry", side_effect=[
            SimpleNamespace(text=json.dumps(self.candidate)),
            *[SimpleNamespace(text=json.dumps({"bestiary": [{"creature_id": "wolf"}]})) for _ in range(4)],
        ]) as request:
            with self.assertRaisesRegex(g.GeminiRequestError, "4 repair attempts"):
                g._generate_new_game_response_with_quality_retry(object(), model=g.DEFAULT_GEMINI_MODEL,
                    prompt=self.prompt, response_schema=self.schema, config={"response_json_schema": self.schema},
                    setup_packet=self.setup)
        self.assertEqual(request.call_count, 5)
        self.assertTrue(all("PRIVATE_CHARACTER_SENTINEL" not in call.kwargs["contents"] for call in request.call_args_list[1:]))


if __name__ == "__main__":
    unittest.main()
