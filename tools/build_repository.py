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
    """Everything in the add-on directory ships to every television, so the
    filter is a deny-list of the things that are not the add-on.

    Dotted directories are excluded as a CLASS rather than by name. Naming them
    one at a time is how `.pytest_cache` reached a built zip: running the test
    suite inside the add-on directory leaves it there, the named ignores did not
    cover it, and five cache files were installed on every television. The next
    tool to leave a dotted directory would do the same.
    """
    parts = path.parts
    return (
        path.is_file()
        and path.name not in IGNORED_NAMES
        and not path.name.startswith(".")
        and path.suffix.lower() not in IGNORED_SUFFIXES
        and not IGNORED_DIRECTORIES.intersection(parts)
        and not any(part.startswith(".") for part in parts)
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


def _write_zip_hashes(archive: Path) -> list[Path]:
    """`<zip>.sha256` and `<zip>.md5` beside every archive.

    The repository manifest declares `<hashes>sha256</hashes>`, which tells Kodi
    to verify each add-on zip it downloads against a hash file sitting next to
    it. Without that file Kodi fetches the zip successfully and then reports
    "installation failed" with no further explanation — measured: the zip
    answered 200 and `plugin.video.roomwatchsync-0.2.0.zip.sha256` answered 404.

    The md5 goes out too: older Kodi builds ask for that one, and the cost of
    both is 100 bytes.
    """
    payload = archive.read_bytes()
    written = []
    for suffix, digest in (("sha256", hashlib.sha256), ("md5", hashlib.md5)):
        target = archive.with_name(archive.name + "." + suffix)
        # The bare digest with no filename: that is the form Kodi parses.
        target.write_text(digest(payload).hexdigest(), encoding="ascii")
        written.append(target)
    return written


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


INDEX_TEMPLATE = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>Room Watch Sync repository {title}</title></head>
<body>
<h1>Index of {title}</h1>
<ul>
{rows}
</ul>
</body></html>
"""


def _write_one_index(directory: Path, root: Path) -> Path:
    rows = []
    if directory != root:
        rows.append('<li><a href="../">../</a></li>')
    for entry in sorted(directory.iterdir(), key=lambda item: (item.is_file(), item.name)):
        if entry.name == "index.html":
            continue
        # A directory link keeps its trailing slash: Kodi resolves one without
        # it as a file and then fails to open it.
        name = entry.name + ("/" if entry.is_dir() else "")
        rows.append(f'<li><a href="{name}">{name}</a></li>')
    title = "/" if directory == root else "/" + directory.relative_to(root).as_posix()
    target = directory / "index.html"
    target.write_text(INDEX_TEMPLATE.format(title=title, rows="\n".join(rows)),
                      encoding="utf-8")
    return target


def _write_directory_indexes(root: Path) -> list[Path]:
    """An `index.html` of plain links in every directory of the repository.

    GitHub Pages serves no directory listing: a GET on `.../repository/`
    answers **404** — measured. Kodi's "Install from zip file" browses an HTTP
    source by parsing the anchors in the directory's HTML, so without these
    files the owner adds the source, opens it and sees an empty folder with
    nothing to click. That looks like a broken repository and is actually a
    missing index.

    The repository add-on itself never needed this: once installed it reads
    `addons.xml` and fetches the exact zip path out of `datadir`. Only the
    first manual install — the one a person does by hand — goes through
    browsing, which is why every automated check passed and it failed on a
    real television.
    """
    written = [_write_one_index(root, root)]
    for directory in sorted(path for path in root.rglob("*") if path.is_dir()):
        written.append(_write_one_index(directory, root))
    return written


def _is_repository_addon(manifest: ElementTree.Element) -> bool:
    return manifest.find("./extension[@point='xbmc.addon.repository']") is not None


def _publish_repository_zip_at_site_root(site_root: Path, output: Path,
                                         repo_addon_id: str, archive_name: str) -> list[Path]:
    """Copy the repository add-on's own zip (+ hashes) one level up, to the
    site root next to index.html.

    Kodi only ever needs the nested copy under `repository/<id>/` - the
    repository add-on's own `<datadir>` points there, and every update after
    the first install goes through `addons.xml`, never through this file.
    But the FIRST install is a person adding this site as an HTTP source and
    clicking "Install from zip file", and every repo this project was
    compared against (peno64's own: a zip sitting right at
    https://peno64.github.io/repository.peno64/repository.peno64-1.5.zip)
    puts that one zip at the top, not two folders deep. A person who expects
    the top-level convention and finds only an empty-looking root reads it as
    the repository not being there at all.

    Additive only: the nested copy is untouched, so nothing that already
    points at it (addons.xml, the repository add-on's own manifest, anyone
    who already installed from the nested path) changes behaviour.
    """
    source_dir = output / repo_addon_id
    written = []
    for name in (archive_name, archive_name + ".sha256", archive_name + ".md5"):
        source = source_dir / name
        if not source.is_file():
            continue
        target = site_root / name
        target.write_bytes(source.read_bytes())
        written.append(target)
    return written


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
    repository_addon = None  # (addon_id, archive_name), at most one expected
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
            for hash_file in _write_zip_hashes(archive):
                artifacts.append(Path(addon_id) / hash_file.name)
            if _is_repository_addon(manifest):
                repository_addon = (addon_id, archive.name)

        index = _render_index(manifests)
        (temporary / "addons.xml").write_bytes(index)
        (temporary / "addons.xml.md5").write_text(hashlib.md5(index).hexdigest(), encoding="ascii")
        (temporary / "addons.xml.sha256").write_text(hashlib.sha256(index).hexdigest(), encoding="ascii")
        artifacts.extend([Path("addons.xml"), Path("addons.xml.md5"), Path("addons.xml.sha256")])

        if output.exists():
            shutil.rmtree(output)
        temporary.replace(output)
        for page in _write_directory_indexes(output):
            artifacts.append(page.relative_to(output))
        if repository_addon is not None:
            addon_id, archive_name = repository_addon
            for published in _publish_repository_zip_at_site_root(
                    root, output, addon_id, archive_name):
                artifacts.append(published)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    # `artifacts` mixes paths relative to `output` with the (already absolute)
    # site-root copies appended above; an absolute right-hand side makes `/`
    # return that operand unchanged, so this resolves both correctly - made
    # explicit here rather than relied on as an implicit pathlib quirk.
    return [item if item.is_absolute() else output / item for item in artifacts]


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
