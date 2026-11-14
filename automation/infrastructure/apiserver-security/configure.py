#!/usr/bin/env python3
"""API Server の暗号化・監査設定。鍵は対象ノード上だけで生成する。"""
import argparse
import base64
import os
from pathlib import Path
import secrets
import tempfile

import yaml

SECURITY_DIR = '/etc/kubernetes/security'
AUDIT_DIR = '/var/log/kubernetes/audit'
# 先に一致した rule が使われる。変更（create / update / patch / delete）と、人の操作、Secret への
# アクセスはすべて Metadata で残す。機械的な読み取りだけを除外し、ログの保持期間を確保する。
AUDIT_RULES = [
    # health 確認と metrics の取得。
    {'level': 'None', 'nonResourceURLs': ['/healthz*', '/livez*', '/readyz*', '/version', '/metrics']},
    # leader election の lease 更新。
    {'level': 'None', 'resources': [{'group': 'coordination.k8s.io', 'resources': ['leases']}]},
    # Secret は誰が読んでも記録する（下の除外より前に置く）。
    {'level': 'Metadata', 'resources': [{'group': '', 'resources': ['secrets']}]},
    # ServiceAccount・kubelet・control-plane コンポーネントの読み取り。
    {'level': 'None', 'verbs': ['get', 'list', 'watch'],
     'userGroups': ['system:serviceaccounts', 'system:nodes']},
    {'level': 'None', 'verbs': ['get', 'list', 'watch'],
     'users': ['system:kube-controller-manager', 'system:kube-scheduler', 'system:apiserver']},
    {'level': 'Metadata'},
]
FLAGS = {
    'encryption-provider-config': f'{SECURITY_DIR}/encryption-config.yaml',
    'audit-policy-file': f'{SECURITY_DIR}/audit-policy.yaml',
    'audit-log-path': f'{AUDIT_DIR}/audit.log',
    'audit-log-maxage': '30',
    'audit-log-maxbackup': '10',
    'audit-log-maxsize': '100',
}
VOLUMES = [('apiserver-security', SECURITY_DIR, True),
           ('apiserver-audit', AUDIT_DIR, False)]


def write_private(path, value):
    """同一 filesystem 上で置換し、生成途中のファイルを読ませない。"""
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            yaml.safe_dump(value, stream, sort_keys=False)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def prepare(root):
    directory = root / SECURITY_DIR.lstrip('/')
    logs = root / AUDIT_DIR.lstrip('/')
    for path in [directory, logs]:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.chmod(0o700)
    keyfile = directory / 'encryption-config.yaml'
    if keyfile.exists():
        # 既存鍵を上書きすると保存済み Secret が読めなくなる。破損時も再生成しない。
        config = yaml.safe_load(keyfile.read_text())
        providers = config['resources'][0]['providers']
        if config['resources'][0]['resources'] != ['secrets'] or 'aescbc' not in providers[0]:
            raise ValueError('既存暗号化設定を確認してください（自動変更しません）')
        for key in providers[0]['aescbc']['keys']:
            if len(base64.b64decode(key['secret'], validate=True)) != 32:
                raise ValueError('既存暗号鍵の長さが不正です')
        if not providers[0]['aescbc']['keys']:
            raise ValueError('既存暗号鍵がありません')
        keyfile.chmod(0o600)
    else:
        config = {'apiVersion': 'apiserver.config.k8s.io/v1',
                  'kind': 'EncryptionConfiguration', 'resources': [{
                      'resources': ['secrets'], 'providers': [
                          {'aescbc': {'keys': [{'name': 'key1', 'secret': base64.b64encode(secrets.token_bytes(32)).decode()}]}},
                          {'identity': {}}]}]}
        write_private(keyfile, config)
    # 本文・レスポンスを一切保存しない。Secret と TokenRequest の内容も対象外。
    write_private(directory / 'audit-policy.yaml', {
        'apiVersion': 'audit.k8s.io/v1', 'kind': 'Policy',
        'omitStages': ['RequestReceived'], 'rules': AUDIT_RULES})


def merge_named(existing, additions):
    names = {item['name'] for item in additions}
    return [item for item in existing if item['name'] not in names] + additions


def configure_kubeadm(documents):
    clusters = [doc for doc in documents if doc and doc.get('kind') == 'ClusterConfiguration']
    if len(clusters) != 1 or clusters[0]['apiVersion'] != 'kubeadm.k8s.io/v1beta4':
        raise ValueError('kubeadm v1beta4 ClusterConfiguration が一つ必要です')
    api = clusters[0].setdefault('apiServer', {})
    for arg in api.get('extraArgs', []):
        if arg['name'] == 'encryption-provider-config' and arg['value'] != FLAGS[arg['name']]:
            raise ValueError('既存の暗号化設定パスが異なります。鍵の移行を先に確認してください')
    api['extraArgs'] = merge_named(api.get('extraArgs', []),
                                   [{'name': k, 'value': v} for k, v in FLAGS.items()])
    api['extraVolumes'] = merge_named(api.get('extraVolumes', []), [
        {'name': name, 'hostPath': path, 'mountPath': path,
         'readOnly': readonly, 'pathType': 'Directory'} for name, path, readonly in VOLUMES])
    return documents


def configure_manifest(documents):
    if len(documents) != 1 or documents[0].get('kind') != 'Pod':
        raise ValueError('kube-apiserver static Pod manifest が必要です')
    spec = documents[0]['spec']
    container = next(c for c in spec['containers'] if c['name'] == 'kube-apiserver')
    # kubeadm が生成する --key=value 形式のみ受け付け、二重引数を防ぐ。
    command = container['command']
    for arg in command:
        if arg.startswith('--encryption-provider-config=') and arg.split('=', 1)[1] != FLAGS['encryption-provider-config']:
            raise ValueError('既存の暗号化設定パスが異なります。鍵の移行を先に確認してください')
    if any(f'--{key}' in command for key in FLAGS):
        raise ValueError('空白区切りの引数は先に --key=value へ統一してください')
    container['command'] = [arg for arg in command if arg.split('=')[0].removeprefix('--') not in FLAGS]
    container['command'] += [f'--{key}={value}' for key, value in FLAGS.items()]
    container['volumeMounts'] = merge_named(container.get('volumeMounts', []), [
        {'name': name, 'mountPath': path, 'readOnly': readonly} for name, path, readonly in VOLUMES])
    spec['volumes'] = merge_named(spec.get('volumes', []), [
        {'name': name, 'hostPath': {'path': path, 'type': 'Directory'}} for name, path, _ in VOLUMES])
    return documents


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['prepare', 'kubeadm', 'manifest'])
    parser.add_argument('--root', type=Path, default=Path('/'))
    parser.add_argument('--input', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    if args.mode == 'prepare':
        prepare(args.root)
        return
    if not args.input or not args.output:
        parser.error('--input と --output が必要です')
    documents = list(yaml.safe_load_all(args.input.read_text()))
    result = configure_kubeadm(documents) if args.mode == 'kubeadm' else configure_manifest(documents)
    # 出力先は static Pod 監視ディレクトリ外でレビューしてから配置する。
    with args.output.open('w') as stream:
        yaml.safe_dump_all(result, stream, sort_keys=False)
    args.output.chmod(0o600)


if __name__ == '__main__':
    main()
