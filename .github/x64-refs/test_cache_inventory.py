from pathlib import Path
import tempfile
import unittest

from cache_inventory import inventory


class CacheInventoryTests(unittest.TestCase):
    def test_tracks_additions_removals_and_resized_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "retained").write_bytes(b"old")
            (root / "removed").write_bytes(b"remove")
            before = inventory(root)
            (root / "retained").write_bytes(b"new-size")
            (root / "removed").unlink()
            (root / "new").write_bytes(b"new")
            after = inventory(root, before)
            self.assertEqual(after["added_files"], 1)
            self.assertEqual(after["removed_files"], 1)
            self.assertEqual(after["resized_files"], 1)
            self.assertEqual(after["bytes"], 11)
            self.assertNotEqual(after["path_size_manifest_sha256"], before["path_size_manifest_sha256"])

    def test_missing_cache_does_not_look_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                inventory(Path(directory) / "missing")


if __name__ == "__main__":
    unittest.main()
