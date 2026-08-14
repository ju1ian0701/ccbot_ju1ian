"""Voice message transcription — converts audio to text.

Supports two backends (configured via CCBOT_WHISPER_BACKEND):
  - "local"  — faster-whisper on CPU (free, no API key, requires ffmpeg)
  - "openai" — OpenAI API (gpt-4o-transcribe, requires OPENAI_API_KEY)
  - "off"    — voice messages disabled

Key function: transcribe() — async, returns transcribed text.
"""

import asyncio
import logging
from pathlib import Path
from typing import Any

import httpx

from .config import config

logger = logging.getLogger(__name__)


class TranscriptionError(Exception):
    """Raised when transcription fails."""


class TranscriptionDisabled(Exception):
    """Raised when voice transcription is disabled or unavailable."""


# --- OpenAI backend ---

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """Return a lazily-initialized httpx client singleton."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=30.0)
    return _client


async def _transcribe_openai(file_path: Path) -> str:
    """Transcribe via OpenAI API (gpt-4o-transcribe)."""
    if not config.openai_api_key:
        raise TranscriptionDisabled(
            "Voice transcription requires OPENAI_API_KEY in .env.\n"
            "Or set CCBOT_WHISPER_BACKEND=local for free local transcription "
            '(requires: uv pip install -e ".[voice]").'
        )
    url = f"{config.openai_base_url.rstrip('/')}/audio/transcriptions"
    client = _get_client()
    ogg_data = file_path.read_bytes()
    post_data: dict[str, str] = {"model": "gpt-4o-transcribe"}
    if config.whisper_language != "auto":
        post_data["language"] = config.whisper_language
    response = await client.post(
        url,
        headers={"Authorization": f"Bearer {config.openai_api_key}"},
        files={"file": ("voice.ogg", ogg_data, "audio/ogg")},
        data=post_data,
    )
    response.raise_for_status()
    text = response.json().get("text", "").strip()
    if not text:
        raise TranscriptionError("Empty transcription returned by OpenAI API")
    return text


async def close_client() -> None:
    """Close the httpx client (call on shutdown)."""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
        _client = None


# --- Local (faster-whisper) backend ---

_local_model: Any = None


def _get_local_model() -> Any:
    """Lazy-load the faster-whisper model (downloads on first use)."""
    global _local_model
    if _local_model is not None:
        return _local_model
    try:
        # faster-whisper has no stubs; keep import-untyped rather than fail pyright.
        from faster_whisper import WhisperModel  # type: ignore[import-untyped]
    except ImportError:
        raise TranscriptionDisabled(
            "faster-whisper is not installed. "
            'Install with: uv pip install -e ".[voice]"\n'
            "Or set CCBOT_WHISPER_BACKEND=openai/off."
        )
    logger.info(
        "Loading faster-whisper model '%s' (may download on first use)...",
        config.whisper_model,
    )
    _local_model = WhisperModel(config.whisper_model, device="cpu", compute_type="int8")
    logger.info("faster-whisper model loaded successfully")
    return _local_model


def _transcribe_local_sync(file_path: Path) -> str:
    """Synchronous transcription using faster-whisper (CPU-bound)."""
    model = _get_local_model()
    language = None if config.whisper_language == "auto" else config.whisper_language
    segments, info = model.transcribe(str(file_path), beam_size=5, language=language)
    text = " ".join(segment.text.strip() for segment in segments)
    if not text.strip():
        raise TranscriptionError("Transcription produced empty text")
    logger.info(
        "Transcribed %s: language=%s, duration=%.1fs, text_len=%d",
        file_path.name,
        info.language,
        info.duration,
        len(text),
    )
    return text.strip()


# --- Unified entry point ---


async def transcribe(file_path: Path) -> str:
    """Transcribe an audio file to text using the configured backend.

    Args:
        file_path: Path to the audio file (OGG/Opus from Telegram).

    Returns:
        Transcribed text string.

    Raises:
        TranscriptionDisabled: Voice is disabled or dependency missing.
        TranscriptionError: Transcription failed or produced empty text.
    """
    if config.whisper_backend == "off":
        raise TranscriptionDisabled("Voice transcription is disabled.")

    if config.whisper_backend == "openai":
        return await _transcribe_openai(file_path)

    # local backend
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _transcribe_local_sync, file_path)
