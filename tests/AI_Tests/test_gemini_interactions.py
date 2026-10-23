"""Exercise the installed SDK against an in-memory HTTP transport, without a key."""

import base64
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import httpx
from google import genai

from ai_adventure.ai import gemini_service as g
from ai_adventure.ai import request_metrics as metrics
from ai_adventure.ai.gemini_client import create_gemini_client
from ai_adventure.visual_assets import GeminiVisualAssetService, VisualAssetRequest


def completed(text='{"answer":1}', **fields):
    return {
        "id": "offline-interaction", "status": "completed",
        "steps": [{"type": "model_output", "content": [{"type": "text", "text": text}]}],
        **fields,
    }


class GeminiInteractionsTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.addCleanup(patch.stopall)
        self.records = patch.object(metrics, "_append_benchmark_record").start()

    def client(self, responses):
        pending = iter(responses)

        def respond(request):
            self.requests.append((request, json.loads(request.content)))
            status, body = next(pending)
            return httpx.Response(status, json=body)

        http = httpx.Client(transport=httpx.MockTransport(respond))
        client = create_gemini_client("offline-test-key", http_options={"httpx_client": http})
        self.addCleanup(client.close)
        return client

    def request(self, client, **kwargs):
        return g._create_interaction_with_retry(
            client, model=g.DEFAULT_GEMINI_MODEL, contents="Authoritative local context",
            config=g._structured_output_config({
                "type": "object", "properties": {"answer": {"type": "integer"}},
                "required": ["answer"], "additionalProperties": False,
            }, apply_response_length=True, ai_preferences={"response_length": "super_brief"}),
            request_label="offline request", **kwargs,
        )

    def test_real_sdk_serializes_stateless_structured_request_and_joins_output(self):
        body = completed()
        body["steps"][0]["content"] = [
            {"type": "text", "text": '{"answer":'}, {"type": "text", "text": "1}"},
        ]
        result = self.request(self.client([(200, body)]))
        self.assertEqual(result.output_text, '{"answer":1}')
        request, sent = self.requests[0]
        self.assertTrue(request.url.path.endswith("/interactions"))
        self.assertEqual(request.headers["Api-Revision"], "2026-05-20")
        self.assertFalse(sent["store"])
        self.assertNotIn("previous_interaction_id", sent)
        self.assertEqual(sent["input"], "Authoritative local context")
        self.assertEqual(sent["generation_config"]["tool_choice"], "none")
        self.assertEqual(sent["generation_config"]["max_output_tokens"], 1536)
        self.assertEqual(sent["response_format"][0]["schema"]["required"], ["answer"])
        self.assertEqual(sent["safety_settings"][0], {"type": "harassment", "threshold": "off"})
        for old in ("contents", "config", "response_json_schema", "response_mime_type", "response_modalities"):
            self.assertNotIn(old, sent)

    def test_actual_sdk_errors_use_only_the_application_retry_budget(self):
        failure = (503, {"error": {"code": "unavailable", "message": "Service unavailable"}})
        for responses, succeeds in (([failure, (200, completed())], True), ([failure, failure], False)):
            with self.subTest(succeeds=succeeds), patch.object(g.time, "sleep") as sleep:
                self.requests.clear()
                client = self.client(responses)
                if succeeds:
                    self.assertEqual(self.request(client).output_text, '{"answer":1}')
                else:
                    with self.assertRaisesRegex(g.GeminiRequestError, "temporarily unavailable"):
                        self.request(client)
                self.assertEqual(len(self.requests), 2)
                sleep.assert_called_once()

    def test_schema_rejection_still_validates_and_repairs_original_contract(self):
        client = self.client([
            (400, {"error": {"code": "invalid_argument", "message": "Unsupported JSON schema"}}),
            (200, completed("{}")), (200, completed()),
        ])
        self.assertEqual(self.request(client).output_text, '{"answer":1}')
        self.assertEqual(len(self.requests), 3)
        self.assertNotIn("schema", self.requests[1][1]["response_format"][0])
        self.assertIn("response schema", self.requests[1][1]["system_instruction"])
        self.assertIn("contract_repair_feedback", self.requests[2][1]["input"])
        self.assertTrue(all(sent["store"] is False for _, sent in self.requests))

    def test_unfinished_interactions_cannot_be_accepted_even_with_valid_json(self):
        for status in ("failed", "cancelled", "incomplete", "requires_action", "queued", "in_progress"):
            with self.subTest(status=status):
                client = self.client([(200, completed(status=status))])
                with self.assertRaisesRegex(g.GeminiRequestError, "did not complete"):
                    self.request(client)

    def test_safety_errors_do_not_trigger_schema_fallback(self):
        client = self.client([(400, {"error": {"code": "safety", "message": "Request blocked"}})])
        with self.assertRaises(g.GeminiRequestError):
            self.request(client)
        self.assertEqual(len(self.requests), 1)

    def test_usage_maps_to_existing_benchmark_fields_without_losing_unknowns(self):
        client = self.client([(200, completed(usage={
            "total_input_tokens": 20, "total_output_tokens": 10,
            "total_thought_tokens": 5, "total_tokens": 35, "total_cached_tokens": 0,
        }))])

        @metrics.measured_operation
        def generate_story_response():
            return self.request(client)

        generate_story_response()
        record = self.records.call_args.args[0]
        attempt = record["attempts"][0]
        self.assertEqual(attempt["prompt_token_count"], 20)
        self.assertEqual(attempt["candidates_token_count"], 10)
        self.assertEqual(attempt["thoughts_token_count"], 5)
        self.assertEqual(attempt["cached_content_token_count"], 0)
        self.assertIsNone(attempt["tool_use_prompt_token_count"])
        self.assertEqual(attempt["api"], "interactions")
        self.assertEqual(attempt["interaction_id"], "offline-interaction")
        self.assertEqual(record["message_type"], "Normal Messages")

    def test_image_request_uses_real_sdk_image_output_and_format_controls(self):
        body = completed()
        body["steps"][0]["content"] = [{
            "type": "image", "mime_type": "image/png",
            "data": base64.b64encode(b"image-bytes").decode(),
        }]
        client = self.client([(200, body)])
        request = VisualAssetRequest(subject_type="location", subject_key="market",
                                     display_name="Market", description="Stone stalls.")
        with patch.object(genai, "Client", return_value=client), patch(
            "ai_adventure.visual_assets.read_api_key", return_value="offline-test-key",
        ):
            data, mime = GeminiVisualAssetService(api_key_path=Path("unused")).generate(request)
        self.assertEqual((data, mime), (b"image-bytes", "image/png"))
        sent = self.requests[0][1]
        self.assertFalse(sent["store"])
        self.assertEqual(sent["response_format"], [{"type": "image", "aspect_ratio": "1:1", "image_size": "1K"}])

    def test_failed_image_response_is_rejected_and_records_reported_usage(self):
        client = self.client([(200, completed(status="incomplete", usage={"total_input_tokens": 12}))])
        request = VisualAssetRequest(subject_type="location", subject_key="market",
                                     display_name="Market", description="Stone stalls.")
        with patch.object(genai, "Client", return_value=client), patch(
            "ai_adventure.visual_assets.read_api_key", return_value="offline-test-key",
        ), self.assertRaises(RuntimeError):
            GeminiVisualAssetService(api_key_path=Path("unused")).generate(request)
        record = self.records.call_args.args[0]
        self.assertFalse(record["succeeded"])
        self.assertFalse(record["attempts"][0]["succeeded"])
        self.assertEqual(record["attempts"][0]["prompt_token_count"], 12)

    def test_malformed_image_is_rejected_and_accounted_once(self):
        body = completed()
        body["steps"][0]["content"] = [{"type": "image", "mime_type": "image/png", "data": "invalid!"}]
        client = self.client([(200, body)])
        request = VisualAssetRequest(subject_type="location", subject_key="market",
                                     display_name="Market", description="Stone stalls.")
        with patch.object(genai, "Client", return_value=client), patch(
            "ai_adventure.visual_assets.read_api_key", return_value="offline-test-key",
        ), self.assertRaises(RuntimeError):
            GeminiVisualAssetService(api_key_path=Path("unused")).generate(request)
        record = self.records.call_args.args[0]
        self.assertFalse(record["succeeded"])
        self.assertEqual(record["request_count"], 1)
        self.assertFalse(record["attempts"][0]["cost_complete"])


if __name__ == "__main__":
    unittest.main()
