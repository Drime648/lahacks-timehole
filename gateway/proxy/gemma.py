from __future__ import annotations

import json
import logging
import os
import ssl
import urllib.error
import urllib.request
from typing import Any

try:
    import certifi
except ModuleNotFoundError:  # pragma: no cover - fallback for partial dev envs
    certifi = None


def normalize_semantic_decision(value: str, *, allow_needs_html: bool) -> str:
    normalized = value.strip().lower().replace("-", "_")
    if normalized in {"allow", "allowed"}:
        return "allow"
    if normalized in {"block", "blocked"}:
        return "block"
    if allow_needs_html and normalized in {"needs_html", "need_html", "html"}:
        return "needs_html"
    raise ValueError(f"Unsupported Gemma decision: {value!r}")


class GeminiGemmaClassifier:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        api_url: str,
        timeout_seconds: float,
        use_system_proxy: bool,
        ca_bundle: str | None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.api_url = api_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.use_system_proxy = use_system_proxy
        self.ca_bundle = ca_bundle or (certifi.where() if certifi is not None else None)

    @classmethod
    def from_env(cls) -> "GeminiGemmaClassifier | None":
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            return None

        return cls(
            api_key=api_key,
            model=os.environ.get("GEMMA_MODEL", "gemma-3-27b-it"),
            api_url=os.environ.get(
                "GEMMA_API_URL",
                "https://generativelanguage.googleapis.com/v1beta",
            ),
            timeout_seconds=float(os.environ.get("GEMMA_TIMEOUT_SECONDS", "3")),
            use_system_proxy=os.environ.get("GEMMA_USE_SYSTEM_PROXY", "false").lower() == "true",
            ca_bundle=os.environ.get("GEMMA_CA_BUNDLE"),
        )

    def __call__(self, payload: dict[str, Any]) -> str:
        return self.classify(payload)

    def classify(self, payload: dict[str, Any]) -> str:
        phase = str(payload.get("phase", "url"))
        prompt = self._build_prompt(payload)
        response_text = self._generate_content(prompt)
        return normalize_semantic_decision(
            response_text,
            allow_needs_html=phase == "url",
        )

    def _build_prompt(self, payload: dict[str, Any]) -> str:
        blocked_categories = payload.get("blocked_categories", [])
        categories_text = ", ".join(str(category) for category in blocked_categories) or "(none)"
        focus_summary = str(payload.get("focus_summary", "")).strip() or "(empty)"
        target_url = str(payload.get("target_url", "")).strip()
        phase = str(payload.get("phase", "url"))

        if phase == "html":
            title = str(payload.get("title", "")).strip() or "(none)"
            description = str(payload.get("description", "")).strip() or "(none)"
            text = str(payload.get("text", "")).strip() or "(none)"
            return (
                "You are a strict web-focus classifier for a productivity proxy.\n"
                "Decide whether the webpage is aligned with the user's current focus.\n"
                "Reply with exactly one token: ALLOW or BLOCK.\n\n"
                f"Focus summary:\n{focus_summary}\n\n"
                f"Blocked categories:\n{categories_text}\n\n"
                f"URL:\n{target_url}\n\n"
                f"HTML title:\n{title}\n\n"
                f"HTML description:\n{description}\n\n"
                f"Visible page text:\n{text}\n"
            )

        return (
            "You are a strict web-focus classifier for a productivity proxy.\n"
            "Decide whether the URL is aligned with the user's current focus.\n"
            "If the URL alone is clearly enough, reply with exactly one token: ALLOW or BLOCK.\n"
            "If the URL is ambiguous and HTML content inspection is needed, reply with exactly one token: NEEDS_HTML.\n\n"
            f"Focus summary:\n{focus_summary}\n\n"
            f"Blocked categories:\n{categories_text}\n\n"
            f"URL:\n{target_url}\n"
        )

    def _generate_content(self, prompt: str) -> str:
        request = urllib.request.Request(
            url=f"{self.api_url}/models/{self.model}:generateContent",
            data=json.dumps(
                {
                    "contents": [
                        {
                            "role": "user",
                            "parts": [{"text": prompt}],
                        }
                    ],
                    "generationConfig": {
                        "temperature": 0,
                        "topP": 1,
                        "maxOutputTokens": 8,
                    },
                }
            ).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": self.api_key,
            },
            method="POST",
        )

        ssl_context = (
            ssl.create_default_context(cafile=self.ca_bundle)
            if self.ca_bundle
            else ssl.create_default_context()
        )
        if self.use_system_proxy:
            opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl_context))
        else:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({}),
                urllib.request.HTTPSHandler(context=ssl_context),
            )

        try:
            with opener.open(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            error_body = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Gemini API HTTP {error.code}: {error_body}"
            ) from error
        except Exception as error:
            raise RuntimeError(f"Gemini API request failed: {error}") from error

        try:
            payload = json.loads(body)
            candidates = payload.get("candidates", [])
            parts = candidates[0]["content"]["parts"]
            text = "".join(str(part.get("text", "")) for part in parts).strip()
            if not text:
                raise ValueError("Empty response text")
            return text
        except Exception as error:
            logging.exception("Failed to parse Gemini response body")
            raise RuntimeError(f"Gemini API response parse failed: {error}") from error
