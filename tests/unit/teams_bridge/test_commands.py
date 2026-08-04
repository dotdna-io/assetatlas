# SPDX-License-Identifier: AGPL-3.0-or-later
from src.teams_bridge.commands import Command, parse_command


def test_slash_new():
    assert parse_command("/new") is Command.NEW
    assert parse_command("  /new  ") is Command.NEW


def test_natural_new_phrasings():
    for s in ["new chat", "Start over", "start a new chat", "NEW CHAT",
              "start new chat"]:
        assert parse_command(s) is Command.NEW, f"expected NEW for {s!r}"


def test_normal_text():
    assert parse_command("show me macs") is Command.NORMAL
    assert parse_command("") is Command.NORMAL
    assert parse_command("new asset") is Command.NORMAL
