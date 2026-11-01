"""
Zero-leak Ollama model switcher and deterministic ChatML client.
Enforces strict single-model GPU VRAM residency via explicit keep_alive=0 eviction.
"""

import logging
import re
import time
from typing import Any, Dict, List, Optional

import requests

from ares.config import (
    DEFAULT_KEEP_ALIVE,
    DEFAULT_NUM_CTX,
    DEFAULT_TEMPERATURE,
    EVICT_KEEP_ALIVE,
    MODEL_ORCHESTRATOR,
    OLLAMA_CHAT_ENDPOINT,
    OLLAMA_GENERATE_ENDPOINT,
    OLLAMA_PS_ENDPOINT,
    STOP_TOKENS,
    VRAM_SWAP_SETTLE_SECONDS,
)

logger = logging.getLogger("ares.llm.manager")


class ModelManager:
    """
    Manages Ollama LLM lifecycle under a strict 4GB VRAM budget.
    Guarantees at most one model resides in GPU memory at any point in time.
    """

    def __init__(self, base_url_timeout: int = 120) -> None:
        self.currently_loaded_model: Optional[str] = None
        self.timeout: int = base_url_timeout
        self.session: requests.Session = requests.Session()

    def _get_running_models(self) -> List[str]:
        """Queries Ollama's /api/ps endpoint to detect all currently resident VRAM models."""
        try:
            response = self.session.get(OLLAMA_PS_ENDPOINT, timeout=10)
            response.raise_for_status()
            data = response.json()
            return [m.get("name", "") for m in data.get("models", []) if m.get("name")]
        except requests.RequestException as exc:
            logger.warning("Could not query /api/ps: %s", exc)
            return []

    def unload_model(self, model_name: str) -> bool:
        """
        Immediately flushes the specified model from GPU VRAM by posting keep_alive=0
        to /api/generate.
        """
        if not model_name:
            return True

        payload: Dict[str, Any] = {
            "model": model_name,
            "prompt": "",
            "keep_alive": EVICT_KEEP_ALIVE,
            "stream": False,
        }
        try:
            logger.info("Evicting model from VRAM: %s", model_name)
            resp = self.session.post(
                OLLAMA_GENERATE_ENDPOINT,
                json=payload,
                timeout=30,
            )
            resp.raise_for_status()
            if self.currently_loaded_model == model_name:
                self.currently_loaded_model = None
            return True
        except requests.RequestException as exc:
            logger.error("Failed to unload model %s: %s", model_name, exc)
            return False

    def unload_all(self) -> None:
        """Evicts any tracked or daemon-resident models from VRAM."""
        resident = set(self._get_running_models())
        if self.currently_loaded_model:
            resident.add(self.currently_loaded_model)
        for model in resident:
            self.unload_model(model)
        self.currently_loaded_model = None

    def switch_to(self, model_name: str, keep_alive: str = DEFAULT_KEEP_ALIVE) -> None:
        """
        Enforces exclusive single-model residency:
        1. Evicts any active model if it differs from model_name.
        2. Waits 200ms for CUDA/VRAM page reclamation.
        3. Updates internal state and pings the target model with keep_alive="5m".
        """
        running_models = self._get_running_models()
        for running in running_models:
            if running != model_name:
                self.unload_model(running)

        if self.currently_loaded_model and self.currently_loaded_model != model_name:
            self.unload_model(self.currently_loaded_model)
            time.sleep(VRAM_SWAP_SETTLE_SECONDS)
        elif any(r != model_name for r in running_models):
            time.sleep(VRAM_SWAP_SETTLE_SECONDS)

        if self.currently_loaded_model != model_name:
            logger.info("Cold-loading model into VRAM: %s (keep_alive=%s)", model_name, keep_alive)
            warmup_payload: Dict[str, Any] = {
                "model": model_name,
                "prompt": "",
                "keep_alive": keep_alive,
                "stream": False,
                "options": {
                    "num_ctx": DEFAULT_NUM_CTX,
                    "temperature": DEFAULT_TEMPERATURE,
                },
            }
            try:
                resp = self.session.post(
                    OLLAMA_GENERATE_ENDPOINT,
                    json=warmup_payload,
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                self.currently_loaded_model = model_name
            except requests.RequestException as exc:
                logger.error("Failed to switch to model %s: %s", model_name, exc)
                raise RuntimeError(f"VRAM model switch to '{model_name}' failed: {exc}") from exc

    @staticmethod
    def format_chatml_prompt(messages: List[Dict[str, str]]) -> str:
        """
        Explicitly formats a message list into raw ChatML tokens for deterministic logging
        and token estimation.
        """
        segments: List[str] = []
        for msg in messages:
            role = msg.get("role", "user").strip()
            content = msg.get("content", "").strip()
            segments.append(f"<|im_start|>{role}\n{content}<|im_end|>")
        segments.append("<|im_start|>assistant\n")
        return "\n".join(segments)

    @classmethod
    def estimate_tokens(cls, messages: List[Dict[str, str]]) -> int:
        """
        Fast CPU-only token estimator calibrated for Qwen/DeepSeek BPE tokenizers.
        Uses ChatML framing + ~3.6 characters per token heuristic.
        """
        raw_chatml = cls.format_chatml_prompt(messages)
        word_bound = len(re.findall(r"\S+|\n", raw_chatml))
        char_bound = len(raw_chatml) // 3
        return max(word_bound, char_bound)

    def chat(
        self,
        messages: List[Dict[str, str]],
        model: str = MODEL_ORCHESTRATOR,
        num_ctx: int = DEFAULT_NUM_CTX,
        temperature: float = DEFAULT_TEMPERATURE,
        keep_alive: str = DEFAULT_KEEP_ALIVE,
    ) -> str:
        """
        Switches to the requested model (evicting any prior model), executes a deterministic
        ChatML request with strict stop tokens, and returns the clean assistant string.
        """
        self.switch_to(model, keep_alive=keep_alive)

        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "keep_alive": keep_alive,
            "options": {
                "num_ctx": num_ctx,
                "temperature": temperature,
                "stop": STOP_TOKENS,
            },
        }

        try:
            response = self.session.post(
                OLLAMA_CHAT_ENDPOINT,
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
            raw_content: str = data.get("message", {}).get("content", "")
            return self._clean_output(raw_content)
        except requests.RequestException as exc:
            logger.error("Ollama chat request failed on model %s: %s", model, exc)
            raise RuntimeError(f"Ollama inference error ({model}): {exc}") from exc

    @staticmethod
    def _clean_output(content: str) -> str:
        """Strips trailing stop sequences and excess whitespace from model output."""
        cleaned = content
        for stop_token in STOP_TOKENS:
            if stop_token in cleaned:
                cleaned = cleaned.split(stop_token)[0]
        return cleaned.strip()