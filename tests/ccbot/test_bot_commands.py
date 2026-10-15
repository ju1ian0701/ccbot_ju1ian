"""Menu commands must each have a handler, or they get forwarded to Claude as text."""

from telegram.ext import CommandHandler

from ccbot.bot import BOT_COMMANDS, CC_COMMANDS, create_bot


def _registered_commands() -> set[str]:
    application = create_bot()
    names: set[str] = set()
    for handlers in application.handlers.values():
        for handler in handlers:
            if isinstance(handler, CommandHandler):
                names |= set(handler.commands)
    return names


def test_every_bot_menu_command_has_a_handler() -> None:
    missing = set(BOT_COMMANDS) - _registered_commands()
    assert not missing, f"menu commands without a CommandHandler: {sorted(missing)}"


def test_kill_is_not_advertised() -> None:
    assert "kill" not in BOT_COMMANDS
    assert "kill" not in CC_COMMANDS


def test_bot_and_cc_menu_names_do_not_overlap() -> None:
    assert not set(BOT_COMMANDS) & set(CC_COMMANDS)
