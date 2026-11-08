"""Runner の権限境界と、実際の生成・Kustomize 出力を検証する。"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[3]
ARC = ROOT / 'manifests/platform/ci-cd/github-actions'
UPDATER = ROOT / 'manifests/platform/ci-cd/image-updater/sandbox-images.yaml'


class RunnerDeliveryTests(unittest.TestCase):
    def test_runner_has_no_api_token_or_privileged_containers(self):
        appset = yaml.safe_load((ARC / 'runners-appset.yaml').read_text())
        values = yaml.safe_load(appset['spec']['template']['spec']['source']['helm']['values'])
        spec = values['template']['spec']
        self.assertFalse(spec['automountServiceAccountToken'])
        self.assertTrue(spec['securityContext']['runAsNonRoot'])
        self.assertEqual(spec['securityContext']['seccompProfile']['type'], 'RuntimeDefault')
        self.assertFalse(spec.get('initContainers'))
        self.assertEqual([c['name'] for c in spec['containers']], ['runner'])
        for container in spec['containers']:
            security = container['securityContext']
            self.assertFalse(security.get('privileged', False))
            self.assertFalse(security['allowPrivilegeEscalation'])
            self.assertEqual(security['capabilities']['drop'], ['ALL'])
        credentials = next(v for v in spec['volumes'] if v['name'] == 'harbor-auth')
        self.assertEqual(credentials['secret']['secretName'], 'harbor-ci-auth')
        for volume in spec['volumes']:
            self.assertNotIn('hostPath', volume)
            self.assertNotIn('projected', volume)
        # 別ファイルから同じ SA に権限を復活させても検出する。
        for path in (ROOT / 'manifests').rglob('*.yaml'):
            if 'charts' in path.parts:
                continue
            for doc in yaml.safe_load_all(path.read_text()):
                if isinstance(doc, dict) and doc.get('kind') in ('RoleBinding', 'ClusterRoleBinding'):
                    for subject in doc.get('subjects', []):
                        self.assertNotEqual(subject.get('name'), 'github-actions-runner', str(path))

    def test_digest_reaches_all_deployment_and_cronjob_templates(self):
        config = yaml.safe_load(UPDATER.read_text())
        self.assertEqual(config['spec']['commonUpdateSettings']['updateStrategy'], 'digest')
        self.assertTrue(config['spec']['commonUpdateSettings']['forceUpdate'])
        self.assertEqual(config['spec']['writeBackConfig']['gitConfig']['writeBackTarget'], 'kustomization')
        covered = set()
        kinds = set()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for ref in config['spec']['applicationRefs']:
                name = ref['namePattern']
                self.assertNotIn('*', name)
                app = yaml.safe_load((ROOT / f'manifests/bootstrap/applications/user-apps/{name}-app.yaml').read_text())
                source = ROOT / app['spec']['source']['path']
                target = root / name
                shutil.copytree(source, target)
                image = ref['images'][0]['manifestTargets']['kustomize']['name']
                self.assertEqual(ref['images'][0]['imageName'], image + ':deploy')
                kfile = target / 'kustomization.yaml'
                kustomization = yaml.safe_load(kfile.read_text())
                entry = next(x for x in kustomization['images'] if x['name'] == image)
                digest = 'sha256:' + 'a' * 64
                entry.pop('newTag', None)
                entry['digest'] = digest
                kfile.write_text(yaml.safe_dump(kustomization))
                rendered = subprocess.check_output(['kustomize', 'build', str(target)], text=True)
                matched = 0
                for doc in yaml.safe_load_all(rendered):
                    if doc['kind'] in ('Deployment', 'StatefulSet'):
                        pod = doc['spec']['template']['spec']
                    elif doc['kind'] == 'CronJob':
                        pod = doc['spec']['jobTemplate']['spec']['template']['spec']
                    else:
                        continue
                    kinds.add(doc['kind'])
                    for container in pod['containers'] + pod.get('initContainers', []):
                        if container['image'].startswith(image + '@'):
                            self.assertEqual(container['image'], image + '@' + digest)
                            matched += 1
                self.assertGreater(matched, 0, name)
                covered.add(source.name)
        self.assertEqual(kinds, {'Deployment', 'StatefulSet', 'CronJob'})
        expected = {p.parent.name for p in (ROOT / 'manifests/apps').glob('*/manifest.yaml')
                    if 'harbor.qroksera.com/sandbox/' in p.read_text()}
        self.assertEqual(covered, expected)


if __name__ == '__main__':
    unittest.main()
