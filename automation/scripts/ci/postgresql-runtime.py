#!/usr/bin/env python3
"""一時 kind で現行版からの更新とバックアップ・復元を検証する。"""
import argparse
import base64
import copy
import importlib.util
import json
import os
from pathlib import Path
import secrets
import subprocess
import tarfile
import tempfile
import time

import yaml

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location('versions', ROOT / 'automation/scripts/ci/postgresql-versions.py')
VERSIONS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERSIONS)


def run(*args, env=None, data=None, timeout=600):
    return subprocess.check_output(args, input=data, text=True, env=env, timeout=timeout)


def wait_for(description, predicate, timeout=600):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            print(f'PASS: {description}', flush=True)
            return
        time.sleep(5)
    raise TimeoutError(description)


class TestCluster:
    def __init__(self, env):
        self.env = env

    def kubectl(self, *args, data=None):
        return run('kubectl', '--request-timeout=30s', *args, env=self.env, data=data)

    def obj(self, namespace, kind, name):
        return json.loads(self.kubectl('-n', namespace, 'get', kind, name, '-o', 'json'))

    def apply(self, objects):
        self.kubectl('apply', '--server-side', '--force-conflicts', '-f', '-', data=objects)

    def operator(self, root):
        print(f"PGO {VERSIONS.chart_source(root)['version']} を適用", flush=True)
        self.apply(run('kustomize', 'build', '--enable-helm', str(root / 'manifests/platform/pgo'), env=self.env))
        self.kubectl('-n', 'postgres-operator', 'rollout', 'status', 'deployment/pgo', '--timeout=300s')

    def database(self, root, name, source=None):
        print(f"DB を適用: {name}", flush=True)
        cluster = copy.deepcopy(VERSIONS.load(root / 'manifests/platform/shared-postgresql/postgrescluster.yaml'))
        cluster['metadata'] = {'name': name, 'namespace': 'database'}
        spec = cluster['spec']
        for instance in spec['instances']:
            # kind に本番のノード名はないため、ノード固定は外す。
            instance.pop('affinity', None)
            # PriorityClass は manifests/core が配布する。kind にはないため外す。
            instance.pop('priorityClassName', None)
            instance['dataVolumeClaimSpec']['storageClassName'] = 'standard'
            instance['dataVolumeClaimSpec']['resources']['requests']['storage'] = '1Gi'
        for repo in spec['backups']['pgbackrest']['repos']:
            repo.pop('schedules', None)
            claim = repo['volume']['volumeClaimSpec']
            claim['storageClassName'] = 'standard'
            claim['resources']['requests']['storage'] = '1Gi'
        if source:
            spec['dataSource'] = {'postgresCluster': {'clusterName': source, 'repoName': 'repo1'}}
        self.apply(yaml.safe_dump(cluster))
        self.wait_database(name, spec['image'])
        self.wait_images(name, root)
        # Ready な Pod と、PGO によるユーザー/DB作成完了は別の状態。
        required = {db for user in spec['users'] for db in user.get('databases', [])}
        def databases_created():
            actual = self.kubectl('-n', 'database', 'exec', self.database_pod(name), '-c', 'database',
                                  '--', 'psql', '-X', '-U', 'postgres', '-d', 'postgres', '-Atc',
                                  'SELECT datname FROM pg_database;').splitlines()
            return required.issubset(actual)
        wait_for(f'{name}: PGO declared databases created', databases_created)
        # 更新前のクラスタには、その版と対になる bootstrap を適用する。
        sql = (root / 'manifests/platform/shared-postgresql/scripts/bootstrap.sql').read_text()
        self.kubectl('-n', 'database', 'exec', '-i', self.database_pod(name), '-c', 'database',
                     '--', 'psql', '-X', '-U', 'postgres', '-d', 'postgres', '-v', 'ON_ERROR_STOP=1', data=sql)

    def pods(self, name):
        return json.loads(self.kubectl('-n', 'database', 'get', 'pods', '-l',
                                      f'postgres-operator.crunchydata.com/cluster={name}', '-o', 'json'))['items']

    def database_pod(self, name, image=None):
        # instance set が複数あるため、書き込める primary を優先する。
        pods = sorted(self.pods(name), key=lambda p: p['metadata'].get('labels', {}).get(
            'postgres-operator.crunchydata.com/role') != 'master')
        for pod in pods:
            if pod['metadata'].get('deletionTimestamp'):
                continue
            db = next((c for c in pod['spec']['containers'] if c['name'] == 'database'), None)
            if db and (image is None or db['image'] == image) and all(
                    s.get('ready') for s in pod['status'].get('containerStatuses', [])) and pod['status'].get('containerStatuses'):
                return pod['metadata']['name']
        return None

    def wait_images(self, name, root):
        expected = set(VERSIONS.expected(root))

        def reconciled():
            cr = self.obj('database', 'postgrescluster', name)
            if cr.get('status', {}).get('observedGeneration') != cr['metadata']['generation']:
                return False
            pods = [p for p in self.pods(name)
                    if any(o['kind'] == 'StatefulSet' for o in p['metadata'].get('ownerReferences', []))]
            if len(pods) < 2:
                return False
            for pod in pods:
                statuses = pod.get('status', {}).get('containerStatuses', [])
                if pod['metadata'].get('deletionTimestamp') or not statuses or not all(s['ready'] for s in statuses):
                    return False
                if any(c['image'] not in expected for c in pod['spec']['containers']):
                    return False
            return True
        wait_for(f'{name}: DB / repo host の実行イメージが chart と一致', reconciled)

    def wait_database(self, name, image):
        wait_for(f'{name}: DB Pod ready ({image.rsplit(":", 1)[1]})', lambda: self.database_pod(name, image))

    def sql(self, name, sql, database='registry'):
        # SQL 接続には PGO 生成の検証専用 password を使い、コマンドやログに出さない。
        secret = self.obj('database', 'secret', f'{name}-pguser-app')
        password = base64.b64decode(secret['data']['password']).decode()
        command = (f'read -r PGPASSWORD; export PGPASSWORD; exec psql '
                   f'"host={name}-primary.database.svc user=app dbname={database} '
                   'sslmode=verify-full sslrootcert=/pgconf/tls/ca.crt connect_timeout=10" '
                   '-X -At -v ON_ERROR_STOP=1')
        return self.kubectl('-n', 'database', 'exec', '-i', self.database_pod(name), '-c', 'database',
                            '--', 'sh', '-c', command, data=password + '\n' + sql + '\n').strip()

    def ready_sql(self, name):
        def ready():
            try:
                return self.sql(name, 'SELECT 1;') == '1'
            except subprocess.CalledProcessError:
                return False
        wait_for(f'{name}: app / TLS authentication', ready, 180)

    def verify(self, name, major):
        self.ready_sql(name)
        assert self.sql(name, 'SELECT current_user;') == 'app'
        assert self.sql(name, 'SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid();') == 't'
        assert int(self.sql(name, 'SHOW server_version_num;')) // 10000 == major
        assert self.sql(name, 'SELECT count(*),sum(id) FROM public.renovate_smoke;') == '100|5050'
        assert self.sql(name, "SELECT count(*) FROM public.renovate_smoke WHERE value <> md5(id::text);") == '0'
        self.sql(name, "BEGIN; INSERT INTO public.renovate_smoke VALUES(101,md5('101')); ROLLBACK;")
        print(f'PASS: {name}: TLS / 読み書き / 検証データ一致', flush=True)
        spec = VERSIONS.load(ROOT / 'manifests/platform/shared-postgresql/postgrescluster.yaml')['spec']
        databases = next(user['databases'] for user in spec['users'] if user['name'] == 'app')
        for database in databases:
            assert self.sql(name, 'SELECT current_user;', database) == 'app'
            assert self.sql(name, 'SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid();', database) == 't'
            assert self.sql(name, "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database();", database) == 'app'
            assert self.sql(name, "SELECT count(*) FROM pg_database d, LATERAL aclexplode(d.datacl) a WHERE d.datname=current_database() AND a.grantee=0 AND a.privilege_type IN ('CONNECT','TEMPORARY');", database) == '0'
            self.sql(name, 'BEGIN; CREATE TABLE public.application_connection_smoke(id integer); INSERT INTO public.application_connection_smoke VALUES(1); ROLLBACK;', database)
        print(f'PASS: {name}: all app databases / TLS / owner / PUBLIC ACL / DDL', flush=True)

    def backup(self, name):
        identifier = 'ci-' + secrets.token_hex(5)
        self.kubectl('-n', 'database', 'annotate', 'postgrescluster', name, '--overwrite',
                     f'postgres-operator.crunchydata.com/pgbackrest-backup={identifier}')
        def completed():
            state = self.obj('database', 'postgrescluster', name).get('status', {}).get('pgbackrest', {}).get('manualBackup', {})
            if state.get('id') != identifier or not state.get('finished'):
                return False
            assert state.get('succeeded') == 1, f'backup failed: {state}'
            return True
        wait_for(f'{name}: manual full backup ({identifier})', completed)

    def diagnostics(self, output):
        output.mkdir(parents=True, exist_ok=True)
        for namespace in ('database', 'postgres-operator'):
            for resource in ('pods', 'events', 'jobs', 'postgresclusters'):
                result = subprocess.run(['kubectl', '--request-timeout=15s', '-n', namespace, 'get', resource, '-o', 'wide'],
                                        env=self.env, capture_output=True, text=True, timeout=30)
                (output / f'{namespace}-{resource}.log').write_text(result.stdout + result.stderr)
            result = subprocess.run(['kubectl', '--request-timeout=15s', '-n', namespace, 'logs', '--all-containers=true',
                                     '--prefix', '--tail=150', '-l', 'postgres-operator.crunchydata.com/cluster', '--max-log-requests=20'],
                                    env=self.env, capture_output=True, text=True, timeout=60)
            (output / f'{namespace}-containers.log').write_text(result.stdout + result.stderr)

        result = subprocess.run(['kubectl', '--request-timeout=15s', '-n', 'postgres-operator', 'logs',
                                 'deployment/pgo', '--all-containers=true', '--tail=200'],
                                env=self.env, capture_output=True, text=True, timeout=60)
        (output / 'operator.log').write_text(result.stdout + result.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-ref', required=True, help='比較する現行版の git commit / ref')
    parser.add_argument('--logs', type=Path, default=Path('/tmp/postgresql-runtime-logs'))
    args = parser.parse_args()
    VERSIONS.check(ROOT)
    with tempfile.TemporaryDirectory(prefix='postgresql-runtime-') as temporary:
        temp = Path(temporary)
        baseline = temp / 'baseline'
        baseline.mkdir()
        archive = temp / 'baseline.tar'
        subprocess.run(['git', 'archive', '--format=tar', '--output', str(archive), args.base_ref], cwd=ROOT, check=True)
        with tarfile.open(archive) as source:
            source.extractall(baseline, filter='data')
        old = VERSIONS.load(baseline / 'manifests/platform/shared-postgresql/postgrescluster.yaml')['spec']['postgresVersion']
        new = VERSIONS.load(ROOT / 'manifests/platform/shared-postgresql/postgrescluster.yaml')['spec']['postgresVersion']
        assert old == new, 'PostgreSQL メジャー更新はこの CI では扱いません。専用の移行手順が必要です'
        name = 'pgo-ci-' + secrets.token_hex(4)
        # 利用者の kubeconfig / 本番 context を参照・更新しない。
        env = {**os.environ, 'KUBECONFIG': str(temp / 'kubeconfig')}
        cluster = TestCluster(env)
        try:
            print(f'一時クラスタ作成: {name}', flush=True)
            run('kind', 'create', 'cluster', '--name', name, '--kubeconfig', env['KUBECONFIG'], '--wait', '120s', env=env)
            cluster.apply(yaml.safe_dump_all([{'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': n}}
                                              for n in ('database', 'postgres-operator')]))
            cluster.operator(baseline)
            cluster.database(baseline, 'ci-source')
            cluster.ready_sql('ci-source')
            cluster.sql('ci-source', 'CREATE TABLE public.renovate_smoke(id integer PRIMARY KEY, value text NOT NULL); '
                        'INSERT INTO public.renovate_smoke SELECT n,md5(n::text) FROM generate_series(1,100) n;')
            # 同一版の PR でも、再起動を経たデータ保持を実測する。
            cluster.operator(ROOT)
            cluster.database(ROOT, 'ci-source')
            pod = cluster.database_pod('ci-source')
            cluster.kubectl('-n', 'database', 'delete', 'pod', pod, '--wait=true', '--timeout=120s')
            image = VERSIONS.expected(ROOT)[0]
            cluster.wait_database('ci-source', image)
            cluster.verify('ci-source', new)
            cluster.backup('ci-source')
            cluster.database(ROOT, 'ci-restore', source='ci-source')
            cluster.verify('ci-restore', new)
            print('PASS: 現行版 → 更新候補 → 再起動 → full backup → 別クラスタ復元', flush=True)
        finally:
            try:
                cluster.diagnostics(args.logs)
            finally:
                subprocess.run(['kind', 'delete', 'cluster', '--name', name], env=env, check=True, timeout=120)


if __name__ == '__main__':
    main()
