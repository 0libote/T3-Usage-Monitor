"""Validate release metadata and build an installable Home Assistant archive."""

import argparse
import json
import tomllib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def build_archive(root: Path, tag: str | None = None) -> Path:
    """Refuse inconsistent versions before creating a release artifact."""
    component = root / "custom_components" / "t3_code"
    manifest = json.loads((component / "manifest.json").read_text())
    project = tomllib.loads((root / "pyproject.toml").read_text())
    hacs = json.loads((root / "hacs.json").read_text())
    version = manifest["version"]
    if version != project["project"]["version"]:
        raise ValueError("Project and integration versions must match")
    if tag is not None and tag != f"v{version}":
        raise ValueError("Release tag must match the integration version")
    minimum = f"homeassistant=={hacs['homeassistant']}"
    if minimum not in project["dependency-groups"]["ha-min"]:
        raise ValueError("HACS minimum must match the minimum-version test environment")
    destination = root / "dist" / "t3_code.zip"
    destination.parent.mkdir(exist_ok=True)
    with ZipFile(destination, "w", compression=ZIP_DEFLATED) as archive:
        for path in sorted(component.rglob("*")):
            if (
                path.is_file()
                and "__pycache__" not in path.parts
                and path.suffix in (".py", ".json")
            ):
                archive.write(path, path.relative_to(component.parent))
        archive.write(root / "LICENSE", "t3_code/LICENSE")
    with ZipFile(destination) as archive:
        required = {
            "t3_code/manifest.json",
            "t3_code/__init__.py",
            "t3_code/diagnostics.py",
            "t3_code/translations/en.json",
            "t3_code/LICENSE",
        }
        if archive.testzip() is not None or not required.issubset(archive.namelist()):
            raise ValueError("Integration archive failed validation")
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", help="Require this v-prefixed release tag to match the version")
    args = parser.parse_args()
    print(build_archive(Path(__file__).resolve().parent.parent, args.tag))
