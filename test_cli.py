from contextlib import redirect_stdout, redirect_stderr
import io
from pathlib import Path
import tempfile
import unittest
from cli import main

class CliTests(unittest.TestCase):
    def test_status_and_stop_are_offline(self):
        with tempfile.TemporaryDirectory() as directory:
            args = ["--username", "test@example.com", "--directory", str(Path(directory) / "photos"), "--state-dir", str(Path(directory) / "private")]
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(["status", *args]), 0)
            self.assertIn("not_started", output.getvalue())
            self.assertEqual(main(["stop", *args]), 0)
            self.assertTrue((Path(directory) / "private" / "state" / "stop-download").exists())

    def test_checkpoint_cannot_be_shared_between_accounts(self):
        with tempfile.TemporaryDirectory() as directory:
            args = ["--directory", str(Path(directory) / "photos"), "--state-dir", str(Path(directory) / "private")]
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                main(["status", "--username", "first@example.com", *args])
                with self.assertRaises(SystemExit):
                    main(["status", "--username", "second@example.com", *args])

if __name__ == "__main__":
    unittest.main()
