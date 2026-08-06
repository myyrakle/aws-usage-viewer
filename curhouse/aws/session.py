from __future__ import annotations

import os

import boto3

from curhouse.config import Config


def get_session(config: Config) -> boto3.Session:
    # Explicit env credentials (e.g. inside a container) take precedence over a
    # configured profile. Otherwise a leftover config.toml `profile` that isn't
    # present in the environment raises ProfileNotFound even when valid
    # AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY are supplied.
    if os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY"):
        return boto3.Session(region_name=config.aws.region)
    return boto3.Session(
        profile_name=config.aws.profile or None,
        region_name=config.aws.region,
    )
