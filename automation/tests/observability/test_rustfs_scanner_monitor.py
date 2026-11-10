"""RustFS scanner 監視スクリプトのメトリクス変換と署名の回帰テスト。"""
import datetime
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2].parent
SPEC = importlib.util.spec_from_file_location('scanner_monitor', ROOT / 'manifests/platform/rustfs/scripts/scanner-monitor.py')
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


class ScannerMonitorTests(unittest.TestCase):
    def test_healthy_status(self):
        status = {'enabled': True, 'freshness': {'last_cycle_end_unix_secs': 1700000000},
                  'cycle_recovery': {'state': 'healthy'}}
        body = m.metrics(status, {'lifecycle_expiry': {'delete_failed': 0}}, 1700000100.9)
        self.assertEqual(body.splitlines(), [
            'rustfs_scanner_enabled 1',
            'rustfs_scanner_last_cycle_end_timestamp_seconds 1700000000',
            'rustfs_scanner_recovery_healthy{state="healthy"} 1',
            'rustfs_lifecycle_delete_failed 0',
            'rustfs_scanner_monitor_last_run_timestamp_seconds 1700000100'])

    def test_usage_floor_failure_and_missing_fields(self):
        # 2026-11-10 に観測した停止状態。完了した走査がなく recovery が paused。
        status = {'enabled': True, 'freshness': {'last_cycle_end_unix_secs': 0},
                  'cycle_recovery': {'state': 'paused', 'classification': 'usage_floor_load_failed'}}
        lines = m.metrics(status, {}, 1).splitlines()
        self.assertIn('rustfs_scanner_last_cycle_end_timestamp_seconds 0', lines)
        self.assertIn('rustfs_scanner_recovery_healthy{state="paused"} 0', lines)
        self.assertIn('rustfs_lifecycle_delete_failed 0', lines)
        self.assertIn('rustfs_scanner_recovery_healthy{state="unknown"} 0', m.metrics({}, {}, 1).splitlines())

    def test_request_is_signed_with_sigv4(self):
        captured = {}
        class Response:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b'{}'
        def urlopen(req, timeout):
            captured.update(url=req.full_url, headers=dict(req.header_items()))
            return Response()
        now = datetime.datetime(2026, 11, 10, 8, 0, 0, tzinfo=datetime.timezone.utc)
        with patch.object(m.urllib.request, 'urlopen', urlopen), patch.object(m, 'RUSTFS_URL', 'http://rustfs:9000'):
            m.signed_get('/rustfs/admin/v3/scanner/status', 'AKID', 'SECRET', now)
        auth = captured['headers']['Authorization']
        self.assertEqual(captured['url'], 'http://rustfs:9000/rustfs/admin/v3/scanner/status')
        self.assertTrue(auth.startswith('AWS4-HMAC-SHA256 Credential=AKID/20261110/us-east-1/s3/aws4_request, '
                                        'SignedHeaders=host;x-amz-content-sha256;x-amz-date, Signature='))
        self.assertEqual(captured['headers']['X-amz-date'], '20261110T080000Z')
        self.assertNotIn('SECRET', auth)


if __name__ == '__main__':
    unittest.main()
