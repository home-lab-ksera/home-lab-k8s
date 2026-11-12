"""Tailscale workflow の生成と GitHub Secrets 登録を、外部への書き込みなしで検証する。"""
import hashlib
import json
import os
import pty
import select
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[3]
SECRETS = ("TS_OAUTH_CLIENT_ID", "TS_AUDIENCE", "HARBOR_USERNAME", "HARBOR_PASSWORD")
MOCK_GH = """#!/usr/bin/env python3
import hashlib, json, os, sys
args = sys.argv[1:]
entry = {"args": args, "debug": os.environ.get("GH_DEBUG")}
if args[:2] == ["secret", "set"]:
    entry["digest"] = hashlib.sha256(sys.stdin.buffer.read()).hexdigest()
with open(os.environ["MOCK_LOG"], "a") as log:
    log.write(json.dumps(entry) + "\\n")
if args[0] == "api":
    if os.environ.get("FAIL_API"):
        sys.exit(1)
    print(os.environ["MOCK_REPO"])
elif args[:2] == ["secret", "set"]:
    if args[2] == os.environ.get("FAIL_SECRET"):
        sys.exit(1)
else:
    sys.exit("unexpected gh invocation")
"""


class OidcWorkflowGenerationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.scripts = self.root / "automation/scripts/github-actions"
        self.scripts.mkdir(parents=True)
        for name in ("add-runner.sh", "add-runners-bulk.sh", "harbor-oidc.yml.tmpl"):
            shutil.copy(ROOT / "automation/scripts/github-actions" / name, self.scripts)
        shutil.copy(ROOT / "automation/scripts/common-logging.sh", self.scripts.parent)
        self.mock_bin = self.root / "bin"
        self.mock_bin.mkdir()
        gh = self.mock_bin / "gh"
        gh.write_text(MOCK_GH)
        gh.chmod(0o755)
        self.env = {k: v for k, v in os.environ.items()
                    if k not in (*SECRETS, "WORKFLOW_DIR", "GITHUB_USERNAME", "LOG_FILE",
                                 "LOGGING_LOG_DIR", "LOG_FORMAT")}
        self.env.update(PATH=f"{self.mock_bin}:{self.env['PATH']}",
                        MOCK_LOG=str(self.root / "gh.jsonl"), GH_DEBUG="api")
        self.metadata = dict(name="Test.Repo", default_branch="main", private=False)
        # shell 展開されそうな文字もそのまま標準入力へ渡ることを確認。
        self.values = {name: f"dummy-{name}-$value-'\"-\\-$(touch BAD)"
                       for name in SECRETS}

    def run_generator(self, *args, private=False, branch="main", secrets=False,
                      script="add-runner.sh", trace=False, **env):
        self.metadata.update(private=private, default_branch=branch)
        run_env = self.env | {"MOCK_REPO": json.dumps(self.metadata)} | env
        if secrets:
            run_env.update(self.values)
        result = subprocess.run(["bash", *(["-x"] if trace else []),
                                 str(self.scripts / script), *args],
                                cwd=self.root, env=run_env, input="",
                                text=True, capture_output=True)
        for value in self.values.values():
            self.assertNotIn(value, result.stdout + result.stderr)
        self.assertFalse((self.root / "BAD").exists())
        return result

    def calls(self):
        path = self.root / "gh.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def workflow_path(self):
        return self.root / ".github/workflows/build-and-push-Test.Repo.yml"

    def check_workflow(self, branch):
        path = self.workflow_path()
        text = path.read_text()
        data = yaml.safe_load(text)
        triggers = data.get("on", data.get(True))
        self.assertEqual(triggers["push"]["branches"], [branch])
        self.assertIn("workflow_dispatch", triggers)
        self.assertEqual(list(data["jobs"]), ["publish"])
        job = data["jobs"]["publish"]
        self.assertEqual(job["uses"], "home-lab-ksera/home-lab-k8s/.github/workflows/publish-harbor.yml@main")
        self.assertEqual(job["permissions"]["id-token"], "write")
        self.assertEqual(job["secrets"], "inherit")
        self.assertEqual(job["with"]["image_name"], "test.repo")
        self.assertNotIn("@@", text)
        lint = subprocess.run(["actionlint", "-shellcheck=", "-pyflakes=", str(path)],
                              text=True, capture_output=True)
        self.assertEqual(lint.returncode, 0, lint.stdout + lint.stderr)

    def test_public_main_and_master_no_secret_writes(self):
        for branch in ("main", "master"):
            with self.subTest(branch=branch):
                result = self.run_generator("Test.Repo", "--force", branch=branch)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.check_workflow(branch)
        self.assertTrue(all(c["args"] == ["api", "repos/home-lab-ksera/Test.Repo"]
                            for c in self.calls()))

    def test_private_registers_exact_values_via_stdin_without_trace(self):
        result = self.run_generator("other/Test.Repo", private=True, branch="master",
                                    secrets=True, trace=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        writes = [c for c in self.calls() if c["args"][0] == "secret"]
        self.assertEqual(len(writes), 4)
        for name, call in zip(SECRETS, writes):
            self.assertEqual(call["args"], ["secret", "set", name, "--repo", "other/Test.Repo"])
            self.assertEqual(call["digest"], hashlib.sha256(self.values[name].encode()).hexdigest())
            self.assertIsNone(call["debug"])
        self.check_workflow("master")

    def test_missing_private_secret_fails_before_any_write(self):
        result = self.run_generator("Test.Repo", "--non-interactive", private=True,
                                    TS_OAUTH_CLIENT_ID="provided")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("TS_AUDIENCE", result.stderr)
        self.assertEqual(len(self.calls()), 1)
        self.assertFalse(self.workflow_path().exists())
        self.assertEqual(list((self.root / ".github/workflows").iterdir()), [])

    def test_private_terminal_input_is_not_echoed(self):
        self.metadata["private"] = True
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        process = subprocess.Popen(
            ["bash", str(self.scripts / "add-runner.sh"), "Test.Repo"],
            cwd=self.root, env=self.env | {"MOCK_REPO": json.dumps(self.metadata)},
            stdin=slave, stdout=slave, stderr=slave)
        os.close(slave)
        output = b""
        try:
            deadline = time.monotonic() + 10
            for name in SECRETS:
                prompt = f"/ {name}: ".encode()
                while prompt not in output:
                    self.assertLess(time.monotonic(), deadline, output.decode())
                    if select.select([master], [], [], 0.1)[0]:
                        output += os.read(master, 65536)
                os.write(master, self.values[name].encode() + b"\n")
            self.assertEqual(process.wait(timeout=10), 0)
            while select.select([master], [], [], 0)[0]:
                try:
                    output += os.read(master, 65536)
                except OSError:
                    break
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
        for value in self.values.values():
            self.assertNotIn(value, output.decode())
        writes = [c for c in self.calls() if c["args"][0] == "secret"]
        self.assertEqual([c["digest"] for c in writes],
                         [hashlib.sha256(self.values[name].encode()).hexdigest() for name in SECRETS])
        self.assertTrue(self.workflow_path().exists())

    def test_secret_failure_does_not_publish_workflow(self):
        result = self.run_generator("Test.Repo", private=True, secrets=True,
                                    FAIL_SECRET="TS_AUDIENCE")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.workflow_path().exists())
        self.assertEqual(len(self.calls()), 3)  # API + 最初の2項目だけ。
        self.assertIn("先に登録済みの値は残ります", result.stderr)

    def test_existing_workflow_preserved_even_when_registration_fails(self):
        self.workflow_path().parent.mkdir(parents=True)
        self.workflow_path().write_text("existing\n")
        result = self.run_generator("Test.Repo", private=True, secrets=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(self.calls()), 1)
        result = self.run_generator("Test.Repo", "--force", private=True, secrets=True,
                                    FAIL_SECRET="TS_AUDIENCE")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.workflow_path().read_text(), "existing\n")

    def test_api_failure_has_no_fallback(self):
        result = self.run_generator("Test.Repo", FAIL_API="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.workflow_path().exists())

    def test_legacy_arguments_and_owner_override(self):
        result = self.run_generator("Test.Repo", "0", "9", "latest", GITHUB_USERNAME="example")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls()[0]["args"], ["api", "repos/example/Test.Repo"])
        self.check_workflow("main")

    def test_invalid_input_rejected_before_github(self):
        for args in (("../bad",), ("repo", "other"), ("repo", "--unknown")):
            result = self.run_generator(*args)
            self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_unsupported_branch_rejected_without_secret_writes(self):
        result = self.run_generator("Test.Repo", private=True, secrets=True, branch="bad'branch")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(self.calls()), 1)
        self.assertFalse(self.workflow_path().exists())

    def test_bulk_forwards_options_and_registers_private_secrets(self):
        (self.root / "automation/settings.toml").write_text(
            '[github]\narc_repositories = [\n'
            '["Test.Repo", 0, 1, "test repo", "latest"],\n]\n')
        for _ in range(2):
            result = self.run_generator("--force", "--non-interactive", private=True, secrets=True,
                                        script="add-runners-bulk.sh")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(sum(c["args"][0] == "secret" for c in self.calls()), 8)
        self.check_workflow("main")


class PublishHarborWorkflowTests(unittest.TestCase):
    """呼び出し側が共有する公開 workflow の安全条件。"""

    def test_publish_steps_are_gated_and_verified(self):
        path = ROOT / ".github/workflows/publish-harbor.yml"
        data = yaml.safe_load(path.read_text())
        triggers = data.get("on", data.get(True))
        self.assertIn("workflow_call", triggers)
        job = data["jobs"]["publish"]
        self.assertEqual(job["permissions"]["id-token"], "write")
        self.assertEqual(job["env"]["PUBLISH"],
                         "${{ github.event_name == 'push' && github.ref == "
                         "format('refs/heads/{0}', github.event.repository.default_branch) }}")
        steps = {step["name"]: step for step in job["steps"]}
        for name in ("Check required secrets are available",
                     "Connect to Tailscale with GitHub OIDC",
                     "Log in to Harbor", "Push and verify versioned image",
                     "Check publication is still the default branch head"):
            self.assertEqual(steps[name]["if"], "env.PUBLISH == 'true'")
        tailscale = steps["Connect to Tailscale with GitHub OIDC"]
        self.assertRegex(tailscale["uses"], r"tailscale/github-action@[a-f0-9]{40}$")
        self.assertEqual(tailscale["with"]["args"], "--accept-dns=false")
        self.assertEqual(tailscale["with"]["tags"], "tag:ci-harbor")
        self.assertIn("head.sha === context.sha",
                      steps["Check publication is still the default branch head"]["with"]["script"])
        self.assertIn("steps.publication.outputs.result == 'true'",
                      steps["Promote current build to latest"]["if"])
        for name in ("Push and verify versioned image", "Promote current build to latest"):
            # containerd image store（.Id = manifest digest）と従来の store（.Id = config digest）の両方で検証する。
            self.assertIn('docker buildx imagetools inspect "$IMAGE"', steps[name]["run"])
            self.assertIn('[[ "$expected" != "$published" && "$expected" != "$config" ]]', steps[name]["run"])
        # 外部入力はコマンドへ直接展開せず、環境変数で渡す。
        self.assertNotIn("${{ inputs.smoke_command }}", steps["Smoke test image"]["run"])
        for step in job["steps"]:
            if "run" in step:
                subprocess.run(["bash", "-n"], input=step["run"], text=True, check=True)
        lint = subprocess.run(["actionlint", "-shellcheck=", "-pyflakes=", str(path)],
                              text=True, capture_output=True)
        self.assertEqual(lint.returncode, 0, lint.stdout + lint.stderr)


if __name__ == "__main__":
    unittest.main()
