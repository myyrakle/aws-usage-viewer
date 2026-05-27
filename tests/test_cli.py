import pytest

from datahouse.cli import build_parser


def test_setup_subcommand_parses() -> None:
    parser = build_parser()
    args = parser.parse_args(["setup", "--config", "config.toml"])
    assert args.command == "setup"
    assert args.config == "config.toml"


def test_sync_subcommand_with_only_period() -> None:
    parser = build_parser()
    args = parser.parse_args(["sync", "--only-period", "2026-04"])
    assert args.command == "sync"
    assert args.only_period == "2026-04"


def test_sync_verbose_flag() -> None:
    parser = build_parser()
    args = parser.parse_args(["sync", "--verbose"])
    assert args.verbose is True


def test_status_subcommand() -> None:
    parser = build_parser()
    args = parser.parse_args(["status"])
    assert args.command == "status"


def test_init_schema_subcommand() -> None:
    parser = build_parser()
    args = parser.parse_args(["init-schema"])
    assert args.command == "init-schema"


def test_no_command_errors(capsys: pytest.CaptureFixture[str]) -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])
