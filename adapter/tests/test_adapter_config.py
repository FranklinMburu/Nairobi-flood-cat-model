"""Tests for adapter.config module."""

import json
import tempfile
from pathlib import Path

import pytest

from adapter.config import AdapterConfig, ConfidenceThresholds


class TestConfidenceThresholds:
    def test_default_values(self):
        t = ConfidenceThresholds()
        assert t.accept_at == 0.85
        assert t.refuse_below == 0.50

    def test_custom_values(self):
        t = ConfidenceThresholds(accept_at=0.9, refuse_below=0.6)
        assert t.accept_at == 0.9
        assert t.refuse_below == 0.6

    def test_validation_order(self):
        with pytest.raises(ValueError):
            ConfidenceThresholds(accept_at=0.5, refuse_below=0.8)

    def test_validation_bounds(self):
        with pytest.raises(ValueError):
            ConfidenceThresholds(accept_at=1.5, refuse_below=0.5)
        with pytest.raises(ValueError):
            ConfidenceThresholds(accept_at=0.5, refuse_below=-0.1)


class TestAdapterConfig:
    def test_default_config(self):
        config = AdapterConfig()
        assert config.thresholds.accept_at == 0.85
        assert config.thresholds.refuse_below == 0.50
        assert config.fx_rate_kes_usd == 130.0
        assert config.data_dir == Path("data")
        assert config.max_outside_nairobi_pct == 0.10
        assert config.random_seed == 42

    def test_load_from_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"fx_rate_kes_usd": 129.5}, f)
            config_path = Path(f.name)
        try:
            config = AdapterConfig.load(config_path=config_path)
            assert config.fx_rate_kes_usd == 129.5
        finally:
            config_path.unlink()

    def test_load_with_env_override(self, monkeypatch):
        monkeypatch.setenv("ADAPTER_FX_RATE", "131.0")
        config = AdapterConfig.load()
        assert config.fx_rate_kes_usd == 131.0
        monkeypatch.delenv("ADAPTER_FX_RATE")

    def test_load_with_explicit_override(self):
        config = AdapterConfig.load(fx_rate=128.0)
        assert config.fx_rate_kes_usd == 128.0

    def test_load_with_threshold_overrides(self):
        config = AdapterConfig.load(accept_at=0.9, refuse_below=0.6)
        assert config.thresholds.accept_at == 0.9
        assert config.thresholds.refuse_below == 0.6

    def test_load_with_data_dir_override(self):
        config = AdapterConfig.load(data_dir="/custom/data")
        assert config.data_dir == Path("/custom/data")

    def test_resolve_model_path_absolute(self):
        config = AdapterConfig()
        abs_path = Path(Path.cwd().anchor) / "absolute" / "path" / "model.joblib"  # absolute on every OS
        assert abs_path.is_absolute()
        assert config.resolve_model_path(abs_path) == abs_path

    def test_resolve_model_path_relative(self):
        config = AdapterConfig()
        rel_path = Path("models/column_mapper.joblib")
        resolved = config.resolve_model_path(rel_path)
        assert resolved == Path(__file__).parent.parent / "models" / "column_mapper.joblib"