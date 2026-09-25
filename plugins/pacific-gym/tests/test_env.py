import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pacific_gym.env import load_dotenv, bootstrap_worktree_env, host_config


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


class WorktreeEnvTest(unittest.TestCase):
    def test_links_primary_env_and_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            primary = root / "primary"
            checkout = root / "checkout"
            primary.mkdir(); checkout.mkdir()
            source = primary / ".env"
            source.write_text("BFL_API_KEY=test\n")
            with patch("pacific_gym.env.subprocess.check_output", side_effect=[str(checkout), str(primary / ".git")]):
                result = bootstrap_worktree_env(checkout)
            self.assertTrue(result.is_symlink())
            self.assertEqual(result.resolve(), source.resolve())
            result.unlink(); result.write_text("LOCAL=keep\n")
            with patch("pacific_gym.env.subprocess.check_output", side_effect=[str(checkout), str(primary / ".git")]):
                bootstrap_worktree_env(checkout)
            self.assertEqual(result.read_text(), "LOCAL=keep\n")

    def test_host_config_loads_dotenv_without_exposing_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("BFL_API_KEY=secret\nPACIFIC_GYM_MODAL_SANDBOX_ID=sb-test\nPACIFIC_GYM_ISAAC_EXECUTABLE=/isaac/python.sh\n")
            with patch.dict(os.environ, {"PACIFIC_GYM_ENV_FILE": str(path)}, clear=True):
                result = host_config()
            self.assertEqual(result["sandbox_id"], "sb-test")
            self.assertEqual(result["isaac_executable"], "/isaac/python.sh")
            self.assertNotIn("secret", str(result))
            self.assertFalse(result["runtime_verified"])


if __name__ == "__main__":
    unittest.main()
