"""暗号鍵の保持と kubeadm / static Pod 更新の安全性を検証する。"""
import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('security', ROOT / 'automation/infrastructure/apiserver-security/configure.py')
security = importlib.util.module_from_spec(spec)
spec.loader.exec_module(security)


class SecurityTests(unittest.TestCase):
    def test_key_is_private_and_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            security.prepare(root)
            key = root / 'etc/kubernetes/security/encryption-config.yaml'
            before = key.read_bytes()
            security.prepare(root)
            self.assertEqual(before, key.read_bytes())
            self.assertEqual(key.stat().st_mode & 0o777, 0o600)
            config = yaml.safe_load(before)
            self.assertIn('aescbc', config['resources'][0]['providers'][0])
            self.assertEqual(config['resources'][0]['providers'][-1], {'identity': {}})
            audit = yaml.safe_load((key.parent / 'audit-policy.yaml').read_text())
            rules = audit['rules']
            # 最後は全要求の Metadata。除外は読み取り・lease・health に限る。
            self.assertEqual(rules[-1], {'level': 'Metadata'})
            for rule in rules:
                if rule['level'] != 'None':
                    continue
                read_only = set(rule.get('verbs', [])) == {'get', 'list', 'watch'}
                leases = rule.get('resources') == [{'group': 'coordination.k8s.io', 'resources': ['leases']}]
                self.assertTrue(read_only or leases or 'nonResourceURLs' in rule, rule)
            # Secret の記録は、読み取りの除外より前にある。
            secret_index = next(i for i, rule in enumerate(rules)
                                if rule.get('resources') == [{'group': '', 'resources': ['secrets']}])
            self.assertEqual(rules[secret_index]['level'], 'Metadata')
            first_read_exclusion = next(i for i, rule in enumerate(rules) if 'verbs' in rule)
            self.assertLess(secret_index, first_read_exclusion)

    def test_corrupt_key_is_never_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            security.prepare(root)
            key = root / 'etc/kubernetes/security/encryption-config.yaml'
            key.write_text('broken: true\n')
            with self.assertRaises((ValueError, KeyError)):
                security.prepare(root)
            self.assertEqual(key.read_text(), 'broken: true\n')

    def test_kubeadm_preserves_unrelated_configuration(self):
        docs = [{'apiVersion': 'kubeadm.k8s.io/v1beta4', 'kind': 'ClusterConfiguration',
                 'networking': {'podSubnet': '10.244.0.0/16'},
                 'apiServer': {'extraArgs': [{'name': 'authorization-mode', 'value': 'Node,RBAC'}]}}]
        once = copy.deepcopy(security.configure_kubeadm(docs))
        self.assertEqual(once, security.configure_kubeadm(docs))
        api = docs[0]['apiServer']
        self.assertIn({'name': 'authorization-mode', 'value': 'Node,RBAC'}, api['extraArgs'])
        self.assertEqual(len(api['extraVolumes']), 2)
        self.assertTrue(api['extraVolumes'][0]['readOnly'])
        self.assertFalse(api['extraVolumes'][1]['readOnly'])

    def test_existing_encryption_path_is_not_silently_replaced(self):
        docs = [{'apiVersion': 'kubeadm.k8s.io/v1beta4', 'kind': 'ClusterConfiguration',
                 'apiServer': {'extraArgs': [{'name': 'encryption-provider-config', 'value': '/existing/key.yaml'}]}}]
        with self.assertRaises(ValueError):
            security.configure_kubeadm(docs)
        docs = [{'kind': 'Pod', 'spec': {'containers': [{'name': 'kube-apiserver',
                 'command': ['kube-apiserver', '--encryption-provider-config=/existing/key.yaml']}]}}]
        with self.assertRaises(ValueError):
            security.configure_manifest(docs)

    def test_old_kubeadm_format_is_rejected(self):
        with self.assertRaises(ValueError):
            security.configure_kubeadm([{'apiVersion': 'kubeadm.k8s.io/v1beta3', 'kind': 'ClusterConfiguration'}])

    def test_manifest_preserves_cert_mount_and_is_idempotent(self):
        docs = [{'kind': 'Pod', 'spec': {'containers': [{'name': 'kube-apiserver',
                 'command': ['kube-apiserver', '--authorization-mode=Node,RBAC'],
                 'volumeMounts': [{'name': 'certs', 'mountPath': '/etc/kubernetes/pki'}]}],
                 'volumes': [{'name': 'certs', 'hostPath': {'path': '/etc/kubernetes/pki'}}]}}]
        once = copy.deepcopy(security.configure_manifest(docs))
        self.assertEqual(once, security.configure_manifest(docs))
        container = docs[0]['spec']['containers'][0]
        self.assertIn('--authorization-mode=Node,RBAC', container['command'])
        self.assertEqual(container['volumeMounts'][0]['name'], 'certs')
        self.assertIn('--encryption-provider-config=/etc/kubernetes/security/encryption-config.yaml', container['command'])


if __name__ == '__main__':
    unittest.main()
