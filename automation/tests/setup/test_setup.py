"""実環境に接続せず、実スクリプトの制御フローを検証する。"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='setup-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.trace = self.root / 'trace'
        # 許可したコマンドだけを PATH に置く。実 SSH / sudo / kubectl は到達不能。
        for command in ('bash', 'dirname', 'date', 'tee', 'awk', 'timeout'):
            source = shutil.which(command)
            self.assertIsNotNone(source, command)
            (self.bin / command).symlink_to(source)
        self.env = {
            'PATH': str(self.bin), 'HOME': str(self.root),
            'TRACE': str(self.trace), 'LC_ALL': 'C',
        }
        for path in ('automation/scripts/run.sh',
                     'automation/scripts/ci/setup-integration.sh',
                     'automation/scripts/common-logging.sh',
                     'automation/scripts/verify.sh',
                     'automation/platform/platform-deploy.sh',
                     'manifests/bootstrap/app-of-apps.yaml',
                     'manifests/platform/argocd-config/upstream/kustomization.yaml'):
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / path, target)
        # ローカル settings.toml / credentials はコピーしない。
        self.stub('sudo', '[[ "$*" == "-v" ]] || exit 99\nexit "${SUDO_STATUS:-0}"')

    def stub(self, name, body):
        path = self.bin / name
        path.write_text('#!/usr/bin/env bash\nset -euo pipefail\n' + body + '\n')
        path.chmod(0o755)

    def run_script(self, path, *args, **env):
        return subprocess.run(
            ['bash', str(self.root / path), *args], cwd=self.root,
            env={**self.env, **env}, text=True, capture_output=True, timeout=15,
        )

    def events(self):
        return self.trace.read_text().splitlines() if self.trace.exists() else []

    def prepare_phases(self):
        for name, path in (
            ('phase1', 'automation/host-setup/setup-host.sh'),
            ('phase2', 'automation/infrastructure/clean-and-deploy.sh'),
            ('bootstrap', 'automation/platform/platform-deploy.sh'),
            ('phase5', 'automation/scripts/verify.sh'),
        ):
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                '#!/usr/bin/env bash\n'
                f'echo {name} >> "$TRACE"\n'
                f'echo output-{name}\n'
                f'if [[ "${{FAIL_PHASE:-}}" == {name} ]]; then exit 42; fi\n'
            )
            target.chmod(0o755)

    def test_all_order_and_log(self):
        self.prepare_phases()
        result = self.run_script('automation/scripts/run.sh', 'all')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.events(), ['phase1', 'phase2', 'bootstrap', 'phase5'])
        log = (self.root / 'automation/run.log').read_text()
        for phase in self.events():
            self.assertIn(f'output-{phase}', log)

    def test_each_failure_stops_later_phases(self):
        self.prepare_phases()
        phases = ['phase1', 'phase2', 'bootstrap', 'phase5']
        for index, phase in enumerate(phases):
            with self.subTest(phase=phase):
                self.trace.unlink(missing_ok=True)
                result = self.run_script('automation/scripts/run.sh', 'all', FAIL_PHASE=phase)
                self.assertEqual(result.returncode, 42, result.stdout + result.stderr)
                self.assertEqual(self.events(), phases[:index + 1])

    def test_aliases(self):
        self.prepare_phases()
        for arg, expected in [('phase1', 'phase1'), ('vm', 'phase1'),
                              ('phase2', 'phase2'), ('k8s', 'phase2'),
                              ('bootstrap', 'bootstrap'), ('phase5', 'phase5'),
                              ('verify', 'phase5')]:
            with self.subTest(arg=arg):
                self.trace.unlink(missing_ok=True)
                result = self.run_script('automation/scripts/run.sh', arg)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.events(), [expected])

    def test_sudo_failure_and_unknown_phase(self):
        self.prepare_phases()
        for args, env in [(('all',), {'SUDO_STATUS': '1'}), (('invalid',), {})]:
            result = self.run_script('automation/scripts/run.sh', *args, **env)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(self.events(), [])

    def prepare_bootstrap(self):
        gate = self.root / "automation/scripts/kubelet-serving-certificates.sh"
        gate.write_text('echo certificate-gate >> "$TRACE"\nexit "${CERTIFICATE_STATUS:-0}"\n')
        self.stub('scp', 'echo copy >> "$TRACE"\nexit "${COPY_STATUS:-0}"')
        self.stub('ssh', r'''
command="${!#}"
if [[ "$command" == 'kubectl get nodes' ]]; then
    echo connect >> "$TRACE"
    exit "${CONNECT_STATUS:-0}"
elif [[ "$command" == 'mkdir -p /tmp/k8s-myhome-bootstrap' ]]; then
    exit 0
elif [[ "$command" == *' bash -s' ]]; then
    # リモート heredoc も実際の Bash で評価する。kubectl は下の stub のみ。
    exec bash -c "$command"
elif [[ "$command" == 'k8suser@192.168.122.10' ]]; then
    exec bash -s
fi
echo "unexpected ssh: $command" >&2
exit 99
''')
        self.stub('kubectl', r'''
echo "kubectl $*" >> "$TRACE"
if [[ -n "${FAIL_COMMAND:-}" && "$*" == *"$FAIL_COMMAND"* ]]; then exit 43; fi
# パイプ入力を消費する（実 API への適用はしない）。
if [[ "$*" == *'-f -'* ]]; then
    while IFS= read -r line; do :; done
fi
''')

    def test_bootstrap_remote_bodies_and_order(self):
        self.prepare_bootstrap()
        token = "fake token ' with $ punctuation"
        result = self.run_script('automation/scripts/run.sh', 'bootstrap', PULUMI_ACCESS_TOKEN=token)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        events = self.events()
        install = next(i for i, s in enumerate(events) if '/manifests/install.yaml' in s)
        secret = next(i for i, s in enumerate(events) if 'create secret generic pulumi-esc-token' in s)
        root = events.index('kubectl apply -f /tmp/k8s-myhome-bootstrap/app-of-apps.yaml')
        self.assertLess(events.index("certificate-gate"), install)
        self.assertLess(install, secret)
        self.assertLess(secret, root)
        self.assertIn('--from-literal=accessToken=' + token, events[secret])
        self.assertNotIn(token, result.stdout + result.stderr)
        self.assertFalse(any('argocd-ingress.yaml' in event or 'argocd.local' in event for event in events))

    def test_bootstrap_failures_do_not_apply_root(self):
        self.prepare_bootstrap()
        cases = [
            {'CONNECT_STATUS': '1'}, {'COPY_STATUS': '1'},
            {'CERTIFICATE_STATUS': '1'},
            {'PULUMI_ACCESS_TOKEN': ''},
            {'FAIL_COMMAND': '/manifests/install.yaml'},
            {'FAIL_COMMAND': 'rollout status'},
            {'FAIL_COMMAND': 'create secret generic'},
        ]
        for case in cases:
            with self.subTest(case=case):
                self.trace.unlink(missing_ok=True)
                result = self.run_script('automation/scripts/run.sh', 'bootstrap',
                                         **{'PULUMI_ACCESS_TOKEN': 'fake', **case})
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('kubectl apply -f /tmp/k8s-myhome-bootstrap/app-of-apps.yaml', self.events())

    def test_bootstrap_uses_gitops_version(self):
        self.prepare_bootstrap()
        upstream = self.root / 'manifests/platform/argocd-config/upstream/kustomization.yaml'
        upstream.write_text('resources:\n  - https://raw.githubusercontent.com/argoproj/argo-cd/v9.8.7/manifests/install.yaml\n')
        result = self.run_script('automation/scripts/run.sh', 'bootstrap', PULUMI_ACCESS_TOKEN='fake')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(any('/v9.8.7/manifests/install.yaml' in event for event in self.events()))

    def test_bootstrap_rejects_unpinned_version_before_ssh(self):
        upstream = self.root / 'manifests/platform/argocd-config/upstream/kustomization.yaml'
        upstream.write_text('resources:\n  - https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml\n')
        result = self.run_script('automation/scripts/run.sh', 'bootstrap', PULUMI_ACCESS_TOKEN='fake')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.events(), [])

    def test_verify_ci_skip_never_connects(self):
        self.stub('ssh', 'echo unexpected-ssh >> "$TRACE"\nexit 99')
        for env in ({'CI': 'true'}, {'CI': '1'}, {'VERIFY_SKIP_SSH': 'true'}):
            result = self.run_script('automation/scripts/verify.sh', **env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.events(), [])
            self.assertFalse((self.root / '.kube/config').exists())

    def test_integration_rejects_non_disposable_environment(self):
        for env in ({}, {'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'self-hosted'},
                    {'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'github-hosted',
                     'RUNNER_TEMP': str(self.root), 'SETUP_CI_DIR': '/unexpected'}):
            with self.subTest(env=env):
                result = self.run_script('automation/scripts/ci/setup-integration.sh', 'prepare', **env)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertEqual(self.events(), [])

    def test_integration_reapply_stops_on_drift_or_error(self):
        self.stub('sg', 'exec bash -c "$3"')
        self.stub('terraform', r'''
echo "$*" >> "$TRACE"
if [[ "$*" == *' plan '* ]]; then exit "${PLAN_STATUS:-0}"; fi
if [[ "$*" == *' apply '* ]]; then exit "${APPLY_STATUS:-0}"; fi
exit 99
''')
        env = {'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'github-hosted',
               'RUNNER_TEMP': str(self.root),
               'SETUP_CI_DIR': str(self.root / 'setup-integration')}
        for plan_status, apply_status, expected, count in (
                ('0', '0', 0, 3), ('2', '0', 2, 1),
                ('1', '0', 1, 1), ('0', '42', 42, 2)):
            with self.subTest(plan_status=plan_status, apply_status=apply_status):
                self.trace.unlink(missing_ok=True)
                result = self.run_script('automation/scripts/ci/setup-integration.sh',
                                         'reapply', **env, PLAN_STATUS=plan_status,
                                         APPLY_STATUS=apply_status)
                self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
                self.assertEqual(len(self.events()), count)
                self.assertIn('plan -detailed-exitcode', self.events()[0])
                if count == 3:
                    self.assertIn('apply -auto-approve', self.events()[1])
                    self.assertIn('plan -detailed-exitcode', self.events()[2])


if __name__ == '__main__':
    unittest.main()
