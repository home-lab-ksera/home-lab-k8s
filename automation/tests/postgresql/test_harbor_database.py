"""chart 展開後、DB 利用コンポーネントの CA 配布漏れを検出する。"""
from pathlib import Path
import subprocess
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[3]


class HarborDatabaseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rendered = subprocess.check_output(
            ['kustomize', 'build', '--enable-helm', str(ROOT / 'manifests/platform/harbor')],
            text=True,
        )
        cls.resources = {(d['kind'], d['metadata']['name']): d
                         for d in yaml.safe_load_all(rendered) if d}

    def test_all_database_clients_have_tls_and_restore_gate(self):
        # jobservice は core の内部 API から DB 設定を取得するため、独自の CA 配布が必要。
        names = ['harbor-core', 'harbor-jobservice']
        if ('Deployment', 'harbor-exporter') in self.resources:
            names.append('harbor-exporter')
        config = self.resources['ConfigMap', 'harbor-core']['data']
        self.assertEqual(config['POSTGRESQL_SSLMODE'], 'verify-full')
        for name in names:
            with self.subTest(deployment=name):
                pod = self.resources['Deployment', name]['spec']['template']['spec']
                volumes = {v['name']: v for v in pod['volumes']}
                gate = next(c for c in pod['initContainers'] if c['name'] == 'wait-for-database')
                env = {v['name']: v for v in gate['env']}
                self.assertEqual(env['PGHOST']['value'], config['POSTGRESQL_HOST'])
                self.assertEqual(env['PGUSER']['value'], config['POSTGRESQL_USERNAME'])
                self.assertEqual(env['PGUSER']['value'], 'app')
                self.assertEqual(env['PGSSLMODE']['value'], 'verify-full')
                self.assertEqual(env['PGPASSWORD']['valueFrom']['secretKeyRef']['name'], 'harbor-database-secret')
                for container in [gate, *pod['containers']]:
                    ca_path = next(v['value'] for v in container['env'] if v['name'] == 'PGSSLROOTCERT')
                    mount = next(m for m in container['volumeMounts'] if ca_path.startswith(m['mountPath'] + '/'))
                    self.assertTrue(mount['readOnly'])
                    self.assertEqual(volumes[mount['name']]['configMap']['name'], 'shared-postgresql-ca')

    def test_database_is_external_and_other_storage_is_preserved(self):
        self.assertNotIn(('StatefulSet', 'harbor-database'), self.resources)
        for name in ['harbor-redis']:
            for claim in self.resources['StatefulSet', name]['spec']['volumeClaimTemplates']:
                self.assertEqual(claim['spec']['storageClassName'], 'nfs-external')


if __name__ == '__main__':
    unittest.main()
