"""Unit tests for Config — env var loading, validation, and user access."""

from pathlib import Path

import pytest

from ccbot.config import Config


@pytest.fixture
def _base_env(monkeypatch, tmp_path):
    # chdir to tmp_path so load_dotenv won't find the real .env in repo root
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test:token")
    monkeypatch.setenv("ALLOWED_USERS", "12345")
    monkeypatch.setenv("CCBOT_DIR", str(tmp_path))


@pytest.mark.usefixtures("_base_env")
class TestConfigValid:
    def test_valid_config(self):
        cfg = Config()
        assert cfg.telegram_bot_token == "test:token"
        assert cfg.allowed_users == {12345}

    def test_custom_tmux_session_name(self, monkeypatch):
        monkeypatch.setenv("TMUX_SESSION_NAME", "mysession")
        cfg = Config()
        assert cfg.tmux_session_name == "mysession"

    def test_custom_monitor_poll_interval(self, monkeypatch):
        monkeypatch.setenv("MONITOR_POLL_INTERVAL", "5.0")
        cfg = Config()
        assert cfg.monitor_poll_interval == 5.0

    def test_is_user_allowed_true(self):
        cfg = Config()
        assert cfg.is_user_allowed(12345) is True

    def test_is_user_allowed_false(self):
        cfg = Config()
        assert cfg.is_user_allowed(99999) is False


@pytest.mark.usefixtures("_base_env")
class TestConfigMissingEnv:
    def test_missing_telegram_bot_token(self, monkeypatch):
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN"):
            Config()

    def test_missing_allowed_users(self, monkeypatch):
        monkeypatch.delenv("ALLOWED_USERS", raising=False)
        with pytest.raises(ValueError, match="ALLOWED_USERS"):
            Config()

    def test_non_numeric_allowed_users(self, monkeypatch):
        monkeypatch.setenv("ALLOWED_USERS", "abc")
        with pytest.raises(ValueError, match="non-numeric"):
            Config()


@pytest.mark.usefixtures("_base_env")
class TestConfigClaudeProjectsPath:
    def test_default_claude_projects_path(self, monkeypatch):
        """Default path is ~/.claude/projects when no env vars are set."""
        # Ensure no custom path env vars are set
        monkeypatch.delenv("CCBOT_CLAUDE_PROJECTS_PATH", raising=False)
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
        cfg = Config()
        assert cfg.claude_projects_path == Path.home() / ".claude" / "projects"

    def test_custom_claude_projects_path(self, monkeypatch):
        """CCBOT_CLAUDE_PROJECTS_PATH overrides the default path."""
        custom_path = "/custom/projects/path"
        monkeypatch.setenv("CCBOT_CLAUDE_PROJECTS_PATH", custom_path)
        cfg = Config()
        assert cfg.claude_projects_path == Path(custom_path)

    def test_claude_config_dir_projects_path(self, monkeypatch):
        """CLAUDE_CONFIG_DIR sets path to $CLAUDE_CONFIG_DIR/projects."""
        custom_config_dir = "/custom/claude/config"
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", custom_config_dir)
        cfg = Config()
        assert cfg.claude_projects_path == Path(custom_config_dir) / "projects"

    def test_ccbot_projects_path_takes_priority(self, monkeypatch):
        """CCBOT_CLAUDE_PROJECTS_PATH takes priority over CLAUDE_CONFIG_DIR."""
        monkeypatch.setenv("CCBOT_CLAUDE_PROJECTS_PATH", "/priority/path")
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/lower/priority")
        cfg = Config()
        assert cfg.claude_projects_path == Path("/priority/path")


@pytest.mark.usefixtures("_base_env")
class TestConfigOpenAI:
    def test_openai_defaults(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        cfg = Config()
        assert cfg.openai_api_key == ""
        assert cfg.openai_base_url == "https://api.openai.com/v1"

    def test_openai_api_key(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-123")
        cfg = Config()
        assert cfg.openai_api_key == "sk-test-123"

    def test_openai_base_url(self, monkeypatch):
        monkeypatch.setenv("OPENAI_BASE_URL", "https://proxy.example.com/v1")
        cfg = Config()
        assert cfg.openai_base_url == "https://proxy.example.com/v1"

    def test_openai_api_key_scrubbed_from_env(self, monkeypatch):
        import os

        monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
        Config()
        assert os.environ.get("OPENAI_API_KEY") is None


@pytest.mark.usefixtures("_base_env")
class TestConfigPollingHealth:
    _VARS = (
        "CCBOT_HTTP_POOL_SIZE",
        "CCBOT_HTTP_POOL_TIMEOUT",
        "CCBOT_GET_UPDATES_POOL_SIZE",
        "CCBOT_POLL_STALL_SOFT",
        "CCBOT_POLL_STALL_HARD",
    )

    def test_polling_health_defaults(self, monkeypatch):
        for var in self._VARS:
            monkeypatch.delenv(var, raising=False)
        cfg = Config()
        assert cfg.http_pool_size == 256
        assert cfg.http_pool_timeout == 10.0
        assert cfg.get_updates_pool_size == 4
        assert cfg.poll_stall_soft == 90.0
        assert cfg.poll_stall_hard == 300.0

    def test_polling_health_env_overrides(self, monkeypatch):
        monkeypatch.setenv("CCBOT_HTTP_POOL_SIZE", "128")
        monkeypatch.setenv("CCBOT_HTTP_POOL_TIMEOUT", "3.5")
        monkeypatch.setenv("CCBOT_GET_UPDATES_POOL_SIZE", "2")
        monkeypatch.setenv("CCBOT_POLL_STALL_SOFT", "45")
        monkeypatch.setenv("CCBOT_POLL_STALL_HARD", "600")
        cfg = Config()
        assert cfg.http_pool_size == 128
        assert cfg.http_pool_timeout == 3.5
        assert cfg.get_updates_pool_size == 2
        assert cfg.poll_stall_soft == 45.0
        assert cfg.poll_stall_hard == 600.0


@pytest.mark.usefixtures("_base_env")
class TestConfigGroupChatId:
    def test_unset_is_none(self, monkeypatch):
        monkeypatch.delenv("CCBOT_GROUP_CHAT_ID", raising=False)
        assert Config().group_chat_id is None

    def test_set_is_int(self, monkeypatch):
        monkeypatch.setenv("CCBOT_GROUP_CHAT_ID", "-1003978703836")
        assert Config().group_chat_id == -1003978703836

    def test_garbage_raises(self, monkeypatch):
        monkeypatch.setenv("CCBOT_GROUP_CHAT_ID", "my group")
        with pytest.raises(ValueError, match="CCBOT_GROUP_CHAT_ID must be an integer"):
            Config()
