import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pacific_gym.env import load_dotenv


class DotenvTest(unittest.TestCase):
    def test_literal_assignments_and_shell_override_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text('BFL_API_KEY="literal-value"\nRAWTREE_API_KEY=other # note\n')
            with patch.dict(os.environ, {"BFL_API_KEY": "shell-value"}, clear=True):
                self.assertEqual(load_dotenv(env_file), env_file.resolve())
                self.assertEqual(os.environ["BFL_API_KEY"], "shell-value")
                self.assertEqual(os.environ["RAWTREE_API_KEY"], "other")

    def test_does_not_execute_shell_or_expand_variables(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            marker = Path(directory) / "executed"
            env_file.write_text(f"SAFE=$(touch {marker})\nLITERAL=$HOME\n")
            with patch.dict(os.environ, {}, clear=True):
                load_dotenv(env_file)
                self.assertEqual(os.environ["SAFE"], f"$(touch {marker})")
                self.assertEqual(os.environ["LITERAL"], "$HOME")
                self.assertFalse(marker.exists())

    def test_invalid_assignment_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text("not a valid name=x\n")
            with self.assertRaisesRegex(ValueError, "Invalid dotenv assignment"):
                load_dotenv(env_file)


if __name__ == "__main__":
    unittest.main()
