"""Build an installable archive containing only the Home Assistant integration."""

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

root = Path(__file__).resolve().parent.parent
component = root / "custom_components" / "t3_code"
destination = root / "dist" / "t3_code.zip"
destination.parent.mkdir(exist_ok=True)
with ZipFile(destination, "w", compression=ZIP_DEFLATED) as archive:
    for path in sorted(component.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
            archive.write(path, path.relative_to(component.parent))
    archive.write(root / "LICENSE", "t3_code/LICENSE")
with ZipFile(destination) as archive:
    if archive.testzip() is not None:
        raise AssertionError()
    if "t3_code/manifest.json" not in archive.namelist():
        raise AssertionError()
print(destination)
