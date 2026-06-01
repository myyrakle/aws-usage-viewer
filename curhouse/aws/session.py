from __future__ import annotations

import boto3

from curhouse.config import Config


def get_session(config: Config) -> boto3.Session:
    return boto3.Session(
        profile_name=config.aws.profile,
        region_name=config.aws.region,
    )
