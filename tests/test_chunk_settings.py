import pytest

from incidentops.config import Settings


def test_splitter_units_and_overlap_are_configurable_by_field_name():
    cfg = Settings(_env_file=None, chunk_size_chars=200, chunk_overlap_chars=25)
    assert cfg.chunk_size_chars == 200
    assert cfg.chunk_overlap_chars == 25


def test_legacy_chunk_env_name_is_accepted(tmp_path):
    config = tmp_path / ".env"
    config.write_text("INCIDENTOPS_CHUNK_TOKENS=180\n")
    cfg = Settings(_env_file=config)
    assert cfg.chunk_size_chars == 180


def test_overlap_must_be_less_than_chunk_size():
    with pytest.raises(ValueError, match="overlap"):
        Settings(_env_file=None, chunk_size_chars=100, chunk_overlap_chars=100)
