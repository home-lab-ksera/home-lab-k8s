"""Image Updater の digest 反映が全 workload template に届くことを検証する。"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[3]
UPDATER = ROOT / 'manifests/platform/ci-cd/image-updater/sandbox-images.yaml'


class ImageDeliveryTests(unittest.TestCase):
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
