import json
from pathlib import Path

from datahouse.state import ManifestRecord, State, load_state, save_state


def test_load_state_missing_returns_empty(tmp_path: Path) -> None:
    state = load_state(tmp_path / "nope.json")
    assert state.schema_version == 1
    assert state.manifests == {}
    assert state.ddl_hash is None


def test_save_then_load_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "s.json"
    state = State(
        schema_version=1,
        table_created_at="2026-05-27T10:00:00Z",
        ddl_hash="sha256:abc",
        manifests={
            "2026-04": ManifestRecord(
                etag='"abc"',
                last_modified="2026-05-15T03:21:00Z",
                last_synced_at="2026-05-15T04:00:00Z",
                row_count=1000,
            )
        },
    )
    save_state(p, state)

    loaded = load_state(p)
    assert loaded == state


def test_save_is_atomic(tmp_path: Path) -> None:
    p = tmp_path / "s.json"
    save_state(p, State(schema_version=1, manifests={}))
    assert not (tmp_path / "s.json.tmp").exists()
    assert p.exists()


def test_saved_file_is_valid_json(tmp_path: Path) -> None:
    p = tmp_path / "s.json"
    state = State(
        schema_version=1,
        manifests={
            "2026-04": ManifestRecord(
                etag="x", last_modified="y", last_synced_at="z", row_count=1
            )
        },
    )
    save_state(p, state)
    parsed = json.loads(p.read_text())
    assert parsed["schema_version"] == 1
    assert parsed["manifests"]["2026-04"]["row_count"] == 1
