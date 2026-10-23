import copy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from ai_adventure.ai import gemini_service as g
from ai_adventure.ui.workers.gemini import GeminiNewGameWorker
from tests.AI_Tests import test_gemini_service as fixtures


class NewGameRepairTests(unittest.TestCase):
    def setUp(self):
        self.packet = fixtures.GeminiServiceTests._completed_new_game_packet(start_location_mode="exact")
        self.full = fixtures.GeminiServiceTests._complete_new_game_response()
        self.full.update(miscellaneous=[], bestiary=[], opening_cues=[])
        stage = {"stage_id": "mix", "kind": "active", "work_amount": 1, "estimated_minutes": 5,
                 "duration_minutes": 0, "required_tool_item_uuids": [], "required_tool_item_names": [], "label": "Mix"}
        self.full["known_crafting_recipes"] = [{
            "name": "Herbal Poultice", "ingredients": [{"reagent_name": "Leaf", "quantity": 1,
                "measure_amount": 1, "measure_unit": "each"}], "result": "A soothing poultice.",
            "result_item_name": "Herbal Poultice", "skill_name": "Crafting", "stages": [stage, {**stage, "stage_id": "finish"}],
            "required_tool_item_uuids": [], "required_tool_item_names": [], "notes": "Apply to skin.", "value_base_units": 1,
        }]
        self.entities = self.phase("entities_mechanics")
        self.bad = copy.deepcopy(self.entities)
        self.bad["known_crafting_recipes"][0]["stages"][1]["work_amount"] = 0
        self.error = "$.known_crafting_recipes[0].stages[1].work_amount expected at least 1"
        self.schema = g.build_new_game_phase_schema(self.packet, "entities_mechanics", for_api=False)
        self.addCleanup(patch.stopall)
        patch("ai_adventure.ai.request_metrics._append_benchmark_record").start()

    def phase(self, name):
        keys = g.build_new_game_phase_schema(self.packet, name, for_api=False)["properties"]
        return {key: value for key, value in self.full.items() if key in keys}

    def helper(self, responses, *, finalize=None):
        models = SimpleNamespace(create=Mock(side_effect=[
            value if isinstance(value, Exception) else SimpleNamespace(output_text=value if isinstance(value, str) else json.dumps(value))
            for value in responses
        ]))
        transport = g.build_new_game_phase_schema(self.packet, "entities_mechanics")
        text = g._generate_new_game_response_with_quality_retry(
            SimpleNamespace(interactions=models), model=g.DEFAULT_GEMINI_MODEL, prompt="Original authoritative setup",
            response_schema=transport, validation_schema=self.schema, finalize=finalize,
            config={'response_format': [{'type': 'text', 'mime_type': 'application/json', 'schema': transport}], 'generation_config': {'max_output_tokens': 123}}, request_label="new-game entities_mechanics",
        )
        return text, models.create

    def test_logged_minimum_error_succeeds_on_fourth_repair_with_feedback(self):
        self.assertEqual(g._response_contract_errors(json.dumps(self.entities), self.schema), [])
        text, request = self.helper([self.bad] * 4 + [self.entities])
        self.assertEqual(json.loads(text), self.entities)
        self.assertEqual(request.call_count, 5)
        for number, call in enumerate(request.call_args_list[1:], 1):
            prompt = call.kwargs["input"]
            self.assertIn(self.error, prompt)
            self.assertIn(f"Repair attempt {number} of 4", prompt)
            self.assertIn("Original authoritative setup", prompt)
            self.assertNotIn("max_output_tokens", call.kwargs["generation_config"])

    def test_repair_feedback_uses_latest_failure_reason(self):
        wrong_type = copy.deepcopy(self.bad)
        wrong_type["known_crafting_recipes"][0]["stages"][1]["work_amount"] = "zero"
        _, request = self.helper([self.bad, wrong_type, self.entities])
        self.assertIn(self.error, request.call_args_list[1].kwargs["input"])
        self.assertIn("work_amount expected integer", request.call_args_list[2].kwargs["input"])

    def test_targeted_repair_cannot_bypass_final_contract_recovery(self):
        finalize = Mock(side_effect=[json.dumps(self.bad), json.dumps(self.entities)])
        _, request = self.helper([self.entities, self.entities], finalize=finalize)
        self.assertEqual(request.call_count, 2)
        self.assertIn(self.error, request.call_args_list[1].kwargs["input"])
        self.assertEqual(finalize.call_count, 2)

    def test_malformed_json_is_repaired(self):
        _, request = self.helper(['{"unfinished":', self.entities])
        self.assertIn("not valid JSON", request.call_args_list[1].kwargs["input"])

    def test_schema_fallback_still_gets_four_repairs_from_owner(self):
        _, request = self.helper([RuntimeError("400 INVALID_ARGUMENT invalid schema"), *([self.bad] * 4), self.entities])
        self.assertEqual(request.call_count, 6)
        self.assertNotIn("schema", request.call_args_list[1].kwargs["response_format"][0])
        self.assertIn("Repair attempt 4 of 4", request.call_args_list[-1].kwargs["input"])

    def test_transport_failure_reason_is_in_repair_prompt(self):
        with patch.object(g, "_create_interaction_with_retry", side_effect=[
            g.GeminiRequestError("503 service overloaded"), SimpleNamespace(output_text=json.dumps(self.entities)),
        ]) as request:
            g._generate_new_game_response_with_quality_retry(object(), model=g.DEFAULT_GEMINI_MODEL,
                prompt="Setup", response_schema=self.schema, config={})
        self.assertIn("503 service overloaded", request.call_args.kwargs["contents"])

    def test_staged_generation_retries_only_failed_phase_and_records_repairs(self):
        from google import genai
        bad_patch = {"known_crafting_recipes": self.bad["known_crafting_recipes"]}
        good_patch = {"known_crafting_recipes": self.entities["known_crafting_recipes"]}
        responses = [self.phase("world_skeleton"), self.bad, *([bad_patch] * 3), good_patch, self.phase("opening_prose")]
        with patch.object(genai, "Client") as client, self.assertLogs("ai_adventure.ai.request_metrics", level="INFO") as metrics:
            request = client.return_value.interactions.create
            request.side_effect = [SimpleNamespace(output_text=json.dumps(value)) for value in responses]
            result = g.GeminiNarrationService(g.GeminiSettings(api_key="test-key")).generate_new_game_world_staged(self.packet)
        self.assertEqual(request.call_count, 7)
        self.assertIn("Morning light", result.introductory_message)
        self.assertIn('"repair_count": 4', "\n".join(metrics.output))
        self.assertIn("world_summary", request.call_args_list[5].kwargs["input"])
        self.assertIn(self.error, request.call_args_list[5].kwargs["input"])

    def test_worker_reports_unavailable_only_after_all_four_repairs_fail(self):
        from google import genai
        failures = []
        worker = GeminiNewGameWorker(self.packet)
        worker.request_failed.connect(failures.append)
        service = g.GeminiNarrationService(g.GeminiSettings(api_key="test-key"))
        with patch("ai_adventure.ui.workers.gemini.GeminiNarrationService", return_value=service), patch.object(genai, "Client") as client:
            request = client.return_value.interactions.create
            request.side_effect = [SimpleNamespace(output_text=json.dumps(self.phase("world_skeleton")))] + [
                SimpleNamespace(output_text=json.dumps(self.bad)) for _ in range(5)
            ]
            worker.run()
        self.assertEqual(request.call_count, 6)
        self.assertEqual(len(failures), 1)
        self.assertIn("Gemini is temporarily unavailable", failures[0])
        self.assertIn("4 repair attempts", failures[0])


if __name__ == "__main__":
    unittest.main()
