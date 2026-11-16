from __future__ import annotations

import unittest

from scripts.secret_guard import content_findings, risky_filename


class SecretGuardTests(unittest.TestCase):
    def test_blocks_private_environment_and_key_files(self) -> None:
        self.assertEqual(risky_filename(".env.production"), "environment file")
        self.assertEqual(risky_filename("deploy/private.key"), "key or certificate container")
        self.assertIsNone(risky_filename(".env.example"))

    def test_allows_empty_and_obvious_placeholder_assignments(self) -> None:
        sample = b"DISCORD_" + b"TOKEN=your_bot_token_here\nTOPGG_" + b"TOKEN=\n"
        self.assertEqual(content_findings(sample), [])

    def test_blocks_working_secret_assignments_without_printing_value(self) -> None:
        sample = b"TOPGG_" + b"TOKEN=live-value-that-must-not-be-committed\n"
        findings = content_findings(sample)
        self.assertEqual(findings, ["non-placeholder value assigned to TOPGG_TOKEN"])
        self.assertNotIn("live-value", " ".join(findings))

    def test_blocks_jwt_shaped_values(self) -> None:
        sample = b"ey" + b"J" + b"a" * 20 + b"." + b"b" * 25 + b"." + b"c" * 25
        self.assertIn("JWT-like token", content_findings(sample))


if __name__ == "__main__":
    unittest.main()
