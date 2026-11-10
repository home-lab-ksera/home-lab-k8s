"""重要な許可／拒否経路を、全ポリシーの和集合で回帰検証する。"""
import ipaddress
from pathlib import Path
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[3]
POLICIES = list(yaml.safe_load_all((ROOT / 'manifests/core/workload-networkpolicies.yaml').read_text()))
for policy_file in ['manifests/core/application-networkpolicies.yaml',
                    'manifests/platform/shared-postgresql/application-networkpolicy.yaml']:
    POLICIES.extend(yaml.safe_load_all((ROOT / policy_file).read_text()))


def matches(selector, labels):
    return all(labels.get(k) == v for k, v in selector.get('matchLabels', {}).items()) and all(
        labels.get(expr['key']) in expr['values'] for expr in selector.get('matchExpressions', []))


def allowed(namespace, labels, direction, remote_ns, remote_labels, ip, port, protocol='TCP'):
    selected = [p['spec'] for p in POLICIES if p['metadata']['namespace'] == namespace
                and matches(p['spec']['podSelector'], labels) and direction in p['spec']['policyTypes']]
    if not selected:
        return True
    for spec in selected:
        for rule in spec.get(direction.lower(), []):
            if not any(p.get('protocol', 'TCP') == protocol and p['port'] <= port <= p.get('endPort', p['port'])
                       for p in rule['ports']):
                continue
            for peer in rule['to' if direction == 'Egress' else 'from']:
                if 'ipBlock' in peer:
                    block = peer['ipBlock']
                    address = ipaddress.ip_address(ip)
                    if address in ipaddress.ip_network(block['cidr']) and not any(
                            address in ipaddress.ip_network(cidr) for cidr in block.get('except', [])):
                        return True
                elif matches(peer.get('namespaceSelector', {'matchLabels': {'kubernetes.io/metadata.name': namespace}}),
                             {'kubernetes.io/metadata.name': remote_ns}) and matches(peer.get('podSelector', {}), remote_labels):
                    return True
    return False


class NetworkPolicyTests(unittest.TestCase):
    def test_queue_metrics_require_the_monitoring_collector(self):
        for app, port in [('api-hub', 3000), ('media-api', 3000), ('hitomi-outbox', 9090)]:
            self.assertTrue(allowed('sandbox', {'app': app}, 'Ingress', 'observability',
                                    {'app.kubernetes.io/name': 'victoria-metrics'}, '10.244.1.2', port))
            self.assertFalse(allowed('sandbox', {'app': app}, 'Ingress', 'observability',
                                     {'app.kubernetes.io/name': 'grafana'}, '10.244.1.2', port))
            self.assertFalse(allowed('sandbox', {'app': app}, 'Ingress', 'sandbox',
                                     {'app.kubernetes.io/name': 'victoria-metrics'}, '10.244.1.2', port))

    def test_application_clients_require_the_database_opt_in_label(self):
        database = {'postgres-operator.crunchydata.com/cluster': 'shared-postgresql'}
        client = {'app': 'media-api', 'database.k8s-myhome.local/shared-postgresql': 'true'}
        self.assertTrue(allowed('sandbox', client, 'Egress', 'database', database, '10.244.1.2', 5432))
        self.assertTrue(allowed('database', database, 'Ingress', 'sandbox', client, '10.244.1.3', 5432))
        self.assertFalse(allowed('sandbox', {'app': 'media-api'}, 'Egress', 'database', database, '10.244.1.2', 5432))
        self.assertFalse(allowed('database', database, 'Ingress', 'sandbox', {'app': 'media-api'}, '10.244.1.3', 5432))

    def test_new_internal_api_clients_and_camera_metrics(self):
        for app in ['hitomi-monitor', 'hitomi-outbox', 'media-api']:
            self.assertTrue(allowed('sandbox', {'app': app}, 'Egress', 'sandbox', {'app': 'api-hub'}, '10.244.1.2', 3000))
            self.assertTrue(allowed('sandbox', {'app': 'api-hub'}, 'Ingress', 'sandbox', {'app': app}, '10.244.1.3', 3000))
        self.assertTrue(allowed('sandbox', {'app': 'home-camera-recorder'}, 'Ingress', 'observability',
                                {'app.kubernetes.io/name': 'victoria-metrics'}, '10.244.1.2', 9090))
        self.assertFalse(allowed('sandbox', {'app': 'home-camera-recorder'}, 'Ingress', 'sandbox',
                                 {'app': 'unrelated'}, '10.244.1.2', 9090))

    def test_hitomi_can_fetch_internal_rss_without_opening_other_lan_ports(self):
        gateway = {'app.kubernetes.io/name': 'nginx-gateway-nginx',
                   'gateway.networking.k8s.io/gateway-name': 'nginx-gateway'}
        for app in ['hitomi-monitor', 'hitomi-pdf-monitor']:
            for address in ['192.168.122.100', '10.244.1.2']:
                self.assertTrue(allowed('sandbox', {'app': app}, 'Egress', 'nginx-gateway',
                                        gateway, address, 443))
            self.assertFalse(allowed('sandbox', {'app': app}, 'Egress', 'nginx-gateway',
                                     gateway, '192.168.122.100', 5432))
        self.assertFalse(allowed('sandbox', {'app': 'unrelated'}, 'Egress', 'nginx-gateway',
                                 gateway, '192.168.122.100', 443))

    def test_unlabelled_apps_cannot_reach_lan_or_internet(self):
        for ns in ['sandbox', 'tools']:
            for ip in ['192.168.122.10', '192.168.10.4', '1.1.1.1']:
                self.assertFalse(allowed(ns, {}, 'Egress', '', {}, ip, 443))

    def test_web_permission_does_not_open_private_network_or_other_ports(self):
        self.assertTrue(allowed('sandbox', {'app': 'api-hub'}, 'Egress', '', {}, '1.1.1.1', 443))
        for ip, port in [('192.168.122.10', 443), ('169.254.169.254', 80), ('1.1.1.1', 22)]:
            self.assertFalse(allowed('sandbox', {'app': 'api-hub'}, 'Egress', '', {}, ip, port))

    def test_cache_requires_correct_client(self):
        for src, dstns, dst, port in [('rsshub', 'sandbox', 'rsshub-redis', 6379)]:
            self.assertTrue(allowed('sandbox', {'app': src}, 'Egress', dstns, {'app': dst}, '10.244.1.2', port))
            self.assertTrue(allowed(dstns, {'app': dst}, 'Ingress', 'sandbox', {'app': src}, '10.244.1.3', port))
            self.assertFalse(allowed(dstns, {'app': dst}, 'Ingress', 'sandbox', {'app': 'unrelated'}, '10.244.1.3', port))

    def test_webdriver_is_pod_local_and_browser_keeps_web_egress(self):
        # 同一 Pod の loopback はポリシー対象外。他 Pod からの WebDriver は許可しない。
        for client in ['unrelated', 'hitomi-monitor']:
            self.assertFalse(allowed('sandbox', {'app': 'hitomi-monitor'}, 'Ingress',
                                     'sandbox', {'app': client}, '10.244.1.3', 4444))
        self.assertFalse(allowed('sandbox', {'app': 'hitomi-monitor'}, 'Egress',
                                 'tools', {'app': 'selenium-standalone-chrome'}, '10.244.1.2', 4444))
        for port in [80, 443]:
            self.assertTrue(allowed('sandbox', {'app': 'hitomi-monitor'}, 'Egress', '', {}, '1.1.1.1', port))
        self.assertFalse(allowed('sandbox', {'app': 'hitomi-monitor'}, 'Egress', '', {}, '192.168.10.4', 80))

    def test_hitomi_pdf_can_reach_rsshub_via_gateway_only_on_https(self):
        client = {'app': 'hitomi-pdf-monitor'}
        gateway = {'app.kubernetes.io/name': 'nginx-gateway-nginx',
                   'gateway.networking.k8s.io/gateway-name': 'nginx-gateway'}
        # Gateway VIP（DNAT 前）と Gateway Pod（DNAT 後）の両経路。
        for ns, labels, ip in [('', {}, '192.168.122.100'),
                               ('nginx-gateway', gateway, '10.244.1.250')]:
            self.assertTrue(allowed('sandbox', client, 'Egress', ns, labels, ip, 443))
            for port in [80, 1200, 6443]:
                self.assertFalse(allowed('sandbox', client, 'Egress', ns, labels, ip, port))
            self.assertFalse(allowed('sandbox', {'app': 'unrelated'}, 'Egress', ns, labels, ip, 443))
        for ns, labels, ip in [('sandbox', gateway, '10.244.1.250'),
                               ('nginx-gateway', {}, '10.244.1.250'),
                               ('', {}, '192.168.122.10')]:
            self.assertFalse(allowed('sandbox', client, 'Egress', ns, labels, ip, 443))
        self.assertTrue(allowed('sandbox', {'app': 'rsshub'}, 'Ingress',
                                'nginx-gateway', gateway, '10.244.1.250', 1200))

    def test_harbor_gateway_reaches_route_backends_only(self):
        gateway = {'app.kubernetes.io/name': 'nginx-gateway-nginx',
                   'gateway.networking.k8s.io/gateway-name': 'nginx-gateway'}
        for component in ['core', 'portal']:
            self.assertTrue(allowed('harbor', {'app': 'harbor', 'component': component},
                                    'Ingress', 'nginx-gateway', gateway, '10.244.1.2', 8080))
        for component in ['redis', 'registry', 'jobservice']:
            self.assertFalse(allowed('harbor', {'app': 'harbor', 'component': component},
                                     'Ingress', 'nginx-gateway', gateway, '10.244.1.2', 8080))

    def test_gateway_requires_both_namespace_and_labels(self):
        labels = {'app.kubernetes.io/name': 'nginx-gateway-nginx',
                  'gateway.networking.k8s.io/gateway-name': 'nginx-gateway'}
        self.assertTrue(allowed('sandbox', {'app': 'api-hub'}, 'Ingress', 'nginx-gateway', labels, '10.244.1.2', 3000))
        self.assertFalse(allowed('sandbox', {'app': 'api-hub'}, 'Ingress', 'sandbox', labels, '10.244.1.2', 3000))


if __name__ == '__main__':
    unittest.main()
