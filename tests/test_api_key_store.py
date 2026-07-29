from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from ai_adventure.app.api_key_store import (
    ENCRYPTED_API_KEY_HEADER,
    TERMS_VERSION,
    read_api_key,
    record_terms_acceptance,
    write_api_key,
)


class ApiKeyStoreTests(unittest.TestCase):
    def test_write_and_read_api_key_use_the_requested_local_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            key_path = Path(temp_dir) / "nested" / "gemini_api_key.txt"

            written_path = write_api_key(key_path, "  test-key  ")

            self.assertEqual(written_path, key_path.resolve())
            self.assertEqual(read_api_key(key_path), "test-key")
            stored_value = key_path.read_text(encoding="utf-8")
            if os.name == "nt":
                self.assertTrue(stored_value.startswith(ENCRYPTED_API_KEY_HEADER))
                self.assertNotIn("test-key", stored_value)
            else:
                self.assertEqual(stored_value, f"{ENCRYPTED_API_KEY_HEADER}dGVzdC1rZXk=\n")

    def test_terms_receipt_contains_time_version_and_terms_fingerprint_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            receipt_path = Path(temp_dir) / "gemini_api_key_terms_acceptance.json"
            terms = "Terms revision one"

            written_path = record_terms_acceptance(receipt_path, terms)
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))

            self.assertEqual(written_path, receipt_path.resolve())
            self.assertEqual(receipt["terms_version"], TERMS_VERSION)
            self.assertEqual(receipt["api_key_included"], False)
            self.assertEqual(
                receipt["terms_sha256"],
                hashlib.sha256(terms.encode("utf-8")).hexdigest(),
            )
            self.assertRegex(receipt["accepted_at_utc"], r"Z$")
            self.assertNotIn("test-key", receipt_path.read_text(encoding="utf-8"))

    def test_legacy_plaintext_key_is_migrated_on_read_on_windows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            key_path = Path(temp_dir) / "gemini_api_key.txt"
            key_path.write_text("legacy-key\n", encoding="utf-8")

            self.assertEqual(read_api_key(key_path), "legacy-key")

            stored_value = key_path.read_text(encoding="utf-8")
            if os.name == "nt":
                self.assertTrue(stored_value.startswith(ENCRYPTED_API_KEY_HEADER))
                self.assertNotIn("legacy-key", stored_value)
            else:
                self.assertEqual(stored_value, "legacy-key\n")


if __name__ == "__main__":
    unittest.main()
