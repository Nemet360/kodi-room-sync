#!/usr/bin/env python3
"""Build a deterministic Kodi add-on repository."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree


FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
IGNORED_NAMES = {".DS_Store", "Thumbs.db"}
IGNORED_SUFFIXES = {".pyc", ".pyo"}
IGNORED_DIRECTORIES = {"__pycache__", "tests"}
METADATA_FILES = {"addon.xml", "changelog.txt", "icon.png", "fanart.jpg", "fanart.png"}


def _is_packaged_file(path: Path) -> bool:
    return (
        path.is_file()
        and path.name not in IGNORED_NAMES
        and path.suffix.lower() not in IGNORED_SUFFIXES
        and not IGNORED_DIRECTORIES.intersection(path.parts)
    )


def _addon_details(addon_dir: Path) -> tuple[str, str, ElementTree.Element]:
    manifest = addon_dir / "addon.xml"
    if not manifest.is_file():
        raise ValueError(f"Missing manifest: {manifest}")
    try:
        root = ElementTree.parse(manifest).getroot()
    except ElementTree.ParseError as exc:
        raise ValueError(f"Invalid XML in {manifest}: {exc}") from exc
    if root.tag != "addon":
        raise ValueError(f"Manifest root must be <addon>: {manifest}")
    addon_id = root.get("id", "").strip()
    version = root.get("version", "").strip()
    if not addon_id or not version:
        raise ValueError(f"Manifest needs id and version: {manifest}")
    if addon_dir.name != addon_id:
        raise ValueError(f"Directory {addon_dir.name!r} does not match add-on id {addon_id!r}")
    return addon_id, version, root


def _zip_addon(addon_dir: Path, destination: Path, addon_id: str) -> None:
    with zipfile.ZipFile(
        destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for source in sorted(addon_dir.rglob("*"), key=lambda item: item.as_posix()):
            if not _is_packaged_file(source):
                continue
            relative = source.relative_to(addon_dir)
            member = str(PurePosixPath(addon_id) / PurePosixPath(relative.as_posix()))
            info = zipfile.ZipInfo(member, FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, source.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def _copy_metadata(addon_dir: Path, destination: Path, manifest: ElementTree.Element) -> None:
    candidates = {Path(name) for name in METADATA_FILES if (addon_dir / name).is_file()}
    for asset in manifest.findall("./extension[@point='xbmc.addon.metadata']/assets/*"):
        if asset.text and asset.text.strip():
            candidates.add(Path(asset.text.strip().replace("/", os.sep)))
    for relative in sorted(candidates, key=lambda item: item.as_posix()):
        source = addon_dir / relative
        if not source.resolve().is_relative_to(addon_dir.resolve()) or not source.is_file():
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def _render_index(manifests: list[ElementTree.Element]) -> bytes:
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<addons>"]
    for manifest in manifests:
        ElementTree.indent(manifest, space="    ")
        rendered = ElementTree.tostring(manifest, encoding="unicode", short_empty_elements=True)
        lines.extend(f"    {line}" if line else line for line in rendered.splitlines())
    lines.append("</addons>")
    return ("\n".join(lines) + "\n").encode("utf-8")


def build_repository(root: Path, output: Path) -> list[Path]:
    addon_root = root / "addon"
    addon_dirs = sorted(
        (item for item in addon_root.iterdir() if item.is_dir() and (item / "addon.xml").is_file()),
        key=lambda item: item.name,
    )
    if not addon_dirs:
        raise ValueError(f"No add-ons found under {addon_root}")

    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    manifests: list[ElementTree.Element] = []
    artifacts: list[Path] = []
    try:
        for addon_dir in addon_dirs:
            addon_id, version, manifest = _addon_details(addon_dir)
            target_dir = temporary / addon_id
            target_dir.mkdir(parents=True)
            archive = target_dir / f"{addon_id}-{version}.zip"
            _zip_addon(addon_dir, archive, addon_id)
            _copy_metadata(addon_dir, target_dir, manifest)
            manifests.append(manifest)
            artifacts.append(Path(addon_id) / archive.name)

        index = _render_index(manifests)
        (temporary / "addons.xml").write_bytes(index)
        (temporary / "addons.xml.md5").write_text(hashlib.md5(index).hexdigest(), encoding="ascii")
        (temporary / "addons.xml.sha256").write_text(hashlib.sha256(index).hexdigest(), encoding="ascii")
        artifacts.extend([Path("addons.xml"), Path("addons.xml.md5"), Path("addons.xml.sha256")])

        if output.exists():
            shutil.rmtree(output)
        temporary.replace(output)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return [output / item for item in artifacts]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    default_root = Path(__file__).resolve().parents[1]
    parser.add_argument("--root", type=Path, default=default_root, help="Project root")
    parser.add_argument("--output", type=Path, help="Output directory (default: ROOT/repository)")
    args = parser.parse_args()
    root = args.root.resolve()
    output = (args.output or root / "repository").resolve()
    artifacts = build_repository(root, output)
    for artifact in artifacts:
        print(artifact.relative_to(root) if artifact.is_relative_to(root) else artifact)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
