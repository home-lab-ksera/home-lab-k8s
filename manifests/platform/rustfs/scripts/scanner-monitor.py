"""RustFS scanner の状態を管理 API から取得し、VictoriaMetrics へ送信する。

scanner は lifecycle（30日保持）の実行も担う。停止しても RustFS 本体は正常に見えるため、
最後に完了した走査の時刻を監視する。
"""
import datetime
import hashlib
import hmac
import json
import os
import time
import urllib.request

RUSTFS_URL = os.environ.get('RUSTFS_URL', 'http://rustfs-svc.rustfs.svc:9000')
METRICS_URL = os.environ.get('METRICS_URL', 'http://victoria-metrics.observability.svc:8428/api/v1/import/prometheus')
REGION, SERVICE = 'us-east-1', 's3'


def signed_get(path, access_key, secret_key, now=None):
    """AWS Signature V4 で署名した GET（本文なし）。"""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    amz_date, date = now.strftime('%Y%m%dT%H%M%SZ'), now.strftime('%Y%m%d')
    host = RUSTFS_URL.split('://', 1)[1].rstrip('/')
    payload = hashlib.sha256(b'').hexdigest()
    headers = {'host': host, 'x-amz-content-sha256': payload, 'x-amz-date': amz_date}
    signed = ';'.join(sorted(headers))
    canonical = '\n'.join(['GET', path, '', ''.join(f'{k}:{headers[k]}\n' for k in sorted(headers)), signed, payload])
    scope = f'{date}/{REGION}/{SERVICE}/aws4_request'
    to_sign = '\n'.join(['AWS4-HMAC-SHA256', amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    key = ('AWS4' + secret_key).encode()
    for part in (date, REGION, SERVICE, 'aws4_request'):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    request_headers = {k: v for k, v in headers.items() if k != 'host'}
    request_headers['Authorization'] = (f'AWS4-HMAC-SHA256 Credential={access_key}/{scope}, '
                                        f'SignedHeaders={signed}, Signature={signature}')
    req = urllib.request.Request(RUSTFS_URL.rstrip('/') + path, headers=request_headers)
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.loads(res.read())


def metrics(status, expiry, now):
    recovery = (status.get('cycle_recovery') or {}).get('state') or 'unknown'
    last_end = (status.get('freshness') or {}).get('last_cycle_end_unix_secs') or 0
    life = expiry.get('lifecycle_expiry') or {}
    lines = [
        f'rustfs_scanner_enabled {1 if status.get("enabled") else 0}',
        f'rustfs_scanner_last_cycle_end_timestamp_seconds {int(last_end)}',
        f'rustfs_scanner_recovery_healthy{{state="{recovery}"}} {1 if recovery == "healthy" else 0}',
        f'rustfs_lifecycle_delete_failed {int(life.get("delete_failed") or 0)}',
        f'rustfs_scanner_monitor_last_run_timestamp_seconds {int(now)}',
    ]
    return '\n'.join(lines) + '\n'


def main():
    access_key, secret_key = os.environ['RUSTFS_ACCESS_KEY'], os.environ['RUSTFS_SECRET_KEY']
    status = signed_get('/rustfs/admin/v3/scanner/status', access_key, secret_key)
    expiry = signed_get('/rustfs/admin/v3/ilm/expiry/status', access_key, secret_key)
    body = metrics(status, expiry, time.time())
    print(body, end='', flush=True)
    req = urllib.request.Request(METRICS_URL, method='POST', data=body.encode(), headers={'Content-Type': 'text/plain'})
    urllib.request.urlopen(req, timeout=30).close()


if __name__ == '__main__':
    main()
