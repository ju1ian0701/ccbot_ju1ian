"""Tests for terminal_parser — Claude Code /login screens (method picker, sign-in URL, code prompt)."""

from pathlib import Path

import pytest

from ccbot.terminal_parser import (
    extract_interactive_content,
    is_interactive_ui,
    is_login_code_prompt,
    parse_login_url,
)

FIXTURES = Path(__file__).parent / "fixtures" / "panes" / "2.1.311"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


LOGIN_METHOD_FIXTURES = ["login_method_w110.txt", "login_method_w80.txt"]
LOGIN_URL_FIXTURES = ["login_url_w110.txt", "login_url_w80.txt"]


# ── LoginMethod detection ────────────────────────────────────────────────


class TestLoginMethodScreen:
    @pytest.mark.parametrize("name", LOGIN_METHOD_FIXTURES)
    def test_extracts_login_method(self, name: str):
        result = extract_interactive_content(_load(name))
        assert result is not None
        assert result.name == "LoginMethod"
        assert "Select login method:" in result.content
        assert "1. Claude account with subscription" in result.content
        assert "3. 3rd-party platform" in result.content

    @pytest.mark.parametrize("name", LOGIN_METHOD_FIXTURES)
    def test_is_interactive(self, name: str):
        assert is_interactive_ui(_load(name)) is True

    @pytest.mark.parametrize("name", LOGIN_METHOD_FIXTURES)
    def test_not_code_prompt_and_no_url(self, name: str):
        pane = _load(name)
        assert is_login_code_prompt(pane) is False
        assert parse_login_url(pane) is None


# ── Sign-in URL parsing ──────────────────────────────────────────────────


class TestLoginUrl:
    @pytest.mark.parametrize("name", LOGIN_URL_FIXTURES)
    def test_parses_wrapped_url(self, name: str):
        url = parse_login_url(_load(name))
        assert url is not None
        assert url.startswith("https://claude.com/cai/oauth/authorize?")
        assert not any(c.isspace() for c in url)
        assert "code_challenge_method=S256" in url
        _, sep, state_value = url.rpartition("state=")
        assert sep == "state="
        assert state_value
        assert set(state_value) == {"S"}

    def test_wrap_width_does_not_matter(self):
        w110 = parse_login_url(_load("login_url_w110.txt"))
        w80 = parse_login_url(_load("login_url_w80.txt"))
        assert w110 is not None
        assert w110 == w80

    @pytest.mark.parametrize("name", LOGIN_URL_FIXTURES)
    def test_code_prompt_detected(self, name: str):
        assert is_login_code_prompt(_load(name)) is True

    def test_rejects_foreign_host(self):
        pane = (
            " Browser didn't open? Use the url below to sign in (c to copy)\n"
            "\n"
            "https://evil.example.com/x?y=1\n"
            "\n"
            " Hold Shift\n"
        )
        assert parse_login_url(pane) is None

    def test_rejects_non_https(self):
        pane = (
            " Browser didn't open? Use the url below to sign in (c to copy)\n"
            "\n"
            "http://claude.com/cai/oauth/authorize?x=1\n"
            "\n"
        )
        assert parse_login_url(pane) is None

    def test_empty_input(self):
        assert parse_login_url("") is None

    def test_marker_missing(self):
        pane = "https://claude.com/cai/oauth/authorize?x=1\n"
        assert parse_login_url(pane) is None

    @pytest.mark.parametrize(
        "pane",
        [
            pytest.param(
                " Browser didn't open? Use the url below to sign in (c to copy)\n\n\n",
                id="only_blank_lines",
            ),
            pytest.param(
                " Browser didn't open? Use the url below to sign in (c to copy)\n"
                "\n"
                " Hold Shift (Option in iTerm2) while selecting\n",
                id="prose_only",
            ),
        ],
    )
    def test_nothing_collected(self, pane: str):
        assert parse_login_url(pane) is None


# ── Login screens do not disturb existing detection ──────────────────────


class TestLoginUrlScreenNoConflict:
    @pytest.mark.parametrize("name", LOGIN_URL_FIXTURES)
    def test_no_existing_pattern_matches(self, name: str):
        result = extract_interactive_content(_load(name))
        if result is not None:
            assert result.name not in {
                "PermissionPrompt",
                "AskUserQuestion",
                "ExitPlanMode",
            }


class TestRegressionOrdinaryPrompt:
    def test_plain_prompt_not_interactive(self, sample_pane_no_ui: str):
        assert extract_interactive_content(sample_pane_no_ui) is None
