from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ManifestRecord:
    etag: str
    last_modified: str
    last_synced_at: str
    row_count: int


@dataclass(frozen=True)
class State:
    schema_version: int = 1
    table_created_at: str | None = None
    ddl_hash: str | None = None
    manifests: dict[str, ManifestRecord] = field(default_factory=dict)


def load_state(path: Path | str) -> State:
    path = Path(path)
    if not path.exists():
        return State()

    data = json.loads(path.read_text())
    manifests = {
        period: ManifestRecord(**record)
        for period, record in data.get("manifests", {}).items()
    }
    return State(
        schema_version=int(data.get("schema_version", 1)),
        table_created_at=data.get("table_created_at"),
        ddl_hash=data.get("ddl_hash"),
        manifests=manifests,
    )


def save_state(path: Path | str, state: State) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "schema_version": state.schema_version,
        "table_created_at": state.table_created_at,
        "ddl_hash": state.ddl_hash,
        "manifests": {
            period: asdict(rec) for period, rec in state.manifests.items()
        },
    }

    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
    os.replace(tmp, path)
