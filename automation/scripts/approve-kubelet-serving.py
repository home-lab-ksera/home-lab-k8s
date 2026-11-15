#!/usr/bin/env python3
"""標準入力の CSR 一覧から、台帳のノード自身が出した未承認の kubelet serving CSR の名前を出力する。"""
import json
from pathlib import Path
import sys


def approvable(csrs, hosts):
    names = []
    for item in csrs.get('items', []):
        spec = item.get('spec', {})
        if spec.get('signerName') != 'kubernetes.io/kubelet-serving':
            continue
        if item.get('status', {}).get('conditions'):
            continue
        user = spec.get('username', '')
        if user.startswith('system:node:') and user[len('system:node:'):] in hosts:
            names.append(item['metadata']['name'])
    return names


if __name__ == '__main__':
    print('\n'.join(approvable(json.load(sys.stdin), json.loads(Path(sys.argv[1]).read_text()))))
