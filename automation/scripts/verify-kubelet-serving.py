#!/usr/bin/env python3
"""control-plane 上で台帳・Node Ready・実配信証明書を検証する。変更は行わない。"""
import argparse
import json
from pathlib import Path
import socket
import ssl
import subprocess
import time


def verify_nodes(hosts, nodes, context):
    by_name = {node['metadata']['name']: node for node in nodes['items']}
    if set(by_name) != set(hosts):
        raise ValueError('登録ノードが kubelet-serving-nodes.json と一致しません')
    for name, ip in hosts.items():
        status = by_name[name]['status']
        ips = [a['address'] for a in status.get('addresses', []) if a['type'] == 'InternalIP']
        if ips != [ip]:
            raise ValueError(f'{name}: InternalIP が台帳と一致しません')
        if not any(c['type'] == 'Ready' and c['status'] == 'True' for c in status.get('conditions', [])):
            raise ValueError(f'{name}: Ready ではありません')
        with socket.create_connection((ip, 10250), timeout=5) as sock:
            with context.wrap_socket(sock, server_hostname=ip) as tls:
                cert = tls.getpeercert()
                if ssl.cert_time_to_seconds(cert['notAfter']) <= time.time() + 86400:
                    raise ValueError(f'{name}: 証明書の残存期間が1日以下です')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('inventory', type=Path)
    parser.add_argument('--timeout', type=int, default=300)
    args = parser.parse_args()
    hosts = json.loads(args.inventory.read_text())
    if not hosts or args.timeout <= 0:
        parser.error('台帳と正の timeout が必要です')
    # 監視Podと同じクラスタCAをAPIから取得する。CA秘密鍵やadmin kubeconfigは取り出さない。
    result = subprocess.run(
        ['kubectl', '--request-timeout=15s', '-n', 'kube-system', 'get', 'configmap',
         'kube-root-ca.crt', '-o', 'json'], check=True, text=True, capture_output=True, timeout=20)
    ca = json.loads(result.stdout)['data']['ca.crt']
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cadata=ca)
    deadline = time.monotonic() + args.timeout
    while True:
        try:
            result = subprocess.run(['kubectl', '--request-timeout=15s', 'get', 'nodes', '-o', 'json'],
                                    check=True, text=True, capture_output=True, timeout=20)
            verify_nodes(hosts, json.loads(result.stdout), context)
            print('全ノードの Ready・クラスタCA・InternalIP SAN・証明書有効期限を確認しました')
            return
        except (ValueError, OSError, subprocess.SubprocessError) as error:
            if time.monotonic() >= deadline:
                raise SystemExit(f'kubelet serving 証明書の検証に失敗しました: {error}') from error
            print(f'証明書の準備待ち: {error}', flush=True)
            time.sleep(5)


if __name__ == '__main__':
    main()
