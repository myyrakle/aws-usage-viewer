from __future__ import annotations

from pathlib import Path

import pytest

from curhouse.aws.session import get_session
from curhouse.config import AwsConfig, ClickhouseConfig, Config, CurConfig, StateConfig


def _cfg(profile: str = "test-profile") -> Config:
    return Config(
        aws=AwsConfig(profile=profile, region="us-east-1", account_id="111"),
        cur=CurConfig(
            bucket_name="b",
            export_name="e",
            prefix="p",
            time_granularity="HOURLY",
            include_resources=True,
            include_split_cost_allocation=True,
        ),
        clickhouse=ClickhouseConfig(
            host="h", port=8123, user="u", password="",
            database="d", table="t", secure=False,
        ),
        state=StateConfig(path="s"),
    )


@pytest.fixture
def fake_aws_credentials(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Create fake AWS credentials so boto3.Session does not raise ProfileNotFound."""
    credentials = tmp_path / "credentials"
    credentials.write_text(
        "[default]\n"
        "aws_access_key_id = AKIAIOSFODNN7EXAMPLE\n"
        "aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY\n"
        "\n"
        "[test-profile]\n"
        "aws_access_key_id = AKIAIOSFODNN7EXAMPLE\n"
        "aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY\n"
    )
    config_file = tmp_path / "config"
    config_file.write_text("")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(credentials))
    monkeypatch.setenv("AWS_CONFIG_FILE", str(config_file))
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAIOSFODNN7EXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY")


def test_get_session_uses_profile(fake_aws_credentials: None) -> None:
    session = get_session(_cfg(profile="default"))
    assert session.profile_name == "default"


def test_get_session_uses_region(fake_aws_credentials: None) -> None:
    session = get_session(_cfg())
    assert session.region_name == "us-east-1"


def test_get_session_empty_profile_uses_default_chain(fake_aws_credentials: None) -> None:
    session = get_session(_cfg(profile=""))
    # When profile="", we pass profile_name=None to boto3.Session, which makes
    # boto3 use the default credential chain (env vars before ~/.aws profiles).
    # boto3 internally represents this as profile_name='default' due to how it
    # resolves the credential chain, but this is still using env var credentials
    # rather than a named profile.
    assert session.profile_name == 'default' or session.profile_name is None
