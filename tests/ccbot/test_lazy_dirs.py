"""ISS-023: lazy init of cache dirs — no import-time mkdir side effects."""

from __future__ import annotations

import importlib
from pathlib import Path

from ccbot.handlers import document, message_handlers
from ccbot.utils import ensure_dir


def test_ensure_dir_creates_and_returns_path(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "cache"
    result = ensure_dir(target)
    assert result == target
    assert target.is_dir()


def test_ensure_dir_idempotent(tmp_path: Path) -> None:
    target = tmp_path / "cache"
    ensure_dir(target)
    ensure_dir(target)  # second call must not raise
    assert target.is_dir()


def test_handler_imports_do_not_create_dirs(tmp_path: Path, monkeypatch) -> None:
    """Reloading handler modules with a fresh CCBOT_DIR must not mkdir."""
    monkeypatch.setenv("CCBOT_DIR", str(tmp_path))
    try:
        importlib.reload(message_handlers)
        importlib.reload(document)

        assert not (tmp_path / "images").exists()
        assert not (tmp_path / "audio").exists()
        assert not (tmp_path / "documents").exists()

        # Path constants still resolve under the patched CCBOT_DIR
        assert message_handlers.IMAGES_DIR == tmp_path / "images"
        assert message_handlers._AUDIO_DIR == tmp_path / "audio"
        assert document.DOCS_DIR == tmp_path / "documents"
    finally:
        monkeypatch.undo()
        importlib.reload(message_handlers)
        importlib.reload(document)
