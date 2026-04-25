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
        provider: str,
        api_key: str | None,
        model: str,
        api_url: str,
        timeout_seconds: float,
        use_system_proxy: bool,
        ca_bundle: str | None,
        temperature: float,
        top_p: float,
        top_k: int,
    ) -> None:
        self.provider = provider
        self.api_key = api_key
        self.model = model
        self.api_url = api_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.use_system_proxy = use_system_proxy
        self.ca_bundle = ca_bundle or (certifi.where() if certifi is not None else None)
        self.temperature = temperature
        self.top_p = top_p
        self.top_k = top_k

    @classmethod
    def from_env(cls) -> "GeminiGemmaClassifier | None":
        provider = os.environ.get("GEMMA_API_PROVIDER", "ollama").strip().lower()
        api_key = os.environ.get("GEMINI_API_KEY")
        if provider == "gemini" and not api_key:
            return None

        return cls(
            provider=provider,
            api_key=api_key,
            model=os.environ.get(
                "GEMMA_MODEL",
                "gemma3:latest" if provider == "ollama" else "gemma-3-27b-it",
            ),
            api_url=os.environ.get(
                "GEMMA_API_URL",
                "http://127.0.0.1:11434/api/generate"
                if provider == "ollama"
                else "https://generativelanguage.googleapis.com/v1beta",
            ),
            timeout_seconds=float(
                os.environ.get(
                    "GEMMA_TIMEOUT_SECONDS",
                    "10" if provider == "ollama" else "3",
                )
            ),
            use_system_proxy=os.environ.get("GEMMA_USE_SYSTEM_PROXY", "false").lower() == "true",
            ca_bundle=os.environ.get("GEMMA_CA_BUNDLE"),
            temperature=float(os.environ.get("GEMMA_TEMPERATURE", "0")),
            top_p=float(os.environ.get("GEMMA_TOP_P", "0")),
            top_k=int(os.environ.get("GEMMA_TOP_K", "1")),
        )

    def __call__(self, payload: dict[str, Any]) -> str:
        return self.classify(payload)

    def classify(self, payload: dict[str, Any]) -> str:
        phase = str(payload.get("phase", "url"))
        prompt = self._build_prompt(payload)
        response_text = self._generate_content(prompt)
        logging.info(
            "Gemma decision for %s: %s",
            payload.get("target_url", ""),
            response_text,
        )
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
                "You are a conservative web-focus classifier for a productivity proxy.\n"
                "Only block a webpage when the content is clearly, specifically, and confidently off-topic for the user's focus.\n"
                "Be globally lenient at the HTML stage. If there is any meaningful ambiguity, choose ALLOW.\n"
                "If there is educational value, research value, documentation value, community-help value, plausible productivity value, or incomplete evidence, choose ALLOW.\n"
                "Generic homepages, feeds, search results, dashboards, category pages, and landing pages should usually be ALLOW unless they are explicitly and strongly off-topic.\n"
                "Do not block based on weak signals, broad platform identity, or the mere presence of entertainment-adjacent words.\n"
                "Only choose BLOCK when a reasonable person would say the actual page is obviously recreational, distracting, or unrelated to the user's stated focus.\n"
                "Do not block a page only because the domain can contain distractions. Judge the actual page content.\n"
                "Examples: a programming subreddit, technical YouTube tutorial, Google Docs page, or research search results should usually be ALLOW.\n"
                "Examples: meme feeds, celebrity gossip, casual entertainment videos, shopping browsing, and clearly recreational scrolling should usually be BLOCK.\n"
                "Reply with exactly one token: ALLOW or BLOCK.\n\n"
                f"Focus summary:\n{focus_summary}\n\n"
                f"Blocked categories:\n{categories_text}\n\n"
                f"URL:\n{target_url}\n\n"
                f"HTML title:\n{title}\n\n"
                f"HTML description:\n{description}\n\n"
                f"Visible page text:\n{text}\n"
            )

        return (
            "You are a conservative web-focus classifier for a productivity proxy.\n"
            "Only block when the URL alone makes it obvious that the destination is specifically off-topic.\n"
            "If the URL could plausibly be productive, educational, research-oriented, documentation-related, or community-helpful, do not block from the URL alone.\n"
            "Broad platforms like reddit.com, youtube.com, google.com, docs.google.com, github.com, and similar multi-purpose domains are usually ambiguous and should return NEEDS_HTML unless the URL path itself is clearly recreational or off-topic.\n"
            "In particular, do not block youtube.com from the domain alone, and do not block generic paths like '/', '/results', '/feed', '/channel/...', or '/@name' from the URL alone.\n"
            "For YouTube, only return BLOCK at the URL stage when the URL itself clearly identifies specifically unproductive content, such as an obviously recreational watch page, shorts page, or entertainment-specific query/path. Otherwise return NEEDS_HTML.\n"
            "For Reddit, only return BLOCK at the URL stage when the subreddit, post slug, or query clearly shows specifically off-topic recreational content. Otherwise return NEEDS_HTML.\n"
            "If the URL alone is clearly enough, reply with exactly one token: ALLOW or BLOCK.\n"
            "If the URL is ambiguous and HTML content inspection is needed, reply with exactly one token: NEEDS_HTML.\n\n"
            f"Focus summary:\n{focus_summary}\n\n"
            f"Blocked categories:\n{categories_text}\n\n"
            f"URL:\n{target_url}\n"
        )

    def _generate_content(self, prompt: str) -> str:
        if self.provider == "ollama":
            return self._generate_ollama_content(prompt)

        return self._generate_gemini_content(prompt)

    def _generate_ollama_content(self, prompt: str) -> str:
        request = urllib.request.Request(
            url=self.api_url,
            data=json.dumps(
                {
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": self.temperature,
                        "top_p": self.top_p,
                        "top_k": self.top_k,
                    },
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            error_body = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Ollama HTTP {error.code}: {error_body}") from error
        except Exception as error:
            raise RuntimeError(f"Ollama request failed: {error}") from error

        try:
            payload = json.loads(body)
            text = str(payload.get("response", "")).strip()
            if not text:
                raise ValueError("Empty response text")
            return text
        except Exception as error:
            logging.exception("Failed to parse Ollama response body")
            raise RuntimeError(f"Ollama response parse failed: {error}") from error

    def _generate_gemini_content(self, prompt: str) -> str:
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY is required for the Gemini provider")

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
                        "temperature": self.temperature,
                        "topP": self.top_p,
                        "topK": self.top_k,
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
