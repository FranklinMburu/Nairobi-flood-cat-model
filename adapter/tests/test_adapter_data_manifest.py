"""Tests for adapter data_manifest.json validation."""

import json
import hashlib
from pathlib import Path

import pytest


class TestDataManifest:
    def test_manifest_exists(self):
        manifest_path = Path("adapter/data_manifest.json")
        assert manifest_path.exists()

    def test_manifest_valid_json(self):
        manifest_path = Path("adapter/data_manifest.json")
        with manifest_path.open() as f:
            manifest = json.load(f)
        assert isinstance(manifest, dict)
        assert len(manifest) == 6  # 1 CSV + 5 TIFFs

    def test_manifest_has_required_fields(self):
        manifest_path = Path("adapter/data_manifest.json")
        with manifest_path.open() as f:
            manifest = json.load(f)

        for filename, info in manifest.items():
            assert "path" in info
            assert "sha256" in info
            assert "description" in info
            assert len(info["sha256"]) == 64  # SHA-256 hex length

    def test_manifest_hashes_match_files(self):
        manifest_path = Path("adapter/data_manifest.json")
        with manifest_path.open() as f:
            manifest = json.load(f)

        for filename, info in manifest.items():
            file_path = Path(info["path"])
            assert file_path.exists(), f"File not found: {file_path}"
            actual_sha256 = hashlib.sha256(file_path.read_bytes()).hexdigest()
            assert actual_sha256 == info["sha256"], (
                f"SHA-256 mismatch for {filename}: "
                f"manifest={info['sha256']}, actual={actual_sha256}"
            )

    def test_manifest_matches_provenance(self):
        """Verify manifest hashes match those recorded in PROVENANCE.md."""
        # Expected hashes from PROVENANCE.md
        expected = {
            "exposure_nairobi_with_hazard.csv": "b60aa96590d5a2e71509579d50c18d4ebd3557e26d2c60505f89d20bee69aa48",
            "nairobi_pluvial_proxy_extreme.tif": "dc83cf80780c7d83c0da803f3eb1bfeb6a7bcea8b9debe76d438a864a6466728",
            "nairobi_pluvial_proxy_severe.tif": "92d6d5ccc2930fcc40edb69d5ff3304a7ce60028586779075666845e01910b74",
            "nairobi_pluvial_proxy_moderate.tif": "2e6eba35dbbfec973591084f2979f0fbce24eee6af19365881118796c0de22ce",
            "nairobi_pluvial_proxy_occasional.tif": "1a6f0e0d911ad5ccadc5345af514d5b01b43dedd1c1bfef8e4d07c5f0e710cb1",
            "nairobi_pluvial_proxy_common.tif": "2071d44cf0ec9b06d240196e56bcd7915829d53cc6d186b033e89fce5afd1834",
        }

        manifest_path = Path("adapter/data_manifest.json")
        with manifest_path.open() as f:
            manifest = json.load(f)

        for filename, expected_sha256 in expected.items():
            # Find in manifest by path
            found = False
            for info in manifest.values():
                if info["path"].endswith(filename):
                    assert info["sha256"] == expected_sha256, (
                        f"Manifest hash for {filename} doesn't match PROVENANCE.md: "
                        f"manifest={info['sha256']}, provenance={expected_sha256}"
                    )
                    found = True
                    break
            assert found, f"{filename} not found in manifest"