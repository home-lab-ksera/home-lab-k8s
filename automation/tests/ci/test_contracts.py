"""公開経路の変異テスト。本番・外部chartへ接続しない。"""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'automation/scripts/ci'))
from contract_inventory import (InventoryError, documents, expand_appset, inside, preflight, run, read_yaml, identity)
from contract_rules import check, ident_dict, load_contracts, hostname_matches, service_fields


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.cluster, self.access = copy.deepcopy(load_contracts(ROOT))
        surface = self.access['spec']['surfaces']['api-hub-internal']
        self.access['spec']['surfaces'] = {'api-hub-internal': surface}
        profiles = {r['profile'] for r in surface['routes']}
        self.access['spec']['profiles'] = {k: v for k, v in self.access['spec']['profiles'].items() if k in profiles}
        self.access['spec']['policies'] = []
        self.cluster['spec']['security'] = {'exceptions': [], 'operatorResources': []}
        paths = ['manifests/access/api-hub/manifest.yaml', 'manifests/apps/api-hub/manifest.yaml',
                 'manifests/access/gateway/gateway.yaml',
                 'manifests/infrastructure/security/cert-manager/internal-certificates.yaml',
                 'manifests/infrastructure/security/cert-manager/cert-manager-letsencrypt-cloudflare-issuer.yaml',
                 'manifests/access/dns/tailscale/manifest.yaml',
                 'manifests/access/dns/core/coredns-configmap.yaml']
        docs = [d for p in paths for d in documents((ROOT / p).read_text())]
        self.resources = [d for d in docs if d['kind'] in {'HTTPRoute', 'Service', 'Gateway', 'Certificate', 'ClusterIssuer', 'ConfigMap'}]
        self.resources = [d for d in self.resources if d['kind'] != 'Certificate' or d['metadata']['name'] == surface['tls']['certificate']]
        gw = self.find('Gateway')
        gw['spec']['listeners'] = [l for l in gw['spec']['listeners'] if l['name'] in {'http', surface['gatewayListener']}]
        for l in gw['spec']['listeners']:
            l['allowedRoutes']['namespaces']['selector']['matchExpressions'][0]['values'] = ['sandbox']
        for d in self.resources:
            if d['kind'] == 'ConfigMap':
                d['data'] = {'Corefile': 'hosts {\n192.168.122.100 api-hub.internal.qroksera.com\n}'}
        dns = self.find('Service', 'tailscale-split-dns')
        self.cluster['spec']['security']['exceptions'] = [{'identity': ident_dict(dns), 'reason': 'DNS', 'fields': service_fields(dns)}]
        self.assertEqual(self.errors(), [])

    def find(self, kind, name=None):
        return next(d for d in self.resources if d['kind'] == kind and (name is None or d['metadata']['name'] == name))

    def errors(self):
        return check(self.cluster, self.access, self.resources)

    def assert_rule(self, rule):
        self.assertTrue(any(s.startswith(rule + ':') for s in self.errors()), self.errors())

    def route(self):
        return self.find('HTTPRoute', 'api-hub-internal')

    def test_annotation_removal(self):
        self.route()['metadata']['annotations'] = {}
        self.assert_rule('CT-001')

    def test_extra_route_even_outside_access_directory(self):
        d = copy.deepcopy(self.route()); d['metadata']['name'] = 'unexpected'
        self.resources.append(d)
        self.assert_rule('CT-001')

    def test_extra_backend_does_not_pass_by_containing_correct_backend(self):
        refs = self.route()['spec']['rules'][0]['backendRefs']
        refs.append({'name': 'exfiltration', 'port': 443, 'weight': 0})
        self.assert_rule('CT-004')

    def test_backend_port_is_service_port(self):
        self.find('Service', 'api-hub')['spec']['ports'][0]['targetPort'] = 9999
        self.assertEqual(self.errors(), [])
        self.route()['spec']['rules'][0]['backendRefs'][0]['port'] = 9999
        self.assert_rule('CT-004')

    def test_cross_namespace_backend(self):
        self.route()['spec']['rules'][0]['backendRefs'][0]['namespace'] = 'database'
        self.assert_rule('CT-008')

    def test_parent_same_section_different_gateway(self):
        self.route()['spec']['parentRefs'][0]['name'] = 'other'
        self.assert_rule('CT-005')

    def test_parent_section_omission(self):
        del self.route()['spec']['parentRefs'][0]['sectionName']
        self.assert_rule('CT-005')

    def test_http_plaintext_forwarding(self):
        d = self.find('HTTPRoute', 'api-hub-internal-redirect')
        d['spec']['rules'] = copy.deepcopy(self.route()['spec']['rules'])
        self.assert_rule('CT-006')

    def test_redirect_offsite(self):
        d = self.find('HTTPRoute', 'api-hub-internal-redirect')
        d['spec']['rules'][0]['filters'][0]['requestRedirect']['hostname'] = 'other.example'
        self.assert_rule('CT-006')

    def test_tls_removed(self):
        del self.find('Gateway')['spec']['listeners'][1]['tls']
        self.assert_rule('CT-007')

    def test_wrong_issuer(self):
        self.find('Certificate')['spec']['issuerRef']['name'] = 'unapproved'
        self.assert_rule('CT-007')

    def test_wildcard_does_not_cover_multiple_labels(self):
        self.assertTrue(hostname_matches('*.qroksera.com', 'blog.qroksera.com'))
        self.assertFalse(hostname_matches('*.qroksera.com', 'blog.internal.qroksera.com'))
        self.find('Certificate')['spec']['dnsNames'] = ['*.qroksera.com']
        self.assert_rule('CT-007')

    def test_all_namespaces(self):
        self.find('Gateway')['spec']['listeners'][1]['allowedRoutes']['namespaces'] = {'from': 'All'}
        self.assert_rule('CT-009')

    def test_extra_namespace(self):
        self.find('Gateway')['spec']['listeners'][1]['allowedRoutes']['namespaces']['selector']['matchExpressions'][0]['values'].append('untrusted')
        self.assert_rule('CT-009')

    def test_nodeport(self):
        self.find('Service', 'api-hub')['spec']['type'] = 'NodePort'
        self.assert_rule('CT-002')

    def test_external_ips(self):
        self.find('Service', 'api-hub')['spec']['externalIPs'] = ['192.0.2.1']
        self.assert_rule('CT-002')

    def test_selectorless_backend(self):
        self.find('Service', 'api-hub')['spec'].pop('selector')
        self.assert_rule('CT-003')

    def test_host_port(self):
        self.resources.append({'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'namespace': 'sandbox', 'name': 'host'}, 'spec': {'containers': [{'name': 'app', 'ports': [{'containerPort': 80, 'hostPort': 80}]}]}})
        self.assert_rule('CT-003')

    def test_mirror(self):
        self.route()['spec']['rules'][0]['filters'] = [{'type': 'RequestMirror', 'requestMirror': {'backendRef': {'name': 'other', 'port': 80}}}]
        self.assert_rule('CT-010')

    def test_dns_duplicate_and_wrong_ip(self):
        d = self.find('ConfigMap', 'coredns')
        d['data']['Corefile'] += '\n192.0.2.1 api-hub.internal.qroksera.com'
        self.assert_rule('CT-011')

    def test_unknown_route_kind(self):
        d = copy.deepcopy(self.route()); d['kind'] = 'TCPRoute'
        self.resources.append(d)
        self.assert_rule('CT-001')

    def test_reference_grant_requires_target_name(self):
        self.resources.append({'apiVersion': 'gateway.networking.k8s.io/v1beta1', 'kind': 'ReferenceGrant', 'metadata': {'name': 'all', 'namespace': 'database'}, 'spec': {'from': [], 'to': [{'group': '', 'kind': 'Service'}]}})
        self.assert_rule('CT-008')

    def test_tailscale_ingress_and_funnel(self):
        _, a = load_contracts(ROOT)
        surface = a['spec']['surfaces']['nature-remo-tailnet']
        self.access['spec']['surfaces']['nature-remo-tailnet'] = surface
        self.resources += [d for d in documents((ROOT / 'manifests/access/nature-remo/manifest.yaml').read_text()) if d['kind'] == 'Ingress']
        self.resources += [d for d in documents((ROOT / 'manifests/apps/nature-remo/manifest.yaml').read_text()) if d['kind'] == 'Service']
        self.assertEqual(self.errors(), [])
        self.find('Ingress')['metadata']['annotations']['tailscale.com/funnel'] = 'true'
        self.assert_rule('CT-012')


class InputTests(unittest.TestCase):
    def test_manifest_list_cannot_hide_public_service(self):
        docs = documents('apiVersion: v1\nkind: List\nitems:\n- apiVersion: v1\n  kind: Service\n  metadata: {name: exposed}\n  spec: {type: NodePort}\n')
        self.assertEqual(docs[0]['kind'], 'Service')
        self.assertEqual(docs[0]['spec']['type'], 'NodePort')

    def test_schema_and_current_contracts(self):
        load_contracts(ROOT)

    def test_duplicate_yaml_rejected_without_value(self):
        with self.assertRaises(InventoryError) as e:
            documents('password: SECRET_VALUE\npassword: OTHER_SECRET\n')
        self.assertNotIn('SECRET', str(e.exception))

    def test_schema_rejects_unknown_fields(self):
        from jsonschema import Draft202012Validator
        c, a = load_contracts(ROOT)
        for name, doc in [('cluster-contract', c), ('access-surfaces', a)]:
            doc['spec']['unrecognised'] = True
            schema = json.loads((ROOT / f'automation/schemas/contracts/{name}.schema.json').read_text())
            self.assertTrue(list(Draft202012Validator(schema).iter_errors(doc)))

    def test_source_symlink_escape(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t); (root / 'escape').symlink_to('/etc/passwd')
            with self.assertRaises(InventoryError): inside(root, 'escape')

    def test_kustomize_plugin_rejected_before_execution(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t); (root / 'kustomization.yaml').write_text('generators: [evil.yaml]\n')
            with self.assertRaises(InventoryError): preflight(root, root)

    def test_kustomize_unknown_remote_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t); (root / 'kustomization.yaml').write_text('resources: [https://example.com/main/base.yaml]\n')
            with self.assertRaises(InventoryError): preflight(root, root)

    def test_timeout_does_not_leak_output(self):
        with patch('contract_inventory.subprocess.run', side_effect=subprocess.TimeoutExpired('helm', 1, output='SECRET')):
            with self.assertRaises(InventoryError) as e: run(['helm'], ROOT)
        self.assertNotIn('SECRET', str(e.exception))

    def test_failure_does_not_leak_stderr(self):
        with patch('contract_inventory.subprocess.run', return_value=subprocess.CompletedProcess('helm', 1, '', 'SECRET')):
            with self.assertRaises(InventoryError) as e: run(['helm'], ROOT)
        self.assertNotIn('SECRET', str(e.exception))

    def test_list_appset_and_unknown_generator(self):
        d = {'kind': 'ApplicationSet', 'metadata': {'name': 'example'},
             'spec': {'goTemplate': True, 'generators': [{'list': {'elements': [{'name': 'one'}]}}],
                      'template': {'metadata': {'name': '{{ .name }}-app'}, 'spec': {}}}}
        apps = list(expand_appset(d)); self.assertEqual(apps[0]['metadata']['name'], 'one-app')
        d['spec']['generators'] = [{'git': {}}]
        with self.assertRaises(InventoryError): list(expand_appset(d))


class ReviewReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('contract_check', ROOT / 'automation/scripts/ci/contract-check.py')
        cls.checker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.checker)

    def test_base_requires_full_commit_sha(self):
        with self.assertRaises(InventoryError), patch.object(self.checker.subprocess, 'run') as command:
            self.checker.changes_since(ROOT, 'main', {}, {})
        command.assert_not_called()

    def test_unchanged_contracts_need_no_review(self):
        import yaml
        cluster, access = load_contracts(ROOT)
        results = [subprocess.CompletedProcess([], 0, yaml.safe_dump(d), '') for d in (cluster, access)]
        results.append(subprocess.CompletedProcess([], 0, '', ''))
        with patch.object(self.checker.subprocess, 'run', side_effect=results):
            self.assertEqual(self.checker.changes_since(ROOT, 'a' * 40, cluster, access), [])

    def test_policy_and_validator_changes_reported_without_values(self):
        import yaml
        cluster, access = load_contracts(ROOT)
        old_cluster, old_access = copy.deepcopy((cluster, access))
        old_cluster['apiVersion'] = 'contracts.k8s-myhome.local/v1alpha1'
        old_cluster['spec']['security'] = {}
        old_access['spec']['surfaces'].pop('nature-remo-tailnet')
        old_access['spec']['profiles']['private-value'] = {'value': 'SECRET_SENTINEL'}
        results = [subprocess.CompletedProcess([], 0, yaml.safe_dump(d), '') for d in (old_cluster, old_access)]
        results.append(subprocess.CompletedProcess([], 0, 'automation/scripts/ci/contract-check.py\n', ''))
        with patch.object(self.checker.subprocess, 'run', side_effect=results):
            changes = self.checker.changes_since(ROOT, 'a' * 40, cluster, access)
        output = '\n'.join(changes)
        for expected in ('schema migration', 'cluster permissions', 'nature-remo-tailnet', 'profiles changed', 'workflow files changed'):
            self.assertIn(expected, output)
        self.assertNotIn('SECRET_SENTINEL', output)


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = 'https://github.com/home-lab-ksera/home-lab-k8s.git'
        self.root_app = self.app('root', {'repoURL': self.repo, 'targetRevision': 'HEAD', 'path': 'bootstrap'})
        self.write('manifests/bootstrap/app-of-apps.yaml', self.root_app)
        (self.root / 'bootstrap').mkdir()

    def write(self, path, doc):
        import yaml
        p = self.root / path; p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(yaml.safe_dump(doc))

    def app(self, name, source):
        return {'apiVersion': 'argoproj.io/v1alpha1', 'kind': 'Application',
                'metadata': {'name': name, 'namespace': 'argocd'},
                'spec': {'source': source, 'destination': {'server': 'https://kubernetes.default.svc', 'namespace': 'sandbox'}}}

    def test_nested_and_directory_include(self):
        from contract_inventory import collect
        child = self.app('child', {'repoURL': self.repo, 'targetRevision': 'HEAD', 'path': 'resources', 'directory': {'include': 'service.yaml'}})
        self.write('resources/service.yaml', {'apiVersion': 'v1', 'kind': 'Service', 'metadata': {'name': 'demo'}, 'spec': {'ports': [{'port': 80}]}})
        with patch('contract_inventory.render_local', return_value=[child]):
            result = collect(self.root)
        self.assertEqual(len(result.sources), 2)
        self.assertIn(('', 'Service', 'sandbox', 'demo'), result.owners)

    def test_duplicate_owner_is_rejected(self):
        from contract_inventory import collect
        d = {'apiVersion': 'v1', 'kind': 'Service', 'metadata': {'name': 'demo'}}
        with patch('contract_inventory.render_local', return_value=[d, d]):
            with self.assertRaises(InventoryError): collect(self.root)

    def test_cycle_is_rejected(self):
        from contract_inventory import collect
        with patch('contract_inventory.render_local', return_value=[self.root_app]):
            with self.assertRaises(InventoryError): collect(self.root)

    def test_unsupported_source_is_not_skipped(self):
        from contract_inventory import collect
        self.root_app['spec']['source']['plugin'] = {'name': 'evil'}
        self.write('manifests/bootstrap/app-of-apps.yaml', self.root_app)
        with self.assertRaises(InventoryError): collect(self.root)

    def test_multisource_and_helm_values_precedence(self):
        from contract_inventory import collect, read_yaml
        child = self.app('chart', {})
        child['spec'].pop('source')
        child['spec']['sources'] = [
            {'repoURL': 'https://charts.example.test', 'chart': 'demo', 'targetRevision': '1.2.3',
             'helm': {'values': 'setting: ignored', 'valuesObject': {'setting': 'object'},
                      'valueFiles': ['$values/config/values.yaml'],
                      'parameters': [{'name': 'setting', 'value': 'parameter', 'forceString': True}]}},
            {'repoURL': self.repo, 'targetRevision': 'HEAD', 'ref': 'values'}]
        self.write('config/values.yaml', {'setting': 'file'})
        def command(args, cwd, env):
            self.assertEqual(args[:3], ['helm', 'template', 'chart'])
            value_indices = [n + 1 for n, value in enumerate(args) if value == '--values']
            self.assertEqual(read_yaml(args[value_indices[-1]]), {'setting': 'object'})
            self.assertEqual(args[-2:], ['--set-string', 'setting=parameter'])
            self.assertIn('no-kubeconfig', env['KUBECONFIG'])
            return 'apiVersion: v1\nkind: Service\nmetadata: {name: chart-service}\n'
        with patch('contract_inventory.render_local', return_value=[child]), patch('contract_inventory.run', side_effect=command):
            result = collect(self.root)
        self.assertEqual(len(result.sources), 2)

    def test_helm_fetch_failure_fails_inventory(self):
        from contract_inventory import collect
        child = self.app('chart', {'repoURL': 'https://charts.example.test', 'chart': 'demo', 'targetRevision': '1.2.3'})
        with patch('contract_inventory.render_local', return_value=[child]), patch('contract_inventory.run', side_effect=InventoryError('download failed')):
            with self.assertRaises(InventoryError): collect(self.root)

    def test_unpinned_chart_is_rejected(self):
        from contract_inventory import collect
        child = self.app('chart', {'repoURL': 'https://charts.example.test', 'chart': 'demo', 'targetRevision': '*'})
        with patch('contract_inventory.render_local', return_value=[child]):
            with self.assertRaises(InventoryError): collect(self.root)


if __name__ == '__main__':
    unittest.main()
