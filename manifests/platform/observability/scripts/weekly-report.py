"""既存の状態メトリクスを集計し、週次レポートを Slack へ送る。"""
import argparse
import datetime as dt
import html
import json
import math
import os
from pathlib import Path
import sys
import time
import urllib.parse
import urllib.request

# bool 比較で正常値も残し、0 件とデータ欠損を区別する。
CHECKS = [
    ('Node Ready 異常', 'node', 'kube_node_status_condition{condition="Ready",status="true"} == bool 0'),
    ('Pod Ready 異常', 'namespace,pod', '(kube_pod_status_ready{condition="true"} == bool 0) and on(namespace,pod,uid) (kube_pod_status_phase{phase=~"Pending|Running|Unknown"} == 1)'),
    ('Deployment replica 不足', 'namespace,deployment', 'kube_deployment_spec_replicas > bool kube_deployment_status_replicas_available'),
    ('PVC Pending / Lost', 'namespace,persistentvolumeclaim', 'kube_persistentvolumeclaim_status_phase{phase=~"Pending|Lost"} == bool 1'),
    ('ArgoCD 同期異常', 'namespace,application', 'kube_argocd_application_sync_status{status=~"OutOfSync|Unknown"} == bool 1'),
    ('ArgoCD health 異常', 'namespace,application', 'kube_argocd_application_health_status{status=~"Degraded|Missing|Unknown"} == bool 1'),
    ('ExternalSecret Ready 異常', 'namespace,externalsecret', 'kube_externalsecret_status_condition{type="Ready"} == bool 0'),
    ('Gateway Programmed 異常', 'namespace,gateway', 'kube_gateway_status_condition{type="Programmed"} == bool 0'),
    ('Certificate Ready 異常', 'namespace,certificate', 'kube_certificate_status_condition{type="Ready"} == bool 0'),
]
# vmalert が書き込む ALERTS 系列。評価間隔（30 秒〜1 分）の短い発火も拾うため 1 分刻みで数える。
# Watchdog は常時発火なので、系列が届いているかの確認だけに使う。
ALERTS = 'sum_over_time((ALERTS{alertstate="firing"} > 0)[1d:1m])'
ALERTS_NOW = 'ALERTS{alertstate="firing"}'
# 対象の表示に使わないラベル。
ALERT_NOISE_LABELS = {'__name__', 'alertname', 'alertstate', 'alertgroup', 'severity', 'cluster',
                      'job', 'instance', 'reason', 'uid', 'container', 'endpoint', 'service'}
SEVERITY_ORDER = {'critical': 0, 'warning': 1}
MAX_NAMES = 5
# 同じ NFS 共有上の StorageClass。
SHARED_STORAGECLASSES = {'nfs-external', 'nfs-postgresql'}
RESTARTS = 'topk(5, sum by(namespace,pod,container) (increase(kube_pod_container_status_restarts_total[7d])))'

PVC_INFO = 'max by(namespace,persistentvolumeclaim,storageclass) (kube_persistentvolumeclaim_info)'
PVC_STATS = {
    'available': 'min by(namespace,persistentvolumeclaim) (kubelet_volume_stats_available_bytes)',
    'capacity': 'min by(namespace,persistentvolumeclaim) (kubelet_volume_stats_capacity_bytes)',
    'used': 'max by(namespace,persistentvolumeclaim) (kubelet_volume_stats_used_bytes)',
}


def pvc_report(fetch):
    info = fetch(PVC_INFO)
    stats = {}
    for kind, expr in PVC_STATS.items():
        stats[kind] = {(r['metric']['namespace'], r['metric']['persistentvolumeclaim']):
                       float(r['value'][1]) for r in fetch(expr)}
    lines = ['*ストレージ容量（現在・空き割合の少ない順）*']
    shared = []
    shared_total = 0
    entries = []
    local = []
    for row in info:
        labels = row['metric']
        key = (labels['namespace'], labels['persistentvolumeclaim'])
        is_shared = labels.get('storageclass') in SHARED_STORAGECLASSES
        if is_shared:
            shared_total += 1
        name = html.escape('/'.join(key))
        if labels.get('storageclass') == 'local-path':
            # kubelet は local-path の容量を報告しない。空きはノードのディスクと同じ。
            local.append(name)
            continue
        available = stats['available'].get(key)
        capacity = stats['capacity'].get(key)
        used = stats['used'].get(key)
        if available is None or capacity is None or capacity <= 0 or used is None:
            if is_shared:
                continue
            entries.append((2, f'• {name}: 容量取得不可（未マウント／未対応／収集停止の可能性）'))
            continue
        if is_shared:
            shared.append((available, capacity, used))
            continue
        ratio = available / capacity
        badge = '🔴' if ratio < .1 else '⚠' if ratio < .2 else '•'
        entries.append((ratio, f'{badge} {name}: 空き {available / 2**30:.1f} GiB ({ratio:.1%})'
                        f' / 総容量 {capacity / 2**30:.1f} GiB / 使用 {used / 2**30:.1f} GiB'
                        f' ({used / capacity:.1%})'))
    if shared_total:
        if shared:
            # nfs-external は同一共有 FS。空き容量が最小の観測を代表に使い、合算しない。
            available, capacity, used = min(shared, key=lambda values: values[0])
            ratio = available / capacity
            badge = '🔴' if ratio < .1 else '⚠' if ratio < .2 else '•'
            entries.append((ratio, f'{badge} 共有 NFS: 空き {available / 2**30:.1f} GiB ({ratio:.1%})'
                            f' / 総容量 {capacity / 2**30:.1f} GiB / 使用 {used / 2**30:.1f} GiB ({used / capacity:.1%})'
                            f' — 容量取得 {len(shared)}/{shared_total} PVC（Pod が使っていない PVC は取れない）'))
        else:
            entries.append((2, f'• 共有 NFS: 容量取得不可 — 容量取得 0/{shared_total} PVC'))
    if local:
        entries.append((3, f'• local-path {len(local)} PVC: ノードのディスクを使用（{", ".join(sorted(local))}）'))
    lines += [text for _, text in sorted(entries)] if entries else ['• PVC 一覧データなし']
    lines.append('NFS は共有ファイルシステムの容量です。PVC ごとの使用量・上限ではなく、合算できません。'
                 '予約領域等により使用量＋空き容量は総容量と一致しない場合があります。')
    return lines


def query(base, expression, end, timeout=20):
    params = urllib.parse.urlencode({'query': expression, 'time': end.timestamp()})
    with urllib.request.urlopen(base.rstrip('/') + '/api/v1/query?' + params, timeout=timeout) as response:
        data = json.load(response)
    if data.get('status') != 'success' or data.get('data', {}).get('resultType') != 'vector':
        raise ValueError('metrics API の応答が不正')
    rows = data['data']['result']
    if any(not math.isfinite(float(r['value'][1])) for r in rows):
        raise ValueError('有限値でないメトリクス')
    return rows


def summarize(rows, labels=None):
    if not rows:
        return 'データなし'
    text = f'{sum(float(r["value"][1]) > 0 for r in rows)} / {len(rows)} 対象'
    if labels:
        names = sorted('/'.join(r['metric'].get(k, '?') for k in labels.split(','))
                       for r in rows if float(r['value'][1]) > 0)
        if names:
            text += f'（{names_text(names)}）'
    return text


def names_text(names):
    shown = ', '.join(html.escape(n) for n in names[:MAX_NAMES])
    return shown + (f' ほか {len(names) - MAX_NAMES} 件' if len(names) > MAX_NAMES else '')


def alert_target(labels):
    labels = {k: v for k, v in labels.items() if k not in ALERT_NOISE_LABELS}
    if 'owner_name' in labels:
        # CronJob の Job 名は実行ごとに変わるので、CronJob 単位にまとめる。
        labels.pop('job_name', None)
    keys = (['namespace'] if 'namespace' in labels else []) + sorted(k for k in labels if k != 'namespace')
    return '/'.join(labels[k] for k in keys) or '-'


def duration_text(minutes):
    return f'約 {minutes:.0f} 分' if minutes < 60 else f'約 {minutes / 60:.1f} 時間'


def alerts_report(fetch, end):
    # 日ごとに 1 分刻みで発火していた分数を数え、アラート名・対象ごとに合計する。
    merged = {}
    watchdog = False
    for day in range(7):
        for row in fetch(ALERTS, end - dt.timedelta(days=day)):
            labels = row['metric']
            if labels.get('alertname') == 'Watchdog':
                watchdog = True
                continue
            key = (labels.get('alertname', '?'), labels.get('severity', ''), alert_target(labels))
            merged[key] = merged.get(key, 0) + float(row['value'][1])
    firing = {(r['metric'].get('alertname', '?'), alert_target(r['metric']))
              for r in fetch(ALERTS_NOW, end)}
    lines = ['*過去 7 日に発火したアラート（Slack に送らない warning を含む）*']
    if not watchdog:
        return lines + ['⚠ アラートの記録（ALERTS）がありません。vmalert の remote write を確認してください']
    groups = {}
    for (name, severity, target), minutes in merged.items():
        groups.setdefault((name, severity), []).append((target, minutes))
    if not groups:
        return lines + ['• なし']

    def order(item):
        (name, severity), targets = item
        return (SEVERITY_ORDER.get(severity, 2), -sum(m for _, m in targets), name)

    for (name, severity), targets in sorted(groups.items(), key=order):
        targets.sort(key=lambda item: (-item[1], item[0]))
        names = [f'{t}（{duration_text(m)}{"・発火中" if (name, t) in firing else ""}）' for t, m in targets]
        lines.append(f'• {html.escape(name)} [{html.escape(severity or "-")}]: {names_text(names)}')
    return lines


def historical_rows(fetch, labels, expression, end):
    # Podの入れ替わりで増えた全系列を7日分同時展開しない。
    # 各区間は同じ5分刻みで評価し、異常値の最大と観測対象の和集合を取る。
    merged = {}
    names = labels.split(',')
    query_expression = f'max by ({labels}) (max_over_time(({expression})[1d:5m]))'
    for day in range(7):
        rows = fetch(query_expression, end - dt.timedelta(days=day))
        for row in rows:
            key = tuple(row.get('metric', {}).get(name, '') for name in names)
            merged[key] = max(merged.get(key, 0), float(row['value'][1]))
    return [{'metric': dict(zip(names, key)), 'value': [end.timestamp(), str(value)]}
            for key, value in sorted(merged.items())]


def report(fetch, end):
    jst = dt.timezone(dt.timedelta(hours=9))
    start = end - dt.timedelta(days=7)
    lines = ['*Kubernetes 週次レポート / home-lab*',
             f'{start.astimezone(jst):%Y-%m-%d %H:%M} ～ {end.astimezone(jst):%Y-%m-%d %H:%M} JST',
             ]
    errors = []
    try:
        lines.extend(alerts_report(fetch, end))
    except Exception:
        errors.append('アラート履歴')
        lines.append('⚠ アラート履歴: 取得失敗')
    lines.append('*過去 7 日に異常を観測した対象数 / 観測対象数*')
    for title, labels, expr in CHECKS:
        try:
            rows = historical_rows(fetch, labels, expr, end)
            lines.append(f'• {title}: {summarize(rows, labels)}')
        except Exception:
            # URL や認証情報を含み得る例外本文はログ・Slack に出さない。
            errors.append(title)
            lines.append(f'• {title}: 取得失敗')
    lines.append('*コンテナ再起動ランキング（上位 5 件・推定回数）*')
    try:
        rows = fetch(RESTARTS)
        positive = sorted((r for r in rows if float(r['value'][1]) > 0),
                          key=lambda r: float(r['value'][1]), reverse=True)
        for row in positive:
            labels = row['metric']
            name = '/'.join(labels.get(k, '?') for k in ('namespace', 'pod', 'container'))
            lines.append(f'• {html.escape(name)}: 約 {float(row["value"][1]):.1f} 回')
        if not positive:
            lines.append('• 再起動なし' if rows else '• データなし')
    except Exception:
        errors.append('再起動ランキング')
        lines.append('• 取得失敗')
    try:
        lines.extend(pvc_report(fetch))
    except Exception:
        errors.append('PVC 容量')
        lines.append('⚠ PVC 容量: 取得失敗')
    lines.append('状態は 5 分間隔、アラートは 1 分間隔の観測です（継続時間は概算）。短い異常・収集停止中の異常・欠損 condition は検出できません。'
                 '対象数は期間中に観測できたリソース数で、同名 Pod の再作成はまとめます。')
    if errors:
        lines.append('⚠ 一部の取得に失敗したため、不完全なレポートです。')
    return '\n'.join(lines), errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    end = dt.datetime.now(dt.timezone.utc)
    base = os.environ.get('VICTORIA_METRICS_URL', 'http://victoria-metrics:8428')
    # Kubernetesの420秒期限内にSlack送信・終了する余裕を残す。
    deadline = time.monotonic() + 360

    def fetch(expression, at=end):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('report query budget exhausted')
        return query(base, expression, at, timeout=min(20, remaining))

    text, errors = report(fetch, end)
    for title in errors:
        print(f'週次集計の取得失敗: {title}', file=sys.stderr)
    if args.dry_run:
        print(text)
    else:
        try:
            url = Path('/etc/slack/webhook-url').read_text().strip()
            if not url.startswith('https://hooks.slack.com/services/'):
                raise ValueError('invalid webhook')
            request = urllib.request.Request(url, data=json.dumps({'text': text}).encode(),
                                             headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(request, timeout=20) as response:
                if response.read().strip() != b'ok':
                    raise ValueError('Slack rejected message')
        except Exception:
            print('週次レポートの Slack 送信に失敗しました。', file=sys.stderr)
            return 1
        print('週次レポートを Slack に送信しました。')
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
