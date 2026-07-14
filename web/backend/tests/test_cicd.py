from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


class CicdSafetyTests(unittest.TestCase):
    def test_self_hosted_deploy_is_gated_to_main(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "pipeline.yml").read_text()

        self.assertEqual(workflow.count("runs-on: [self-hosted, Linux, ARM64, jetson]"), 1)
        self.assertIn("github.event_name != 'pull_request'", workflow)
        self.assertIn("github.ref == 'refs/heads/main'", workflow)
        self.assertIn("vars.JETSON_CD_ENABLED == 'true'", workflow)

    def test_deploy_checks_idle_before_copying_files(self) -> None:
        script = (ROOT / "web" / "deploy" / "deploy_from_ci.sh").read_text()

        self.assertLess(script.index("ensure_ohcar_deploy_idle"), script.index("rsync -a"))

    def test_runtime_install_uses_fixed_passwordless_helper(self) -> None:
        script = (ROOT / "web" / "deploy" / "install_on_jetson.sh").read_text()

        self.assertIn("sudo -n /usr/local/sbin/ohcar-web-restart", script)
        self.assertIn(
            "NOPASSWD: /usr/local/sbin/ohcar-web-restart",
            script,
        )


if __name__ == "__main__":
    unittest.main()
