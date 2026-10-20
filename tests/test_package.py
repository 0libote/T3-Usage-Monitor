"""Release metadata and archives must match what users install."""

import json
import shutil
from pathlib import Path
from zipfile import ZipFile

import pytest

from scripts.package import build_archive


@pytest.fixture
def release_root(tmp_path):
    root = Path(__file__).resolve().parent.parent
    shutil.copytree(root / "custom_components", tmp_path / "custom_components")
    for name in ("LICENSE", "pyproject.toml", "hacs.json"):
        shutil.copy(root / name, tmp_path / name)
    return tmp_path


def test_archive_has_installable_layout_and_only_integration_files(release_root):
    component = release_root / "custom_components" / "t3_code"
    (component / "debug.log").write_text("private-log")
    (component / "__pycache__").mkdir(exist_ok=True)
    (component / "__pycache__" / "cache.pyc").write_bytes(b"cache")
    version = json.loads((component / "manifest.json").read_text())["version"]
    archive_path = build_archive(release_root, f"v{version}")
    with ZipFile(archive_path) as archive:
        names = archive.namelist()
        assert all(name.startswith("t3_code/") for name in names)
        assert not any("__pycache__" in name or name.endswith(".log") for name in names)
        # The documented manual install extracts t3_code into custom_components.
        install = release_root / "install" / "custom_components"
        archive.extractall(install)
        installed = json.loads((install / "t3_code" / "manifest.json").read_text())
        assert installed["domain"] == "t3_code"
        assert installed["version"] == version
        assert (install / "t3_code" / "diagnostics.py").is_file()


@pytest.mark.parametrize("mismatch", ["project", "tag", "minimum"])
def test_inconsistent_release_is_rejected(release_root, mismatch):
    if mismatch == "project":
        path = release_root / "custom_components" / "t3_code" / "manifest.json"
        data = json.loads(path.read_text())
        data["version"] = "99.0.0"
        path.write_text(json.dumps(data))
    elif mismatch == "minimum":
        path = release_root / "hacs.json"
        data = json.loads(path.read_text())
        data["homeassistant"] = "2099.1.0"
        path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        build_archive(release_root, "v99.0.0" if mismatch == "tag" else None)
    assert not (release_root / "dist" / "t3_code.zip").exists()
