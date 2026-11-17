"""auto-upgrade.sh の判定を、SSH / curl / upgrade-safe を stub にして検証する。"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]

PACKAGES = """Package: kubeadm
Version: {v}-1.1

Package: kubectl
Version: {v}-1.1

Package: kubelet
Version: {v}-1.1
"""


class AutoUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='auto-upgrade-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.trace = self.root / 'trace'
        self.state = self.root / 'state'
        for command in ('bash', 'dirname', 'date', 'awk', 'sed', 'sort', 'tail', 'head',
                        'tr', 'mkdir', 'rm', 'cat', 'grep', 'flock'):
            source = shutil.which(command)
            self.assertIsNotNone(source, command)
            (self.bin / command).symlink_to(source)
        for path in ('automation/scripts/common-logging.sh',
                     'automation/scripts/settings-loader.sh',
                     'automation/scripts/upgrade/auto-upgrade.sh',
                     'automation/scripts/upgrade/kubernetes-version.sh'):
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / path, target)
        self.fake_script('automation/scripts/run.sh',
                         'echo "run.sh $*" >> "$TRACE"\nexit "${UPGRADE_STATUS:-0}"')
        self.fake_script('automation/scripts/upgrade/upgrade-gate-check.sh',
                         'echo "gate $*" >> "$TRACE"\n'
                         'if [[ "${GATE_STATUS:-0}" != 0 ]]; then echo "argocd/app sync=OutOfSync health=Healthy"; fi\n'
                         'exit "${GATE_STATUS:-0}"')
        # ssh: control-plane の kubectl だけを返す
        self.stub('ssh', '''
cmd="${*: -1}"
echo "ssh $cmd" >> "$TRACE"
if [[ "${SSH_STATUS:-0}" != 0 ]]; then exit "$SSH_STATUS"; fi
case "$cmd" in
  *"get --raw /version"*) printf '{\\n  "major": "1",\\n  "gitVersion": "%s",\\n  "platform": "linux/amd64"\\n}\\n' "$SERVER";;
  *"get nodes"*) printf 'k8s-control-plane %s\\nk8s-worker2 %s\\n' "$CP_KUBELET" "$WORKER_KUBELET";;
  *) exit 97;;
esac''')
        self.stub('curl', '''
echo "curl $*" >> "$TRACE"
case "$*" in
  *pkgs.k8s.io*) printf '%s' "${PACKAGES_BODY:-}";;
  *hooks.slack*) echo "slack $*" >> "$TRACE";;
  *) exit 98;;
esac''')
        self.env = {
            'PATH': str(self.bin), 'HOME': str(self.root), 'TRACE': str(self.trace),
            'K8S_AUTO_UPGRADE_STATE_DIR': str(self.state), 'LC_ALL': 'C',
            'SERVER': 'v1.36.0', 'CP_KUBELET': 'v1.36.0', 'WORKER_KUBELET': 'v1.36.0',
        }
        self.set_desired('v1.36.0')
        self.write_settings()

    def stub(self, name, body):
        path = self.bin / name
        path.write_text('#!/usr/bin/env bash\nset -euo pipefail\n' + body + '\n')
        path.chmod(0o755)

    def fake_script(self, path, body):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('#!/usr/bin/env bash\n' + body + '\n')
        target.chmod(0o755)

    def set_desired(self, version):
        (self.root / 'kubernetes-version.txt').write_text(version + '\n')

    def write_settings(self, extra=''):
        (self.root / 'automation/settings.toml').write_text(
            '[auto_upgrade]\nslack_webhook_url = "https://hooks.slack.test/x"\n' + extra)

    def run_auto(self, *args, **env):
        return subprocess.run(
            ['bash', str(self.root / 'automation/scripts/upgrade/auto-upgrade.sh'), *args],
            cwd=self.root, env={**self.env, **env}, text=True, capture_output=True, timeout=15,
        )

    def events(self):
        return self.trace.read_text().splitlines() if self.trace.exists() else []

    def ran_upgrade(self):
        return 'run.sh upgrade-safe' in self.events()

    def slack_messages(self):
        return [e for e in self.events() if e.startswith('slack ')]

    def test_same_version_does_nothing(self):
        result = self.run_auto()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.ran_upgrade())
        self.assertFalse(any(e.startswith('curl') for e in self.events()))

    def test_new_minor_runs_upgrade_and_notifies(self):
        self.set_desired('v1.37.2')
        result = self.run_auto(PACKAGES_BODY=PACKAGES.format(v='1.37.2'))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        events = self.events()
        self.assertIn('gate --phase pre', events)
        self.assertLess(events.index('gate --phase pre'), events.index('run.sh upgrade-safe'))
        self.assertTrue(any('pkgs.k8s.io/core:/stable:/v1.37/deb/Packages' in e for e in events))
        self.assertEqual(len(self.slack_messages()), 2)  # 開始と完了
        self.assertFalse((self.state / 'failed').exists())

    def test_unpublished_package_waits_without_latch(self):
        self.set_desired('v1.37.2')
        # 1.37.20 は 1.37.2 として扱わない
        result = self.run_auto(PACKAGES_BODY=PACKAGES.format(v='1.37.20'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.ran_upgrade())
        self.assertFalse((self.state / 'failed').exists())
        self.assertEqual(self.slack_messages(), [])

    def test_skipping_a_minor_latches(self):
        self.set_desired('v1.38.0')
        result = self.run_auto(PACKAGES_BODY=PACKAGES.format(v='1.38.0'))
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.ran_upgrade())
        self.assertTrue((self.state / 'failed').exists())
        self.assertEqual(len(self.slack_messages()), 1)

    def test_downgrade_latches(self):
        self.set_desired('v1.35.9')
        result = self.run_auto()
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.ran_upgrade())
        self.assertIn('古い', (self.state / 'failed').read_text())

    def test_mixed_node_versions_latch(self):
        self.set_desired('v1.37.2')
        result = self.run_auto(WORKER_KUBELET='v1.35.0')
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.ran_upgrade())
        self.assertIn('k8s-worker2', (self.state / 'failed').read_text())

    def test_failure_latches_and_later_runs_skip_until_cleared(self):
        self.set_desired('v1.36.6')
        env = {'PACKAGES_BODY': PACKAGES.format(v='1.36.6')}
        first = self.run_auto(UPGRADE_STATUS='1', **env)
        self.assertEqual(first.returncode, 1)
        self.assertTrue((self.state / 'failed').exists())
        self.trace.unlink()
        second = self.run_auto(**env)
        self.assertEqual(second.returncode, 0)
        self.assertEqual(self.events(), [])  # SSH もしない
        self.assertEqual(self.run_auto('--clear-failure').returncode, 0)
        self.assertFalse((self.state / 'failed').exists())
        third = self.run_auto(**env)
        self.assertEqual(third.returncode, 0, third.stderr)
        self.assertTrue(self.ran_upgrade())

    def test_gate_failure_waits_and_notifies_once_per_version(self):
        self.set_desired('v1.37.2')
        env = {'PACKAGES_BODY': PACKAGES.format(v='1.37.2'), 'GATE_STATUS': '1'}
        for _ in range(2):
            result = self.run_auto(**env)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.ran_upgrade())
        self.assertFalse((self.state / 'failed').exists())
        self.assertEqual(len(self.slack_messages()), 1)

    def test_unreachable_cluster_is_retried_later(self):
        self.set_desired('v1.37.2')
        result = self.run_auto(SSH_STATUS='255')
        self.assertEqual(result.returncode, 0)
        self.assertFalse((self.state / 'failed').exists())
        self.assertEqual(self.slack_messages(), [])

    def test_dry_run_neither_upgrades_nor_notifies(self):
        self.set_desired('v1.37.2')
        result = self.run_auto('--dry-run', PACKAGES_BODY=PACKAGES.format(v='1.37.2'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('dry-run', result.stdout + result.stderr)
        self.assertFalse(self.ran_upgrade())
        self.assertFalse(any(e.startswith('gate') for e in self.events()))
        self.assertEqual(self.slack_messages(), [])

    def test_invalid_version_file_latches(self):
        self.set_desired('1.37')
        result = self.run_auto()
        self.assertEqual(result.returncode, 1)
        self.assertTrue((self.state / 'failed').exists())


class TargetVersionTests(unittest.TestCase):
    """upgrade-*.sh が共有する resolve_target_version。"""

    def resolve(self, file_text, legacy=''):
        with tempfile.TemporaryDirectory() as tmp:
            version_file = Path(tmp) / 'kubernetes-version.txt'
            version_file.write_text(file_text)
            script = (
                'log_error() { echo "$*" >&2; }\n'
                f'get_config() {{ [[ "$2" == target_version ]] && echo "{legacy}" || echo "$3"; }}\n'
                f'source "{ROOT}/automation/scripts/upgrade/kubernetes-version.sh"\n'
                'resolve_target_version\n'
            )
            return subprocess.run(['bash', '-c', script], text=True, capture_output=True,
                                  env={**os.environ, 'KUBERNETES_VERSION_FILE': str(version_file)})

    def test_reads_version_file(self):
        result = self.resolve('v1.37.2\n')
        self.assertEqual((result.returncode, result.stdout), (0, 'v1.37.2\n'))

    def test_matching_legacy_setting_is_allowed(self):
        self.assertEqual(self.resolve('v1.37.2\n', legacy='v1.37.2').returncode, 0)

    def test_conflicting_legacy_setting_fails(self):
        result = self.resolve('v1.37.2\n', legacy='v1.36.1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('target_version', result.stderr)

    def test_rejects_malformed_version(self):
        self.assertNotEqual(self.resolve('1.37.2\n').returncode, 0)

    def test_repository_version_file_is_valid(self):
        self.assertRegex((ROOT / 'kubernetes-version.txt').read_text(), r'^v\d+\.\d+\.\d+\n$')


if __name__ == '__main__':
    unittest.main()
