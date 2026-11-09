"""Teste pentru sugestiile Gemini, cu API-ul extern simulat."""

from __future__ import annotations

import json
import threading
import unittest
from functools import partial
from http.server import ThreadingHTTPServer
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from . import server_site


class TestGeminiSuggestions(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        handler = partial(server_site.Handler, directory=str(server_site.server_baza.WEB_DIR))
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}/sugestii-ai"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def _post(self, payload: dict) -> tuple[int, dict]:
        request = Request(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            try:
                return error.code, json.loads(error.read())
            finally:
                error.close()

    def test_sends_overall_and_criterion_scores_without_image(self) -> None:
        api_response = {
            "candidates": [{
                "content": {
                    "parts": [{"text": "1. Crește contrastul.\n2. Simplifică textul."}],
                },
            }],
        }
        with (
            patch.dict("os.environ", {"GEMINI_API_KEY": "test-gemini-key"}),
            patch.object(server_site, "urlopen", return_value=BytesIO(json.dumps(api_response).encode())) as mocked,
        ):
            status, result = self._post({
                "overallScore": 61,
                "criteria": [
                    {"name": "Contrast", "score": 42, "weight": 20, "reason": "Textul se distinge greu."},
                    {"name": "Lizibilitate", "score": 80, "weight": 30, "reason": "Bună."},
                ],
                "recommendations": ["Crește contrastul local."],
            })

        self.assertEqual(status, 200, result)
        self.assertEqual(result["model"], server_site.MODEL_GEMINI)
        self.assertEqual(result["provider"], "Gemini API")
        self.assertEqual(result["suggestions"], api_response["candidates"][0]["content"]["parts"][0]["text"])
        api_request = mocked.call_args.args[0]
        self.assertIsInstance(api_request, Request)
        self.assertEqual(api_request.get_header("X-goog-api-key"), "test-gemini-key")
        self.assertEqual(
            api_request.full_url,
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent",
        )
        sent = json.loads(api_request.data)
        prompt = sent["contents"][0]["parts"][0]["text"]
        self.assertIn("Contrast", prompt)
        self.assertIn("61", prompt)
        self.assertIn("80", prompt)
        self.assertIn("Crește contrastul local", prompt)
        self.assertNotIn("image", sent)
        self.assertEqual(sent["generationConfig"]["maxOutputTokens"], 500)

    def test_missing_key_returns_explicit_error(self) -> None:
        with patch.object(server_site, "_cheie_gemini", return_value=""):
            status, result = self._post({"overallScore": 80, "criteria": [], "recommendations": []})
        self.assertEqual(status, 503)
        self.assertIn("GEMINI_API_KEY", result["error"])

    def test_rejects_invalid_scores_before_calling_provider(self) -> None:
        with patch.dict("os.environ", {"GEMINI_API_KEY": "test-gemini-key"}):
            status, result = self._post({
                "overallScore": 101,
                "criteria": [],
                "recommendations": [],
            })
        self.assertEqual(status, 400)
        self.assertIn("date invalide", result["error"])

    def test_explains_rate_limit(self) -> None:
        rate_limit = HTTPError(self.url, 429, "Too Many Requests", {}, BytesIO(b"{}"))
        with (
            patch.dict("os.environ", {"GEMINI_API_KEY": "test-gemini-key"}),
            patch.object(server_site, "urlopen", side_effect=rate_limit),
        ):
            status, result = self._post({
                "overallScore": 80,
                "criteria": [],
                "recommendations": [],
            })
        self.assertEqual(status, 502)
        self.assertIn("Gemini a limitat cererea (HTTP 429)", result["error"])
        self.assertIn("gemini-3.1-flash-lite", result["error"])

    def test_explains_payment_required(self) -> None:
        payment_required = HTTPError(self.url, 402, "Payment Required", {}, BytesIO(b"{}"))
        with (
            patch.dict("os.environ", {"GEMINI_API_KEY": "test-gemini-key"}),
            patch.object(server_site, "urlopen", side_effect=payment_required),
        ):
            status, result = self._post({
                "overallScore": 80,
                "criteria": [],
                "recommendations": [],
            })
        self.assertEqual(status, 502)
        self.assertIn("HTTP 402", result["error"])
        self.assertIn("Gemini", result["error"])

    def test_explains_invalid_key(self) -> None:
        for code in (401, 403):
            with self.subTest(status=code):
                rejected = HTTPError(self.url, code, "Unauthorized", {}, BytesIO(b"{}"))
                with (
                    patch.dict("os.environ", {"GEMINI_API_KEY": "test-gemini-key"}),
                    patch.object(server_site, "urlopen", side_effect=rejected),
                ):
                    status, result = self._post({
                        "overallScore": 80,
                        "criteria": [],
                        "recommendations": [],
                    })
                self.assertEqual(status, 502)
                self.assertIn(f"cheia API (HTTP {code})", result["error"])
                self.assertNotIn("test-gemini-key", result["error"])

    def test_reports_bad_request_without_exposing_key(self) -> None:
        rejected = HTTPError(self.url, 400, "Bad Request", {}, BytesIO(b"{}"))
        with (
            patch.dict("os.environ", {"GEMINI_API_KEY": "test-gemini-key"}),
            patch.object(server_site, "urlopen", side_effect=rejected),
        ):
            status, result = self._post({
                "overallScore": 80,
                "criteria": [],
                "recommendations": [],
            })
        self.assertEqual(status, 502)
        self.assertIn("HTTP 400", result["error"])
        self.assertNotIn("test-gemini-key", result["error"])


if __name__ == "__main__":
    unittest.main()
