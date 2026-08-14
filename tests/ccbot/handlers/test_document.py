"""Tests for document_handler (ISS-021 / upstream PR #89, require_session rewrite).

Pure helpers are tested without stubs. Handler paths use the ISS-004
stub-environment style (MagicMock/AsyncMock + patch of require_*).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.error import BadRequest

from ccbot.handlers.document import (
    _MAX_DOC_BYTES,
    _format_size,
    _safe_filename,
    document_handler,
)
from ccbot.session_guard import SessionContext
from ccbot.tmux_manager import TmuxWindow

_DOC = "ccbot.handlers.document"


def _user(uid: int = 1) -> MagicMock:
    u = MagicMock()
    u.id = uid
    return u


def _session_ctx(
    *,
    user: MagicMock | None = None,
    thread_id: int = 42,
    window_id: str = "@8",
) -> SessionContext:
    user = user or _user()
    win = TmuxWindow(window_id=window_id, window_name="p", cwd="/t")
    return SessionContext(
        user=user, thread_id=thread_id, window_id=window_id, window=win
    )


def _doc_update(
    *,
    file_size: int | None = 100,
    file_name: str | None = "note.pdf",
    file_unique_id: str = "uid1",
    caption: str | None = None,
    chat_type: str = "private",
) -> MagicMock:
    upd = MagicMock()
    user = _user()
    upd.effective_user = user
    msg = MagicMock()
    msg.caption = caption
    msg.chat = MagicMock()
    msg.chat.type = chat_type
    msg.chat.send_action = AsyncMock()
    doc = MagicMock()
    doc.file_size = file_size
    doc.file_name = file_name
    doc.file_unique_id = file_unique_id
    doc.get_file = AsyncMock()
    msg.document = doc
    upd.message = msg
    upd.callback_query = None
    return upd


class TestSafeFilename:
    """Pure _safe_filename: traversal, unicode, empty/dotfile-only."""

    def test_path_traversal_uses_basename(self) -> None:
        assert _safe_filename("../../etc/passwd") == "passwd"
        assert _safe_filename("..\\..\\windows\\system32\\config") == "config"

    def test_special_and_unicode_become_underscore(self) -> None:
        assert _safe_filename("hello world.pdf") == "hello_world.pdf"
        assert _safe_filename("caf\u00e9.pdf") == "caf_.pdf"
        assert _safe_filename("file/name?.txt") == "name_.txt"

    def test_empty_or_dotfile_only_becomes_file(self) -> None:
        assert _safe_filename("") == "file"
        assert _safe_filename(".") == "file"
        assert _safe_filename("..") == "file"
        assert _safe_filename("._") == "file"
        assert _safe_filename("...") == "file"


class TestFormatSize:
    """Pure _format_size unit boundaries (B / KB / MB / GB)."""

    def test_bytes_below_1024(self) -> None:
        assert _format_size(0) == "0.0 B"
        assert _format_size(1023) == "1023.0 B"

    def test_kilobytes_at_1024(self) -> None:
        assert _format_size(1024) == "1.0 KB"

    def test_megabytes_at_1_mib(self) -> None:
        assert _format_size(1024 * 1024) == "1.0 MB"
        assert _format_size(_MAX_DOC_BYTES) == "20.0 MB"

    def test_gigabytes_at_1_gib(self) -> None:
        assert _format_size(1024 * 1024 * 1024) == "1.0 GB"


class TestDocumentHandler:
    """document_handler on ISS-004-style stubs: reject, assemble, fallback, abort."""

    @pytest.mark.asyncio
    async def test_oversize_rejects_without_download(self) -> None:
        upd = _doc_update(file_size=_MAX_DOC_BYTES + 1)
        sctx = _session_ctx()
        with (
            patch(f"{_DOC}.require_user", new_callable=AsyncMock, return_value=_user()),
            patch(f"{_DOC}.require_session", new_callable=AsyncMock, return_value=sctx),
            patch(f"{_DOC}.get_thread_id", return_value=42),
            patch(f"{_DOC}.safe_reply", new_callable=AsyncMock) as reply,
            patch(f"{_DOC}.session_manager") as sm,
        ):
            await document_handler(upd, MagicMock())
        upd.message.document.get_file.assert_not_called()
        sm.send_to_window.assert_not_called()
        reply.assert_awaited_once()
        text = reply.await_args.args[1]
        assert "too large" in text
        assert _format_size(_MAX_DOC_BYTES + 1) in text

    @pytest.mark.asyncio
    async def test_caption_assembles_attached_path(self) -> None:
        upd = _doc_update(caption="please read")
        tg_file = MagicMock()
        tg_file.download_to_drive = AsyncMock()
        upd.message.document.get_file = AsyncMock(return_value=tg_file)
        sctx = _session_ctx()
        with (
            patch(f"{_DOC}.require_user", new_callable=AsyncMock, return_value=_user()),
            patch(f"{_DOC}.require_session", new_callable=AsyncMock, return_value=sctx),
            patch(f"{_DOC}.get_thread_id", return_value=42),
            patch(f"{_DOC}.safe_reply", new_callable=AsyncMock),
            patch(f"{_DOC}.clear_status_msg_info"),
            patch(f"{_DOC}.session_manager") as sm,
        ):
            sm.send_to_window = AsyncMock(return_value=(True, "ok"))
            await document_handler(upd, MagicMock())
        sm.send_to_window.assert_awaited_once()
        sent = sm.send_to_window.await_args.args[1]
        assert sent.startswith("please read\n\n(file attached: ")
        assert sent.endswith(")")

    @pytest.mark.asyncio
    async def test_no_caption_is_attached_path_only(self) -> None:
        upd = _doc_update(caption=None)
        tg_file = MagicMock()
        tg_file.download_to_drive = AsyncMock()
        upd.message.document.get_file = AsyncMock(return_value=tg_file)
        sctx = _session_ctx()
        with (
            patch(f"{_DOC}.require_user", new_callable=AsyncMock, return_value=_user()),
            patch(f"{_DOC}.require_session", new_callable=AsyncMock, return_value=sctx),
            patch(f"{_DOC}.get_thread_id", return_value=42),
            patch(f"{_DOC}.safe_reply", new_callable=AsyncMock),
            patch(f"{_DOC}.clear_status_msg_info"),
            patch(f"{_DOC}.session_manager") as sm,
        ):
            sm.send_to_window = AsyncMock(return_value=(True, "ok"))
            await document_handler(upd, MagicMock())
        sent = sm.send_to_window.await_args.args[1]
        assert sent.startswith("(file attached: ")
        assert "\n\n" not in sent

    @pytest.mark.asyncio
    async def test_badrequest_on_get_file_replies_without_raising(self) -> None:
        upd = _doc_update()
        upd.message.document.get_file = AsyncMock(side_effect=BadRequest("too big"))
        sctx = _session_ctx()
        with (
            patch(f"{_DOC}.require_user", new_callable=AsyncMock, return_value=_user()),
            patch(f"{_DOC}.require_session", new_callable=AsyncMock, return_value=sctx),
            patch(f"{_DOC}.get_thread_id", return_value=42),
            patch(f"{_DOC}.safe_reply", new_callable=AsyncMock) as reply,
            patch(f"{_DOC}.session_manager") as sm,
        ):
            await document_handler(upd, MagicMock())
        sm.send_to_window.assert_not_called()
        reply.assert_awaited_once()
        assert "Could not download" in reply.await_args.args[1]

    @pytest.mark.asyncio
    async def test_require_session_none_is_noop(self) -> None:
        upd = _doc_update()
        with (
            patch(f"{_DOC}.require_user", new_callable=AsyncMock, return_value=_user()),
            patch(f"{_DOC}.require_session", new_callable=AsyncMock, return_value=None),
            patch(f"{_DOC}.get_thread_id", return_value=42),
            patch(f"{_DOC}.safe_reply", new_callable=AsyncMock) as reply,
            patch(f"{_DOC}.session_manager") as sm,
        ):
            await document_handler(upd, MagicMock())
        upd.message.document.get_file.assert_not_called()
        sm.send_to_window.assert_not_called()
        reply.assert_not_awaited()
