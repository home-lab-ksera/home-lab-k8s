"""重要な許可／拒否経路を、全ポリシーの和集合で回帰検証する。"""
import ipaddress
from pathlib import Path
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[3]
POLICIES = list(yaml.safe_load_all((ROOT / 'manifests/core/workload-networkpolicies.yaml').read_text()))
for policy_file in ['manifests/core/application-networkpolicies.yaml',
                    'manifests/core/platform-networkpolicies.yaml',
                    'manifests/platform/observability/logging-networkpolicy.yaml',
                    'manifests/platform/shared-postgresql/application-networkpolicy.yaml']:
    POLICIES.extend(yaml.safe_load_all((ROOT / policy_file).read_text()))


def matches(selector, labels):
    return all(labels.get(k) == v for k, v in selector.get('matchLabels', {}).items()) and all(
        labels.get(expr['key']) in expr['values'] for expr in selector.get('matchExpressions', []))


def port_matches(rule, port, protocol):
    # ports を省略した rule は全 port・全 protocol を許可する。
    if 'ports' not in rule:
        return True
    return any(p.get('protocol', 'TCP') == protocol and p['port'] <= port <= p.get('endPort', p['port'])
               for p in rule['ports'])


def allowed(namespace, labels, direction, remote_ns, remote_labels, ip, port, protocol='TCP'):
    selected = [p['spec'] for p in POLICIES if p['metadata']['namespace'] == namespace
                and matches(p['spec']['podSelector'], labels) and direction in p['spec']['policyTypes']]
    if not selected:
        return True
    for spec in selected:
        for rule in spec.get(direction.lower(), []):
            if not port_matches(rule, port, protocol):
                continue
            # to / from を省略した rule は全送信元・全宛先を許可する。
            peers = rule.get('to' if direction == 'Egress' else 'from')
            if peers is None:
                return True
            for peer in peers:
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


GATEWAY = {'app.kubernetes.io/name': 'nginx-gateway-nginx', 'gateway.networking.k8s.io/gateway-name': 'nginx-gateway'}
VM = {'app.kubernetes.io/name': 'victoria-metrics'}
UNRELATED = {'app': 'unrelated'}
INTERNET = '1.1.1.1'


class PlatformNetworkPolicyTests(unittest.TestCase):
    """基盤 namespace の default-deny と、壊すと監視・公開・証明書が止まる経路。"""

    def test_every_platform_namespace_denies_by_default(self):
        for namespace in ['observability', 'cert-manager', 'external-secrets-system', 'nginx-gateway', 'tailscale']:
            for direction in ['Ingress', 'Egress']:
                self.assertFalse(allowed(namespace, UNRELATED, direction, 'sandbox', UNRELATED, '10.244.1.2', 8080),
                                 f'{namespace} {direction}')
            # DNS はどの Pod にも許可する。
            self.assertTrue(allowed(namespace, UNRELATED, 'Egress', 'kube-system', {'k8s-app': 'kube-dns'},
                                    '10.244.0.5', 53, 'UDP'))

    def test_victoria_metrics_accepts_only_known_writers_and_readers(self):
        for remote_ns, labels in [('observability', {'app.kubernetes.io/name': 'alloy'}),
                                  ('observability', {'app.kubernetes.io/name': 'grafana'}),
                                  ('observability', {'app.kubernetes.io/name': 'vmalert'}),
                                  ('observability', {'app.kubernetes.io/name': 'weekly-observability-report'}),
                                  ('harbor', {'app': 'harbor-maintenance'}),
                                  ('rustfs', {'app.kubernetes.io/name': 'rustfs-scanner-monitor'}),
                                  ('nginx-gateway', GATEWAY)]:
            self.assertTrue(allowed('observability', VM, 'Ingress', remote_ns, labels, '10.244.1.2', 8428), labels)
        self.assertFalse(allowed('observability', VM, 'Ingress', 'sandbox', UNRELATED, '10.244.1.2', 8428))
        self.assertFalse(allowed('observability', VM, 'Ingress', 'harbor', {'app': 'harbor'}, '10.244.1.2', 8428))

    def test_victoria_metrics_scrapes_pods_nodes_and_host(self):
        for port in [3000, 8080, 9090, 9113, 9402]:
            self.assertTrue(allowed('observability', VM, 'Egress', 'sandbox', UNRELATED, '10.244.1.2', port))
        self.assertFalse(allowed('observability', VM, 'Egress', 'database', UNRELATED, '10.244.1.2', 5432))
        for node in ['192.168.122.10', '192.168.122.12']:
            for port in [10250, 9080]:
                self.assertTrue(allowed('observability', VM, 'Egress', '', {}, node, port))
        self.assertTrue(allowed('observability', VM, 'Egress', '', {}, '192.168.122.1', 9100))
        self.assertFalse(allowed('observability', VM, 'Egress', '', {}, '192.168.122.10', 22))
        self.assertFalse(allowed('observability', VM, 'Egress', '', {}, INTERNET, 443))

    def test_alerts_reach_slack_but_grafana_stays_inside(self):
        alertmanager = {'app.kubernetes.io/name': 'alertmanager'}
        grafana = {'app.kubernetes.io/name': 'grafana'}
        self.assertTrue(allowed('observability', {'app.kubernetes.io/name': 'vmalert'}, 'Egress', 'observability',
                                alertmanager, '10.244.1.2', 9093))
        self.assertTrue(allowed('observability', alertmanager, 'Ingress', 'observability',
                                {'app.kubernetes.io/name': 'vmalert'}, '10.244.1.2', 9093))
        self.assertTrue(allowed('observability', alertmanager, 'Egress', '', {}, INTERNET, 443))
        self.assertFalse(allowed('observability', alertmanager, 'Egress', '', {}, '192.168.10.2', 443))
        self.assertFalse(allowed('observability', grafana, 'Egress', '', {}, INTERNET, 443))
        self.assertTrue(allowed('observability', grafana, 'Egress', 'database',
                                {'postgres-operator.crunchydata.com/cluster': 'shared-postgresql'}, '10.244.1.2', 5432))
        self.assertTrue(allowed('observability', grafana, 'Ingress', 'nginx-gateway', GATEWAY, '10.244.1.2', 3000))
        self.assertFalse(allowed('observability', grafana, 'Ingress', 'sandbox', UNRELATED, '10.244.1.2', 3000))

    def test_alloy_probes_gateway_split_dns_and_apiserver(self):
        alloy = {'app.kubernetes.io/name': 'alloy'}
        for address in ['192.168.122.100', '10.244.2.2']:
            self.assertTrue(allowed('observability', alloy, 'Egress', 'nginx-gateway', GATEWAY, address, 443))
        for address in ['192.168.122.101', '10.244.2.3']:
            self.assertTrue(allowed('observability', alloy, 'Egress', 'tailscale', {'app': 'tailscale-split-dns'},
                                    address, 53, 'UDP'))
        self.assertTrue(allowed('observability', alloy, 'Egress', '', {}, '10.96.0.1', 443))
        self.assertTrue(allowed('observability', alloy, 'Egress', '', {}, '192.168.122.10', 6443))
        self.assertTrue(allowed('observability', {'app.kubernetes.io/name': 'loki'}, 'Ingress', 'observability',
                                alloy, '10.244.1.2', 3100))

    def test_cert_manager_reaches_acme_and_only_the_apiserver_calls_the_webhook(self):
        controller = {'app.kubernetes.io/name': 'cert-manager', 'app.kubernetes.io/component': 'controller'}
        webhook = {'app.kubernetes.io/name': 'webhook', 'app.kubernetes.io/component': 'webhook'}
        self.assertTrue(allowed('cert-manager', controller, 'Egress', '', {}, INTERNET, 443))
        # DNS01 の伝播確認は DoH のため、外部の 53 番は開けない。
        self.assertFalse(allowed('cert-manager', controller, 'Egress', '', {}, INTERNET, 53, 'UDP'))
        self.assertFalse(allowed('cert-manager', webhook, 'Egress', '', {}, INTERNET, 443))
        # 別ノードの API サーバーからは control-plane の flannel.1 が送信元になる。
        self.assertTrue(allowed('cert-manager', webhook, 'Ingress', '', {}, '10.244.0.0', 10250))
        self.assertFalse(allowed('cert-manager', webhook, 'Ingress', 'sandbox', UNRELATED, '10.244.1.2', 10250))
        self.assertTrue(allowed('cert-manager', webhook, 'Ingress', 'observability', VM, '10.244.1.2', 9402))

    def test_external_secrets_reaches_pulumi_and_the_apiserver_only(self):
        operator = {'app.kubernetes.io/name': 'external-secrets'}
        self.assertTrue(allowed('external-secrets-system', operator, 'Egress', '', {}, INTERNET, 443))
        self.assertTrue(allowed('external-secrets-system', operator, 'Egress', '', {}, '192.168.122.10', 6443))
        self.assertFalse(allowed('external-secrets-system', operator, 'Egress', 'database', UNRELATED, '10.244.1.2', 5432))
        self.assertFalse(allowed('external-secrets-system', operator, 'Ingress', 'sandbox', UNRELATED, '10.244.1.2', 8080))

    def test_gateway_serves_everyone_and_reaches_only_route_backends(self):
        for source in ['192.168.10.9', '192.168.122.1', '10.244.0.0']:
            for port in [80, 443]:
                self.assertTrue(allowed('nginx-gateway', GATEWAY, 'Ingress', '', {}, source, port))
        for remote_ns, port in [('sandbox', 80), ('sandbox', 1200), ('sandbox', 3000), ('sandbox', 8080),
                                ('harbor', 8080), ('argocd', 8080), ('observability', 3000),
                                ('observability', 8428), ('rustfs', 9000), ('rustfs', 9001)]:
            self.assertTrue(allowed('nginx-gateway', GATEWAY, 'Egress', remote_ns, UNRELATED, '10.244.1.2', port),
                            (remote_ns, port))
        self.assertFalse(allowed('nginx-gateway', GATEWAY, 'Egress', 'database', UNRELATED, '10.244.1.2', 5432))
        self.assertFalse(allowed('nginx-gateway', GATEWAY, 'Egress', '', {}, INTERNET, 443))
        control = {'app.kubernetes.io/name': 'nginx-gateway-fabric'}
        self.assertTrue(allowed('nginx-gateway', GATEWAY, 'Egress', 'nginx-gateway', control, '10.244.1.2', 8443))
        self.assertTrue(allowed('nginx-gateway', control, 'Ingress', 'nginx-gateway', GATEWAY, '10.244.1.2', 8443))
        self.assertFalse(allowed('nginx-gateway', control, 'Ingress', 'sandbox', UNRELATED, '10.244.1.2', 8443))
        # 製品テレメトリは values で止めており、外部への送信は許可しない。
        self.assertFalse(allowed('nginx-gateway', control, 'Egress', '', {}, INTERNET, 443))

    def test_tailscale_router_reaches_the_subnet_but_not_the_pod_network(self):
        router = {'tailscale.com/managed': 'true', 'tailscale.com/parent-resource': 'k8s-subnet-router',
                  'tailscale.com/parent-resource-type': 'connector'}
        for address, port in [('192.168.122.10', 22), ('192.168.122.1', 9100), ('192.168.122.100', 443)]:
            self.assertTrue(allowed('tailscale', router, 'Egress', '', {}, address, port), address)
        self.assertTrue(allowed('tailscale', router, 'Egress', 'nginx-gateway', GATEWAY, '10.244.2.2', 443))
        self.assertTrue(allowed('tailscale', router, 'Egress', 'tailscale', {'app': 'tailscale-split-dns'},
                                '10.244.2.3', 53, 'UDP'))
        self.assertFalse(allowed('tailscale', router, 'Egress', 'database', UNRELATED, '10.244.1.2', 5432))
        self.assertFalse(allowed('tailscale', router, 'Egress', 'sandbox', {'app': 'api-hub'}, '10.244.1.2', 3000))
        # Tailscale 自体の通信。自宅 LAN の端末とは UDP で直接つながる。
        self.assertTrue(allowed('tailscale', router, 'Egress', '', {}, INTERNET, 443))
        self.assertTrue(allowed('tailscale', router, 'Egress', '', {}, '192.168.10.9', 41641, 'UDP'))
        self.assertFalse(allowed('tailscale', router, 'Egress', '', {}, '10.244.1.2', 41641, 'UDP'))

    def test_tailscale_ingress_proxy_and_split_dns(self):
        proxy = {'tailscale.com/managed': 'true', 'tailscale.com/parent-resource': 'nature-remo',
                 'tailscale.com/parent-resource-type': 'ingress'}
        self.assertTrue(allowed('tailscale', proxy, 'Egress', 'sandbox', {'app': 'nature-remo'}, '10.244.1.2', 8080))
        self.assertFalse(allowed('tailscale', proxy, 'Egress', 'sandbox', {'app': 'api-hub'}, '10.244.1.2', 3000))
        self.assertFalse(allowed('tailscale', proxy, 'Egress', '', {}, '192.168.122.10', 22))
        split_dns = {'app': 'tailscale-split-dns'}
        for source in ['10.244.2.199', '192.168.122.1']:
            self.assertTrue(allowed('tailscale', split_dns, 'Ingress', '', {}, source, 53, 'UDP'))
        self.assertFalse(allowed('tailscale', split_dns, 'Egress', '', {}, INTERNET, 53, 'UDP'))
        operator = {'app': 'operator'}
        self.assertFalse(allowed('tailscale', operator, 'Egress', '', {}, INTERNET, 41641, 'UDP'))
        self.assertTrue(allowed('tailscale', operator, 'Egress', '', {}, INTERNET, 443))
