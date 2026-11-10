"""配布の成功記録、昇格、標準保持、GC設定を単一CronJobで調停する。"""
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request


class APIError(RuntimeError):
    def __init__(self, method, path, code):
        super().__init__(f'{method} {path}: HTTP {code}')
        self.code = code


class API:
    def __init__(self, url, authorization, context=None):
        self.url, self.authorization, self.context = url, authorization, context

    def request(self, method, path, payload=None):
        req = urllib.request.Request(self.url + path, method=method,
                                     headers={'Authorization': self.authorization, 'Content-Type': 'application/json'},
                                     data=None if payload is None else json.dumps(payload).encode())
        try:
            with urllib.request.urlopen(req, context=self.context, timeout=30) as res:
                body = res.read()
                return json.loads(body) if body else None
        except urllib.error.HTTPError as exc:
            raise APIError(method, path, exc.code) from None

    def manifest_config(self, repository, digest):
        """manifestのconfig digest。圧縮だけ異なる同一imageの判定に使う。indexはNone。"""
        accept = ('application/vnd.oci.image.manifest.v1+json,'
                  'application/vnd.docker.distribution.manifest.v2+json')
        req = urllib.request.Request(self.url.rsplit('/api/', 1)[0] + f'/v2/{repository}/manifests/{digest}',
                                     headers={'Authorization': self.authorization, 'Accept': accept})
        try:
            with urllib.request.urlopen(req, context=self.context, timeout=30) as res:
                return (json.loads(res.read()).get('config') or {}).get('digest')
        except urllib.error.HTTPError as exc:
            raise APIError('GET', f'/v2/{repository}/manifests/{digest}', exc.code) from None

    def pages(self, path):
        result = []
        for page in range(1, 1001):
            rows = self.request('GET', f'{path}{"&" if "?" in path else "?"}page={page}&page_size=100') or []
            result.extend(rows)
            if len(rows) < 100:
                return result
        raise RuntimeError('API pagination limit')


def emit(**fields):
    print(json.dumps(fields, ensure_ascii=False), flush=True)


def image_digest(image, repository):
    if not image.startswith('harbor.qroksera.com/' + repository + ':') and not image.startswith('harbor.qroksera.com/' + repository + '@'):
        return None
    if '@' not in image or not re.fullmatch(r'sha256:[a-f0-9]{64}', image.rsplit('@', 1)[1]):
        raise ValueError('digest固定でないworkload')
    return image.rsplit('@', 1)[1]


def observe(app, items, repository, artifacts, config_of=None):
    """旧実行中Pod/Jobと全templateを照合。indexの実行manifestも検証する。

    containerdは同じconfigのimageに複数のrepoDigestを持ち、imageIDにはその1つしか返さない。
    再pushで圧縮だけ異なるmanifestが増えた場合に備え、digest不一致時はconfig digestで同一性を確認する。
    """
    status = app.get('status', {})
    if (status.get('sync', {}).get('status') != 'Synced'
            or status.get('health', {}).get('status') != 'Healthy'
            or app.get('operation')
            or status.get('operationState', {}).get('phase') in ('Running', 'Terminating')):
        raise ValueError('Application同期・正常稼働待ち')
    roots = {a['digest']: {a['digest']} | {r['child_digest'] for r in a.get('references') or []} for a in artifacts}
    desired, running = set(), set()
    healthy = 0
    for item in items:
        kind, spec, st = item['kind'], item.get('spec', {}), item.get('status', {})
        if kind in ('Pod', 'Job'):
            if kind == 'Pod' and st.get('phase') in ('Succeeded', 'Failed'):
                continue
            if kind == 'Job' and any(c.get('status') == 'True' and c['type'] in ('Complete', 'Failed') for c in st.get('conditions', [])):
                continue
        pod = (spec if kind == 'Pod' else spec['jobTemplate']['spec']['template']['spec'] if kind == 'CronJob'
               else spec.get('template', {}).get('spec', {}))
        matched = {d for c in pod.get('containers', []) + pod.get('initContainers', [])
                   if (d := image_digest(c['image'], repository))}
        if not matched:
            continue
        if kind in ('Deployment', 'StatefulSet', 'DaemonSet', 'CronJob'):
            desired |= matched
        else:
            running |= matched
        if not matched <= roots.keys():
            raise ValueError('workloadが参照するartifactが不明')
        if kind in ('Deployment', 'StatefulSet', 'DaemonSet'):
            n = spec.get('replicas', 1)
            if st.get('observedGeneration', 0) < item['metadata']['generation'] or n < 1:
                raise ValueError('controller反映待ち、またはreplicas=0')
            if kind == 'DaemonSet':
                n = st.get('desiredNumberScheduled', 0)
                ok = n > 0 and st.get('numberReady', 0) == n and st.get('updatedNumberScheduled', 0) == n
            else:
                ok = st.get('readyReplicas', 0) == n and st.get('updatedReplicas', 0) == n and st.get('replicas', 0) == n
                if kind == 'StatefulSet':
                    ok = ok and st.get('currentRevision') == st.get('updateRevision')
            if not ok:
                raise ValueError('workloadの切替完了待ち')
        if kind == 'Pod':
            if item['metadata'].get('deletionTimestamp') or not any(c['type'] == 'Ready' and c['status'] == 'True' for c in st.get('conditions', [])):
                raise ValueError('旧Pod終了またはPod Ready待ち')
            by_name = {c['name']: image_digest(c['image'], repository) for c in pod.get('containers', []) + pod.get('initContainers', [])}
            statuses = st.get('containerStatuses', []) + st.get('initContainerStatuses', [])
            for name, root in by_name.items():
                if root:
                    actual = next((c.get('imageID', '').rsplit('@', 1)[-1] for c in statuses if c['name'] == name), '')
                    if actual not in roots[root] and not same_image(config_of, actual, roots[root]):
                        raise ValueError('実行imageIDがmanifestと不一致')
            healthy += 1
    if len(desired) != 1 or not healthy or not running or running != desired:
        raise ValueError('正常稼働版を一意に確認できない')
    return next(iter(desired))


def same_image(config_of, actual, expected):
    if not config_of or not re.fullmatch(r'sha256:[a-f0-9]{64}', actual or ''):
        return False
    configs = {c for d in expected if (c := config_of(d))}
    return bool(configs) and config_of(actual) in configs


def tags(artifacts):
    return {t['name']: a['digest'] for a in artifacts for t in a.get('tags') or []}


def transitions(existing, healthy, override=None, seed_previous=None):
    """previous→currentの順。途中終了しても直前正常版を失わない。"""
    changes = []
    current = existing.get('retain-current')
    if current != healthy:
        if current:
            changes.append(('retain-previous', current))
        elif seed_previous and not existing.get('retain-previous'):
            changes.append(('retain-previous', seed_previous))
        changes.append(('retain-current', healthy))
    deploy = existing.get('deploy')
    # 未完了deployを追い越さない。rollbackDigestだけは明示的に切替可能。
    if override:
        target = override
    elif deploy is None or deploy == healthy:
        target = existing.get('latest', healthy)
    else:
        target = deploy
    if deploy != target:
        changes.append(('deploy', target))
    return changes


def set_tag(client, path, existing, tag, target):
    if existing.get(tag) == target:
        return
    # target存在確認を先に行う。DELETE/POSTの間に停止しても次回修復可能。
    client.request('GET', f'{path}/{target}')
    if tag in existing:
        client.request('DELETE', f'{path}/{existing[tag]}/tags/{tag}')
    client.request('POST', f'{path}/{target}/tags', {'name': tag})
    existing[tag] = target


def pattern(names):
    return names[0] if len(names) == 1 else '{' + ','.join(sorted(names)) + '}'


def retention_policy(project_id, eligible, archived=()):
    def rule(repo_pattern, decoration, tag_pattern, template='always', params=None):
        return {'action': 'retain', 'template': template, 'params': params or {},
                'tag_selectors': [{'kind': 'doublestar', 'decoration': 'matches', 'pattern': tag_pattern,
                                   'extras': json.dumps({'untagged': tag_pattern == '**'})}],
                'scope_selectors': {'repository': [{'kind': 'doublestar', 'decoration': decoration, 'pattern': repo_pattern}]}}
    rules = [rule('**', 'repoMatches', '**')]
    if eligible:
        p = pattern(eligible)
        rules = [rule(p, 'repoMatches', '{retain-current,retain-previous,deploy,latest}'),
                 # CIがVERSIONをpushしてlatestへ昇格する短い間も保護する。
                 rule(p, 'repoMatches', '**', 'latestPushedK', {'latestPushedK': 1}),
                 rule(p, 'repoExcludes', '**')]
    if archived:
        if not eligible:
            rules = [rule(pattern(list(archived)), 'repoExcludes', '**')]
        else:
            rules[-1] = rule(pattern(eligible + list(archived)), 'repoExcludes', '**')
        rules.append(rule(pattern(list(archived)), 'repoMatches', '**', 'latestPushedK', {'latestPushedK': 3}))
    return {'algorithm': 'or', 'scope': {'level': 'project', 'ref': project_id}, 'trigger': {'kind': ''}, 'rules': rules}


def execute(client, policy_id, dry_run):
    path = f'/retentions/{policy_id}/executions'
    before = {r['id'] for r in client.pages(path)}
    client.request('POST', path, {'dry_run': dry_run})
    deadline = time.monotonic() + 480
    while time.monotonic() < deadline:
        new = [r for r in client.pages(path) if r['id'] not in before]
        if len(new) > 1:
            raise RuntimeError('並行した保持処理を検出')
        if new:
            row = new[0]
            if row['status'] == 'Success':
                emit(execution=row['id'], dry_run=dry_run, status='Success')
                return
            if row['status'] not in ('Pending', 'Running'):
                raise RuntimeError('保持処理失敗: ' + row['status'])
        time.sleep(2)
    raise RuntimeError('保持処理タイムアウト。次回も実行中の間はタグ変更しない')


def reconcile(client, config, applications, items, dry_run=False):
    project = config['project']
    info = client.request('GET', f'/projects/{project}')
    policy_id = int(info.get('metadata', {}).get('retention_id', 0))
    if policy_id and any(e['status'] in ('Pending', 'Running') for e in client.pages(f'/retentions/{policy_id}/executions')):
        raise RuntimeError('前回の保持処理が実行中')
    eligible, blocked, archived = [], [], []
    delete_count = 0
    repos = client.pages(f'/projects/{project}/repositories')
    names = {r['name'].split('/', 1)[1] for r in repos}
    unknown = names - config['repositories'].keys()
    # 未登録repoは保持処理の対象外（全保持）。Job全体は失敗させず、repo単位の状態として通知する。
    statuses = {name: '未登録repo（全保持）' for name in unknown}
    if unknown:
        emit(preserved_unknown=sorted(unknown))
        blocked.extend(sorted(unknown))
    for name, settings in config['repositories'].items():
        path = f'/projects/{project}/repositories/{urllib.parse.quote(name, safe="")}/artifacts'
        artifacts = client.pages(path + '?with_tag=true')
        existing = tags(artifacts)
        try:
            if settings.get('archive'):
                if any(project + '/' + name in json.dumps(i.get('spec', {})) for i in items):
                    raise ValueError('archive対象を参照するworkloadを検出')
                archived.append(name)
                delete_count += max(0, len(artifacts) - 3)
                emit(repository=name, status='archived-latest-3')
                statuses[name] = None
                continue
            if settings.get('preserve'):
                emit(repository=name, status='preserved-by-config')
                statuses[name] = None
                continue
            if not artifacts or any(a.get('type') != 'IMAGE' for a in artifacts):
                raise ValueError('artifactなし、または非IMAGEを検出')
            override = settings.get('rollbackDigest')
            cache = {}
            def config_of(d, repository=project + '/' + name):
                if d not in cache:
                    cache[d] = client.manifest_config(repository, d)
                return cache[d]
            try:
                healthy = observe(applications[settings['application']], items, project + '/' + name, artifacts, config_of)
            except ValueError:
                # 失敗中でも、Gitで明示した既知の正常版へのrollbackは実行する。
                if override and override in (existing.get('retain-current'), existing.get('retain-previous')):
                    if not dry_run:
                        set_tag(client, path, existing, 'deploy', override)
                raise
            changes = transitions(existing, healthy, override, settings.get('initialPrevious'))
            if override and override not in (existing.get('retain-current'), existing.get('retain-previous'), healthy):
                raise ValueError('rollbackDigestは正常版のみ指定可能')
            for tag, target in changes:
                if target not in {a['digest'] for a in artifacts}:
                    raise ValueError('役割の対象artifactが存在しない')
                if dry_run:
                    existing[tag] = target
                else:
                    set_tag(client, path, existing, tag, target)
            keep = {existing[t] for t in ('retain-current', 'retain-previous', 'deploy', 'latest') if t in existing}
            newest = max(artifacts, key=lambda a: a['push_time'])['digest']
            keep.add(newest)
            if len(keep) > 3:
                raise ValueError('公開・配布待ちで3世代超過、今回は全保持')
            eligible.append(name)
            delete_count += len(artifacts) - len(keep)
            emit(repository=name, healthy=healthy, roles=existing, keep=sorted(keep), status='eligible')
            statuses[name] = None
        except (ValueError, KeyError) as exc:
            blocked.append(name)
            statuses[name] = str(exc)
            emit(repository=name, status='blocked', reason=str(exc))
    policy = retention_policy(info['project_id'], eligible, archived)
    if not dry_run:
        if policy_id:
            client.request('PUT', f'/retentions/{policy_id}', policy)
        else:
            client.request('POST', '/retentions', policy)
            policy_id = int(client.request('GET', f'/projects/{project}')['metadata']['retention_id'])
        # 不要な実行履歴・Jobを毎回作らず、削除候補がある回だけ標準保持を実行する。
        if delete_count:
            execute(client, policy_id, True)
            execute(client, policy_id, False)
    emit(eligible=eligible, blocked=blocked, delete_candidates=delete_count, dry_run=dry_run)
    return statuses


@contextmanager
def lock(kube):
    # resourceVersion付き更新で、手動Jobを含む二重起動も排他する。
    path = '/apis/coordination.k8s.io/v1/namespaces/harbor/leases/harbor-maintenance'
    try:
        lease = kube.request('GET', path)
    except APIError as exc:
        if exc.code != 404:
            raise
        # ArgoCDはLeaseを既定で追跡対象外にする。初回だけ実行時に作成する。
        lease = kube.request('POST', path.rsplit('/', 1)[0], {
            'apiVersion': 'coordination.k8s.io/v1', 'kind': 'Lease',
            'metadata': {'name': 'harbor-maintenance', 'namespace': 'harbor'}})
    now = datetime.now(timezone.utc)
    state = lease.get('spec', {})
    renew = state.get('renewTime')
    if state.get('holderIdentity') and renew and (now - datetime.fromisoformat(renew.replace('Z', '+00:00'))).total_seconds() < state.get('leaseDurationSeconds', 1000):
        raise RuntimeError('他の保守Jobが実行中、または中断後のLease期限待ち')
    lease['spec'] = {'holderIdentity': os.environ['HOSTNAME'], 'leaseDurationSeconds': 1000,
                     'renewTime': now.isoformat(timespec='microseconds').replace('+00:00', 'Z')}
    lease = kube.request('PUT', path, lease)
    try:
        yield
    finally:
        lease['spec']['holderIdentity'] = ''
        kube.request('PUT', path, lease)


METRICS_URL = 'http://victoria-metrics.observability.svc:8428/api/v1/import/prometheus'


def label(value):
    return value.replace('\\', '\\\\').replace('"', '\\"').replace('\n', ' ')


def metrics(statuses, now):
    """repo単位の状態。blockedは1、正常・保持設定は0。reasonはblocked時のみ。"""
    lines = []
    for name, reason in sorted(statuses.items()):
        lines.append(f'harbor_maintenance_repository_blocked{{repository="{label(name)}",reason="{label(reason or "")}"}} '
                     f'{1 if reason else 0}')
    lines.append(f'harbor_maintenance_last_run_timestamp_seconds {int(now)}')
    return '\n'.join(lines) + '\n'


def push_metrics(body):
    # 送信失敗はJob失敗にし、HarborMaintenanceStalledで検知する。
    req = urllib.request.Request(METRICS_URL, method='POST', data=body.encode(), headers={'Content-Type': 'text/plain'})
    with urllib.request.urlopen(req, timeout=30) as res:
        if res.status >= 300:
            raise RuntimeError(f'メトリクス送信失敗: HTTP {res.status}')


def main():
    config = json.loads(Path('/config/maintenance.json').read_text())
    password = Path('/auth/password').read_text().strip()
    client = API('http://harbor-core.harbor.svc/api/v2.0', 'Basic ' + base64.b64encode(('admin:' + password).encode()).decode())
    sa = Path('/var/run/secrets/kubernetes.io/serviceaccount')
    kube = API('https://kubernetes.default.svc', 'Bearer ' + (sa / 'token').read_text(), ssl.create_default_context(cafile=str(sa / 'ca.crt')))
    with lock(kube):
        run(client, kube, config)


def configure_gc(client):
    # GCのuntagged削除は使わない。配布保護は保持処理だけが判断する。
    schedule = {'schedule': {'type': 'Custom', 'cron': '0 30 18 * * *'},
                'parameters': {'delete_untagged': False, 'dry_run': False, 'workers': 1}}
    current = client.request('GET', '/system/gc/schedule')
    gc_params = json.loads(current.get('job_parameters') or '{}') if current else {}
    if (not current or current.get('schedule', {}).get('cron') != schedule['schedule']['cron']
            or any(gc_params.get(k, False) != v for k, v in schedule['parameters'].items())):
        client.request('PUT' if current else 'POST', '/system/gc/schedule', schedule)


def run(client, kube, config):
    updater = kube.request('GET', '/apis/argocd-image-updater.argoproj.io/v1alpha1/namespaces/argocd/imageupdaters/sandbox-images')
    watched = {i['imageName'] for a in updater['spec']['applicationRefs'] for i in a['images']}
    expected = {'harbor.qroksera.com/sandbox/' + n + ':deploy' for n, s in config['repositories'].items() if 'application' in s}
    if watched != expected:
        raise RuntimeError('Image Updaterのdeployタグ追従への切替待ち')
    apps = kube.request('GET', '/apis/argoproj.io/v1alpha1/namespaces/argocd/applications')['items']
    items = []
    for group, kinds in [('apps/v1', ('deployments', 'statefulsets', 'daemonsets')), ('batch/v1', ('cronjobs', 'jobs')), ('v1', ('pods',))]:
        for kind in kinds:
            prefix = '/api/' if group == 'v1' else '/apis/'
            rows = kube.request('GET', prefix + group + '/namespaces/sandbox/' + kind)['items']
            singular = {'deployments': 'Deployment', 'statefulsets': 'StatefulSet', 'daemonsets': 'DaemonSet',
                        'cronjobs': 'CronJob', 'jobs': 'Job', 'pods': 'Pod'}[kind]
            for row in rows:
                row['kind'] = singular
            items.extend(rows)
    configure_gc(client)
    # repoごとのblockedはJobを失敗させない。Job失敗はHarbor全体の保守（API・保持・GC・quota）の異常に限る。
    statuses = reconcile(client, config, {a['metadata']['name']: a for a in apps}, items)
    ok = True
    info = client.request('GET', '/projects/' + config['project'])
    quotas = client.request('GET', '/quotas?reference=project&reference_id=' + str(info['project_id']))
    if len(quotas) != 1:
        raise RuntimeError('project quotaを一意に確認できない')
    quota = quotas[0]
    limit = quota.get('hard', {}).get('storage', -1)
    if limit < 0 or limit > 4 * 1024 ** 3:
        limit = 4 * 1024 ** 3
        client.request('PUT', '/quotas/' + str(quota['id']), {'hard': {'storage': limit}})
    used = quota.get('used', {}).get('storage', 0)
    emit(quota_bytes=limit, used_bytes=used)
    if used >= limit * 0.9:
        emit(status='failed', reason='project quotaの90%以上を使用中')
        ok = False
    history = client.request('GET', '/system/gc?page=1&page_size=10') or []
    successful = [g for g in history if g.get('job_status') == 'Success' and not json.loads(g.get('job_parameters') or '{}').get('dry_run')]
    if not successful or (datetime.now(timezone.utc) - datetime.fromisoformat(successful[0]['update_time'].replace('Z', '+00:00'))).total_seconds() > 36 * 3600:
        emit(status='failed', reason='GCが36時間以上成功していない')
        ok = False
    push_metrics(metrics(statuses, time.time()))
    if not ok:
        raise SystemExit(1)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        emit(status='failed', error=str(error))
        raise SystemExit(1) from None
