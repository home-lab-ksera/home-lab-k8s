"""外部通信なしで更新候補の選択・連動更新・chart差し替えを検証する。"""
import importlib.util
import io
from pathlib import Path
import tarfile
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('audit', ROOT / 'automation/scripts/ci/version-audit.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class VersionTests(unittest.TestCase):
    def test_timeout_does_not_expose_authorization(self):
        failure = subprocess.TimeoutExpired(['curl', '--header', 'Authorization: Bearer private-token'], 300)
        with patch.object(audit.subprocess, 'run', side_effect=failure):
            with self.assertRaises(RuntimeError) as caught:
                audit.fetch('https://example.com', ('Authorization: Bearer private-token',))
        self.assertNotIn('private-token', str(caught.exception))

    def test_stable_only_and_numeric_order(self):
        self.assertEqual(audit.stable_latest(['v1.9.0', 'v1.10.0', 'v2.0.0-rc.1', 'latest']), 'v1.10.0')
        with self.assertRaises(ValueError):
            audit.stable_latest(['v2.0.0-beta.12-preview.1'])

    def test_image_variant_and_no_downgrade(self):
        self.assertEqual(audit.image_latest('3.15.0-slim-bookworm', [
            '3.16.0a1-slim-bookworm', '3.15.1-slim-trixie', '3.15.1-slim-bookworm', '4.0.0-alpine',
        ]), '3.15.1-slim-bookworm')
        self.assertEqual(audit.image_latest('v2.2.0', ['v2.1.0', 'v3.0.0-rc1']), 'v2.2.0')
        self.assertEqual(audit.image_latest('8.10.2-alpine', ['8.11.0-alpine', '8.12.0-alpine3.24']), '8.11.0-alpine')

    def test_selenium_numeric_date_order(self):
        self.assertEqual(audit.image_latest('155.0-20261010', [
            '155.0-20261015', '156.0-20261011', '4.51.0-20261010', 'nightly',
        ]), '156.0-20261011')

    def test_unrecognized_tag_fails_closed(self):
        with self.assertRaises(ValueError):
            audit.image_latest('unrecognized-version', ['latest'])


class UpdateTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.audit = audit.Audit(self.root, apply=True)

    def write(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def test_image_change_verified_and_dry_run_read_only(self):
        path = self.write('manifests/apps/test.yaml', 'image: redis:8.10.2-alpine\n')
        with patch.object(audit, 'registry_tags', return_value=['8.11.0-alpine']), \
             patch.object(audit, 'registry', return_value=('registry-1.docker.io', 'library/redis', ())), \
             patch.object(audit, 'verify_image') as verify:
            dry = audit.Audit(self.root)
            dry.image(path, 'redis', '8.10.2-alpine', 'redis:8.10.2-alpine')
            self.assertIn('8.10.2', path.read_text())
            self.audit.image(path, 'redis', '8.10.2-alpine', 'redis:8.10.2-alpine')
            self.assertEqual(path.read_text(), 'image: redis:8.11.0-alpine\n')
            verify.assert_called_with('redis', '8.11.0-alpine')

    def test_private_and_floating_images_are_skipped(self):
        with patch.object(audit, 'registry_tags') as tags:
            for repo, tag in [('harbor.qroksera.com/sandbox/blog', '0.0.1'), ('busybox', 'latest'), ('docker', 'dind')]:
                self.audit.image(self.root / 'unused', repo, tag, repo + ':' + tag)
            tags.assert_not_called()

    def test_remote_release_pin_and_asset_verification(self):
        url = 'https://raw.githubusercontent.com/argoproj/argo-cd/v3.5.5/manifests/install.yaml'
        path = self.write(audit.ARGO_BASE, 'resources:\n  - ' + url + '\n')
        with patch.object(audit, 'github', return_value={'tag_name': 'v3.6.0'}), patch.object(audit, 'fetch') as fetch:
            self.audit.remote(path, url)
            fetch.assert_called_once_with(url.replace('v3.5.5', 'v3.6.0'))
        self.assertIn('v3.6.0', path.read_text())

    @staticmethod
    def archive(files):
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode='w:gz') as archive:
            for name, contents in files.items():
                info = tarfile.TarInfo(name)
                info.size = len(contents.encode())
                archive.addfile(info, io.BytesIO(contents.encode()))
        return data.getvalue()

    def test_chart_replacement_preserves_secret_templates(self):
        parent = self.root / 'manifests/platform/harbor'
        self.write('manifests/platform/harbor/charts/harbor-1.0.0/harbor/old', 'old')
        contents = self.archive({'harbor/Chart.yaml': 'name: harbor\nversion: 1.1.0',
                                 'harbor/templates/trivy/trivy-secret.yaml': '{{ .Values.secret }}'})
        with patch.object(audit, 'fetch', return_value=contents):
            self.audit.vendor_chart(parent, 'harbor', '1.0.0', '1.1.0', 'https://example/chart.tgz')
        self.assertFalse((parent / 'charts/harbor-1.0.0').exists())
        self.assertTrue((parent / 'charts/harbor-1.1.0/harbor/templates/trivy/trivy-secret.yaml').is_file())

    def test_chart_path_traversal_rejected_before_deleting_old(self):
        parent = self.root / 'manifests/platform/harbor'
        old = self.write('manifests/platform/harbor/charts/harbor-1.0.0/harbor/old', 'old')
        with patch.object(audit, 'fetch', return_value=self.archive({'../escape': 'bad'})):
            with self.assertRaises(ValueError):
                self.audit.vendor_chart(parent, 'harbor', '1.0.0', '1.1.0', 'https://example/chart.tgz')
        self.assertTrue(old.is_file())
        self.assertFalse((self.root / 'escape').exists())

    def test_chart_no_downgrade_and_prerelease_ignored(self):
        path = self.write('manifests/bootstrap/app.yaml', 'targetRevision: 2.1.0\n')
        index = yaml.safe_dump({'entries': {'test': [
            {'version': '2.0.0', 'urls': ['test.tgz']},
            {'version': '3.0.0-rc.1', 'urls': ['test-rc.tgz']},
        ]}}).encode()
        with patch.object(audit, 'fetch', return_value=index):
            self.audit.chart(path, {'chart': 'test', 'repoURL': 'https://example.com', 'targetRevision': '2.1.0'}, False)
        self.assertEqual(path.read_text(), 'targetRevision: 2.1.0\n')
        self.assertFalse(self.audit.rows[0]['changed'])


if __name__ == '__main__':
    unittest.main()
