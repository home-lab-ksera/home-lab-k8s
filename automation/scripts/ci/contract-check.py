#!/usr/bin/env python3
"""公開台帳、配備対象、公開拡大の差分を検証する。実クラスタに接続しない。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

from contract_inventory import InventoryError, collect, documents
from contract_rules import check, load_contracts

ROOT = Path(__file__).resolve().parents[3]


def changes_since(root, revision, cluster, access):
    if not re.fullmatch(r'[0-9a-f]{40,64}', revision):
        raise InventoryError('base revision must be a full commit SHA')
    changes = []
    for name, current in [('cluster-contract', cluster), ('access-surfaces', access)]:
        result = subprocess.run(['git', 'show', f'{revision}:manifests/contracts/home-lab/{name}.yaml'],
                                cwd=root, capture_output=True, text=True, timeout=20)
        if result.returncode:
            raise InventoryError('base contract unavailable; fetch the PR base commit')
        previous = documents(result.stdout)[0]
        if previous.get('apiVersion') != current['apiVersion']:
            changes.append(f'{name}: contract schema migration; review complete policy')
        if name == 'access-surfaces':
            before = previous.get('spec', {}).get('surfaces', {})
            after = current['spec']['surfaces']
            for sid in sorted(before.keys() | after.keys()):
                if before.get(sid) != after.get(sid):
                    changes.append(f'access surface changed: {sid}')
            for field in ('profiles', 'policies'):
                if previous.get('spec', {}).get(field) != current['spec'].get(field):
                    changes.append(f'{field} changed: review forwarding/TLS/reference permissions')
        elif previous.get('spec') != current['spec']:
            changes.append('cluster permissions changed: review Service/operator/host access and exceptions')
    result = subprocess.run(['git', 'diff', '--name-only', revision, '--', 'automation/scripts/ci',
                             'automation/schemas/contracts', '.github'], cwd=root, capture_output=True,
                            text=True, timeout=20, check=True)
    if result.stdout.strip():
        changes.append('validation or workflow files changed: review checks and execution permissions')
    return changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--base-ref')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    try:
        cluster, access = load_contracts(args.root)
        inventory = collect(args.root)
        issues = check(cluster, access, inventory.resources)
        changes = changes_since(args.root, args.base_ref, cluster, access) if args.base_ref else []
        report = {'sources': inventory.sources, 'resourceCount': len(inventory.resources),
                  'issues': issues, 'reviewRequired': changes,
                  'runtimeChecks': [{'identity': e['identity'], 'checks': e['runtimeChecks']}
                                    for e in cluster['spec']['security']['operatorResources']]}
        if args.report:
            args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        for issue in issues:
            print('[NG] ' + issue, file=sys.stderr)
        for change in changes:
            print('[REVIEW] ' + change)
        if not issues:
            print(f'[OK] contracts: {len(inventory.sources)} sources, {len(inventory.resources)} resources; '
                  'operator-generated resources require the documented runtime checks')
        return bool(issues)
    except (InventoryError, KeyError, TypeError, ValueError, OSError, subprocess.SubprocessError) as exc:
        # 入力値・コマンド出力を例外のtracebackへ含めない。
        message = str(exc) if isinstance(exc, InventoryError) else type(exc).__name__ + ' (details omitted)'
        print('[NG] contract validation: ' + message, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
