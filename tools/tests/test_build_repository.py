from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from build_repository import build_repository  # noqa: E402


MANIFEST = """<?xml version="1.0" encoding="UTF-8"?>
<addon id="plugin.video.example" name="Example" version="1.2.3" provider-name="Test">
  <extension point="xbmc.python.pluginsource" library="default.py" />
  <extension point="xbmc.addon.metadata"><platform>all</platform></extension>
</addon>
"""


class BuildRepositoryTests(unittest.TestCase):
    def test_build_is_deterministic_and_zip_has_top_level_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            addon = root / "addon" / "plugin.video.example"
            addon.mkdir(parents=True)
            (addon / "addon.xml").write_text(MANIFEST, encoding="utf-8")
            (addon / "default.py").write_text("print('hello')\n", encoding="utf-8")

            output = root / "repository"
            build_repository(root, output)
            first = {
                path.relative_to(output).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in output.rglob("*")
                if path.is_file()
            }
            build_repository(root, output)
            second = {
                path.relative_to(output).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in output.rglob("*")
                if path.is_file()
            }
            self.assertEqual(first, second)

            archive = output / "plugin.video.example" / "plugin.video.example-1.2.3.zip"
            with zipfile.ZipFile(archive) as package:
                self.assertEqual(
                    package.namelist(),
                    ["plugin.video.example/addon.xml", "plugin.video.example/default.py"],
                )
                self.assertTrue(all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in package.infolist()))

            index = (output / "addons.xml").read_bytes()
            checksum = (output / "addons.xml.md5").read_text(encoding="ascii")
            self.assertEqual(checksum, hashlib.md5(index).hexdigest())
            self.assertIn(b'id="plugin.video.example"', index)


if __name__ == "__main__":
    unittest.main()
