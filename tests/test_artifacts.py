from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ctfh_core.artifacts import ArtifactStore


class ArtifactStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_publish_file_hashes_the_bytes_it_copies(self) -> None:
        source = self.root / "source.bin"
        payload = (b"alpha-beta-gamma\n" * 4096) + b"tail"
        source.write_bytes(payload)
        store = ArtifactStore(self.root / "store")

        # Regression guard: publish_file must not pre-hash the source and then
        # copy it in a separate pass. The digest must be accumulated over the
        # same chunks written to the temporary CAS object.
        with patch.object(
            ArtifactStore,
            "_hash_file",
            side_effect=AssertionError("publish_file pre-hashed source"),
        ):
            artifact = store.publish_file(source)

        blob = Path(str(artifact["path"]))
        copied = blob.read_bytes()
        expected = hashlib.sha256(copied).hexdigest()

        self.assertEqual(copied, payload)
        self.assertEqual(artifact["sha256"], expected)
        self.assertEqual(artifact["size"], len(copied))
        self.assertEqual(blob.name, expected)


if __name__ == "__main__":
    unittest.main()
