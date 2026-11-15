"""証明書準備の失敗で後続処理を止め、検証のみの実行では適用しないことを確認する。"""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('verify_serving', ROOT / 'automation/scripts/verify-kubelet-serving.py')
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)


class CertificateSetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.trace = self.root / 'trace'
        for command in ('bash', 'dirname'):
            (self.bin / command).symlink_to(shutil.which(command))
        path = self.root / 'automation/scripts/kubelet-serving-certificates.sh'
        path.parent.mkdir(parents=True)
        shutil.copy2(ROOT / 'automation/scripts/kubelet-serving-certificates.sh', path)
        self.script = path
        self.stub('scp', 'echo scp >> "$TRACE"\nexit "${COPY_STATUS:-0}"')
        self.stub('ssh', r'''
command="${!#}"
echo "$command" >> "$TRACE"
case "$command" in
  'mktemp -d '*) echo /tmp/kubelet-certs.TEST ;;
  'set -o pipefail; kubectl get csr '*) exit "${APPLY_STATUS:-0}" ;;
  'python3 '*) exit "${VERIFY_STATUS:-0}" ;;
  'rm -rf '*) exit 0 ;;
  *) exit 99 ;;
esac
''')

    def stub(self, name, body):
        p = self.bin / name
        p.write_text('#!/usr/bin/env bash\nset -euo pipefail\n' + body)
        p.chmod(0o755)

    def run_gate(self, mode='--prepare', **env):
        self.trace.unlink(missing_ok=True)
        result = subprocess.run(['bash', str(self.script), mode], env={
            'PATH': str(self.bin), 'TRACE': str(self.trace), **env,
        }, text=True, capture_output=True, timeout=10)
        events = self.trace.read_text().splitlines() if self.trace.exists() else []
        return result, events

    def test_prepare_verifies_after_apply(self):
        result, events = self.run_gate()
        self.assertEqual(result.returncode, 0, result.stderr)
        apply = next(i for i, s in enumerate(events) if s.startswith('set -o pipefail; kubectl get csr'))
        check = next(i for i, s in enumerate(events) if s.startswith('python3'))
        self.assertLess(apply, check)
        self.assertTrue(events[-1].startswith('rm -rf'))

    def test_verify_only_never_applies(self):
        result, events = self.run_gate('--verify-only')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(s.startswith('set -o pipefail; kubectl get csr') for s in events))
        self.assertTrue(any(s.startswith('python3') for s in events))

    def test_copy_apply_and_tls_failure_propagate(self):
        for var in ('COPY_STATUS', 'APPLY_STATUS', 'VERIFY_STATUS'):
            with self.subTest(var=var):
                result, events = self.run_gate(**{var: '42'})
                self.assertEqual(result.returncode, 42, result.stderr)
                if var != 'VERIFY_STATUS':
                    self.assertFalse(any(s.startswith('python3') for s in events))
                self.assertTrue(events[-1].startswith('rm -rf'))


class CertificateApprovalTests(unittest.TestCase):
    def test_only_pending_serving_requests_from_listed_nodes(self):
        spec = importlib.util.spec_from_file_location('approve', ROOT / 'automation/scripts/approve-kubelet-serving.py')
        approve = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(approve)

        def csr(name, user='system:node:node1', signer='kubernetes.io/kubelet-serving', conditions=None):
            return {'metadata': {'name': name}, 'spec': {'username': user, 'signerName': signer},
                    'status': {'conditions': conditions} if conditions else {}}

        csrs = {'items': [
            csr('ok'),
            csr('other-node', user='system:node:unknown'),
            csr('not-a-node', user='system:serviceaccount:default:x'),
            csr('client', signer='kubernetes.io/kube-apiserver-client-kubelet'),
            csr('done', conditions=[{'type': 'Approved'}]),
        ]}
        self.assertEqual(approve.approvable(csrs, {'node1': '192.168.122.11'}), ['ok'])


class CertificateVerificationTests(unittest.TestCase):
    def setUp(self):
        self.hosts = {'node1': '192.168.122.11'}
        self.nodes = {'items': [{'metadata': {'name': 'node1'}, 'status': {
            'addresses': [{'type': 'InternalIP', 'address': '192.168.122.11'}],
            'conditions': [{'type': 'Ready', 'status': 'True'}],
        }}]}
        self.context = MagicMock()
        self.context.wrap_socket.return_value.__enter__.return_value.getpeercert.return_value = {
            'notAfter': 'Jan  1 00:00:00 2099 GMT',
        }

    @patch.object(verify.socket, 'create_connection')
    def test_checks_ip_and_live_tls(self, connection):
        verify.verify_nodes(self.hosts, self.nodes, self.context)
        connection.assert_called_once_with(('192.168.122.11', 10250), timeout=5)
        self.assertEqual(self.context.wrap_socket.call_args.kwargs['server_hostname'], '192.168.122.11')

    @patch.object(verify.socket, 'create_connection')
    def test_rejects_bad_ca_or_san(self, connection):
        self.context.wrap_socket.side_effect = verify.ssl.SSLCertVerificationError('bad CA/IP')
        with self.assertRaises(verify.ssl.SSLCertVerificationError):
            verify.verify_nodes(self.hosts, self.nodes, self.context)

    @patch.object(verify.socket, 'create_connection')
    def test_rejects_expiring_certificate(self, connection):
        self.context.wrap_socket.return_value.__enter__.return_value.getpeercert.return_value = {
            'notAfter': 'Jan  1 00:00:00 2000 GMT',
        }
        with self.assertRaises(ValueError):
            verify.verify_nodes(self.hosts, self.nodes, self.context)

    def test_missing_node_wrong_ip_and_not_ready_fail(self):
        for change in ('missing', 'IP', 'Ready'):
            nodes = json.loads(json.dumps(self.nodes))
            if change == 'missing':
                nodes['items'] = []
            elif change == 'IP':
                nodes['items'][0]['status']['addresses'][0]['address'] = '192.168.122.12'
            else:
                nodes['items'][0]['status']['conditions'][0]['status'] = 'False'
            with self.subTest(change=change), self.assertRaises(ValueError):
                verify.verify_nodes(self.hosts, nodes, self.context)
