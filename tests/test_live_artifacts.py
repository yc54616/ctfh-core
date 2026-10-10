import hashlib
import os
import tempfile
import unittest
from pathlib import Path

from ctfh_core.live_artifacts import verify_accepted_artifacts
from ctfh_core.live_store import LiveStore


class AcceptedArtifactTests(unittest.TestCase):
    def test_accepted_flag_and_writeup_are_private_and_match_the_candidate(self):
        with tempfile.TemporaryDirectory() as temp, LiveStore(Path(temp)) as store:
            store.ensure_contest("https://ctf.example/challenges", None, None)
            workspace = store.workspace("p1")
            workspace.mkdir(parents=True, mode=0o700)
            flag = workspace / "flag.txt"
            writeup = workspace / "writeup.md"
            value = b"CTF{answer}"
            flag.write_bytes(value)
            writeup.write_text("# Solution\n\nReproduce the flaw and submit the flag.\n")
            os.chmod(flag, 0o600)
            os.chmod(writeup, 0o600)
            report = {"submissions": [{"challenge_key": "p1", "verdict": "accepted",
                                       "flag_sha256": hashlib.sha256(value).hexdigest()}]}
            verify_accepted_artifacts(store, report)

            flag.write_bytes(value + b"\n")
            with self.assertRaisesRegex(ValueError, "does not match"):
                verify_accepted_artifacts(store, report)
            flag.write_bytes(value)
            os.chmod(flag, 0o644)
            with self.assertRaisesRegex(ValueError, "private"):
                verify_accepted_artifacts(store, report)
            os.chmod(flag, 0o600)
            writeup.unlink()
            with self.assertRaisesRegex(ValueError, "flag.txt and writeup.md"):
                verify_accepted_artifacts(store, report)


if __name__ == "__main__":
    unittest.main()
