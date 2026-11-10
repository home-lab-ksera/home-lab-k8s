"""公開経路の許可台帳とrender済みリソースを照合する。"""
from __future__ import annotations

import copy
import ipaddress
import json
import re
from collections import defaultdict
from pathlib import Path

from jsonschema import Draft202012Validator

from contract_inventory import InventoryError, identity, label, read_yaml

ANNOTATION = "contracts.k8s-myhome.local/access-surface"
POLICY_KINDS = {"ClientSettingsPolicy", "BackendTLSPolicy", "ReferenceGrant", "AccessPolicy"}
OPERATOR_KINDS = {"NginxProxy", "GatewayClass", "Connector", "PostgresCluster", "AutoscalingRunnerSet"}


def ident_dict(doc):
    return dict(zip(("group", "kind", "namespace", "name"), identity(doc)))


def ident_tuple(value):
    return tuple(value[k] for k in ("group", "kind", "namespace", "name"))


def load_contracts(root):
    result = []
    for name in ("cluster-contract", "access-surfaces"):
        doc = read_yaml(root / f"manifests/contracts/home-lab/{name}.yaml")
        schema = json.loads((root / f"automation/schemas/contracts/{name}.schema.json").read_text())
        errors = list(Draft202012Validator(schema).iter_errors(doc))
        if errors:
            # jsonschemaのmessageは入力値を含むため表示しない。
            paths = ["/".join(map(str, e.absolute_path)) for e in errors[:10]]
            raise InventoryError(f"{name}: schema validation failed at {paths}")
        result.append(doc)
    return result


def normalize_rules(rules, namespace):
    result = copy.deepcopy(rules)
    for rule in result:
        for ref in rule.get("backendRefs", []):
            ref.setdefault("group", "")
            ref.setdefault("kind", "Service")
            ref.setdefault("namespace", namespace)
            ref.setdefault("weight", 1)
    return result


def service_fields(doc):
    spec = doc.get("spec", {})
    return {"type": spec.get("type", "ClusterIP"),
            "externalIPs": spec.get("externalIPs", []),
            "externalName": spec.get("externalName", ""),
            "loadBalancerIP": spec.get("loadBalancerIP", ""),
            "loadBalancerSourceRanges": spec.get("loadBalancerSourceRanges", []),
            "allocateLoadBalancerNodePorts": spec.get("allocateLoadBalancerNodePorts", True),
            "ports": spec.get("ports", []), "selector": spec.get("selector", {}),
            "annotations": doc.get("metadata", {}).get("annotations", {})}


def host_fields(doc):
    """PodSpecを含むCRも走査する。CRDのschemaは呼出側で除外する。"""
    found = []
    def walk(value, path):
        if isinstance(value, dict):
            if value.get("hostNetwork") or value.get("hostPort"):
                found.append({"path": path, **{k: value[k] for k in ("hostNetwork", "hostPort", "hostIP") if k in value}})
            for k, v in value.items():
                walk(v, path + "/" + str(k))
        elif isinstance(value, list):
            for n, v in enumerate(value):
                walk(v, path + "/" + str(n))
    walk(doc.get("spec", {}), "/spec")
    return {"hostAccess": found}


def operator_fields(doc):
    spec = doc.get("spec", {})
    kind = doc["kind"]
    if kind in {"NginxProxy", "GatewayClass", "Connector"}:
        value = copy.deepcopy(spec)
        # image tagの更新は公開権限の変更ではない。
        if kind == "NginxProxy":
            value.get("kubernetes", {}).get("deployment", {}).get("container", {}).pop("image", None)
        return value
    if kind == "PostgresCluster":
        return {k: copy.deepcopy(v) for k, v in spec.items() if k in {"service", "proxy", "instances"}}
    if kind == "AutoscalingRunnerSet":
        return {"template": copy.deepcopy(spec.get("template", {}))}
    return {}


def hostname_matches(pattern, hostname):
    if pattern.startswith("*."):
        return hostname.endswith(pattern[1:]) and len(hostname.split(".")) == len(pattern.split("."))
    return pattern == hostname


def hosts_from_corefile(text):
    text = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
    if re.search(r"(?m)^\s*(rewrite|template|import|file|auto|secondary)\b", text):
        raise InventoryError("unsupported DNS override directive")
    blocks = re.findall(r"\bhosts\s*\{([^{}]*)\}", text)
    if len(blocks) != 1:
        raise InventoryError("expected exactly one inline DNS hosts block")
    outside = re.sub(r"\bhosts\s*\{[^{}]*\}", "", text)
    for line in outside.splitlines():
        words = line.split()
        if words:
            try:
                ipaddress.ip_address(words[0])
            except ValueError:
                continue
            raise InventoryError("DNS address entry outside hosts block")
    found = {}
    for line in blocks[0].splitlines():
        parts = line.split("#", 1)[0].split()
        if len(parts) < 2:
            continue
        try:
            ipaddress.ip_address(parts[0])
        except ValueError:
            continue
        for hostname in parts[1:]:
            if hostname in found:
                raise InventoryError("duplicate hostname in DNS configuration")
            found[hostname] = parts[0]
    return found


def check(cluster, access, resources):
    issues = []
    index = {identity(d): d for d in resources}
    if len(index) != len(resources):
        issues.append("CT-000: duplicate rendered resource identity")
    expected_routes = {}
    expected_gateways = set()
    listeners = defaultdict(lambda: defaultdict(set))
    used_profiles = set()
    referenced_backends = set()
    referenced_certificates = set()
    hostnames = [s["hostname"] for s in access["spec"]["surfaces"].values() if s["type"] == "gateway-http"]
    if len(hostnames) != len(set(hostnames)):
        issues.append("CT-001: duplicate hostname in access contract")

    def fail(code, doc, field):
        issues.append(f"{code}: {label(doc)}: {field}")

    def get(group, kind, namespace, name):
        return index.get((group, kind, namespace, name))

    def grant_exists(from_doc, to_group, to_kind, to_ns, to_name):
        from_group, from_kind, from_ns, _ = identity(from_doc)
        if from_ns == to_ns:
            return True
        for d in resources:
            if d.get("kind") != "ReferenceGrant" or d["metadata"].get("namespace") != to_ns:
                continue
            spec = d["spec"]
            if ({"group": from_group, "kind": from_kind, "namespace": from_ns} in spec.get("from", [])
                    and {"group": to_group, "kind": to_kind, "name": to_name} in spec.get("to", [])):
                return True
        return False

    for sid, surface in access["spec"]["surfaces"].items():
        if surface["type"] == "tailscale-ingress":
            ident = ident_tuple(surface["identity"])
            expected_routes[ident] = sid
            doc = index.get(ident)
            if not doc:
                issues.append(f"CT-012: missing ingress for {sid}")
                continue
            annotations = dict(doc.get("metadata", {}).get("annotations", {}))
            annotations.pop(ANNOTATION, None)
            if doc.get("spec") != surface["spec"] or annotations != surface["annotations"]:
                fail("CT-012", doc, "Ingress spec/annotations differ from contract")
            spec = doc["spec"]
            if spec.get("ingressClassName") != "tailscale" or not spec.get("tls"):
                fail("CT-012", doc, "Tailnet ingress requires tailscale class and TLS")
            if annotations.get("tailscale.com/funnel", "false").lower() != "false":
                fail("CT-012", doc, "Funnel is not allowed on an internal surface")
            for rule in spec.get("rules", []):
                for path in rule.get("http", {}).get("paths", []):
                    backend = path.get("backend", {}).get("service", {})
                    referenced_backends.add((ident[2], backend.get("name"), backend.get("port", {}).get("number")))
            continue
        if "accessPolicy" in surface:
            ref = surface["accessPolicy"]
            policy = get("gateway.nginx.org", "AccessPolicy", ref["namespace"], ref["name"])
            targets = [{"group": "gateway.networking.k8s.io", "kind": "HTTPRoute", "name": r["name"]}
                       for r in surface["routes"]]
            if (not policy or policy.get("spec", {}).get("action") != "Allow"
                    or policy["spec"].get("targetRefs") != targets
                    or any(r["namespace"] != ref["namespace"] for r in surface["routes"])):
                issues.append(f"CT-008: required IP access policy missing or targets differ for {sid}")
            else:
                rules = policy["spec"].get("rules", [])
                try:
                    if not rules or any(r.get("source", {}).get("type") != "IPAddress"
                                        or ipaddress.ip_network(r["source"]["ipAddress"]["address"]).prefixlen == 0
                                        for r in rules):
                        raise ValueError
                except (KeyError, TypeError, ValueError):
                    fail("CT-008", policy, "IP allowlist must contain explicit bounded source ranges")
        gateway_id = ("gateway.networking.k8s.io", "Gateway", surface["gateway"]["namespace"], surface["gateway"]["name"])
        expected_gateways.add(gateway_id)
        gateway = index.get(gateway_id)
        if not gateway:
            issues.append(f"CT-005: missing Gateway for {sid}")
            continue
        if (gateway["spec"].get("gatewayClassName") != surface["gateway"]["className"]
                or set(gateway["spec"]) - {"gatewayClassName", "listeners"}):
            fail("CT-005", gateway, "Gateway class or additional configuration differs")
        listener_map = {l["name"]: l for l in gateway["spec"].get("listeners", [])}
        tls = surface["tls"]
        listener = listener_map.get(surface["gatewayListener"], {})
        expected_tls = {"mode": "Terminate", "certificateRefs": [
            {"group": "", "kind": "Secret", "namespace": gateway_id[2], "name": tls["secret"]}]}
        actual_tls = copy.deepcopy(listener.get("tls", {}))
        actual_tls.setdefault("mode", "Terminate")
        for ref in actual_tls.get("certificateRefs", []):
            ref.setdefault("group", "")
            ref.setdefault("kind", "Secret")
            ref.setdefault("namespace", gateway_id[2])
        if (listener.get("hostname") != surface["hostname"] or listener.get("port") != 443
                or listener.get("protocol") != "HTTPS" or actual_tls != expected_tls):
            fail("CT-007", gateway, f"TLS listener differs: {surface['gatewayListener']}")
        cert = get("cert-manager.io", "Certificate", gateway_id[2], tls["certificate"])
        referenced_certificates.add((gateway_id[2], tls["certificate"]))
        if not cert:
            fail("CT-007", gateway, "Certificate declaration missing")
        else:
            cs = cert["spec"]
            writers = [d for d in resources if d.get("kind") == "Certificate"
                       and d["metadata"].get("namespace") == gateway_id[2]
                       and d["spec"].get("secretName") == tls["secret"]]
            if len(writers) != 1:
                fail("CT-007", cert, "TLS Secret has multiple Certificate owners")
            actual_issuer = {"group": "cert-manager.io", "kind": "Issuer", **cs.get("issuerRef", {})}
            if cs.get("secretName") != tls["secret"] or actual_issuer != tls["issuer"]:
                fail("CT-007", cert, "Secret/issuer differs from contract")
            issuer_ns = "" if actual_issuer["kind"] == "ClusterIssuer" else gateway_id[2]
            if not get(actual_issuer["group"], actual_issuer["kind"], issuer_ns, actual_issuer["name"]):
                fail("CT-007", cert, "issuer declaration missing")
            if not any(hostname_matches(n, surface["hostname"]) for n in cs.get("dnsNames", [])):
                fail("CT-007", cert, "Certificate DNS names do not cover hostname")
        for route in surface["routes"]:
            ident = ("gateway.networking.k8s.io", "HTTPRoute", route["namespace"], route["name"])
            if ident in expected_routes:
                issues.append("CT-001: duplicate route in contract")
            expected_routes[ident] = sid
            listeners[gateway_id][route["listener"]].add(route["namespace"])
            used_profiles.add(route["profile"])
            expected_rules = access["spec"]["profiles"].get(route["profile"])
            doc = index.get(ident)
            if doc is None or expected_rules is None:
                issues.append(f"CT-001: missing Route/profile for {sid}/{route['name']}")
                continue
            spec = doc["spec"]
            if set(spec) - {"parentRefs", "hostnames", "rules"}:
                fail("CT-001", doc, "unsupported HTTPRoute spec field")
            if spec.get("hostnames") != [surface["hostname"]] or "*" in surface["hostname"]:
                fail("CT-001", doc, "hostname differs or is wildcard")
            parent = {"group": gateway_id[0], "kind": "Gateway", "namespace": gateway_id[2],
                      "name": gateway_id[3], "sectionName": route["listener"]}
            actual_parents = copy.deepcopy(spec.get("parentRefs", []))
            for p in actual_parents:
                p.setdefault("group", "gateway.networking.k8s.io")
                p.setdefault("kind", "Gateway")
                p.setdefault("namespace", ident[2])
            if actual_parents != [parent] or route["listener"] not in {"http", surface["gatewayListener"]}:
                fail("CT-005", doc, "parent Gateway/listener differs")
            rules = normalize_rules(spec.get("rules", []), ident[2])
            if rules != expected_rules:
                fail("CT-004", doc, "routing rules (matches/backends/filters) differ from profile")
            for rule in rules:
                filters = rule.get("filters", [])
                if route["listener"] == "http":
                    if rule.get("backendRefs") or len(filters) != 1 or filters[0].get("type") != "RequestRedirect":
                        fail("CT-006", doc, "HTTP listener must only redirect")
                    elif (filters[0].get("requestRedirect", {}).get("scheme") != "https"
                          or filters[0]["requestRedirect"].get("hostname", surface["hostname"]) != surface["hostname"]
                          or filters[0]["requestRedirect"].get("port", 443) != 443):
                        fail("CT-006", doc, "HTTP redirect must preserve host and use HTTPS:443")
                for f in filters:
                    if f.get("type") in {"RequestMirror", "ExtensionRef"}:
                        fail("CT-010", doc, "mirror/extension requires a dedicated supported policy")
                    redirect = f.get("requestRedirect", {})
                    if redirect.get("scheme", "https") != "https" or redirect.get("hostname", surface["hostname"]) != surface["hostname"]:
                        fail("CT-006", doc, "redirect changes host or downgrades TLS")
                for ref in rule.get("backendRefs", []):
                    if ref.get("group") != "" or ref.get("kind") != "Service":
                        fail("CT-004", doc, "only Service backends are supported")
                    referenced_backends.add((ref.get("namespace"), ref.get("name"), ref.get("port")))
                    if not grant_exists(doc, "", "Service", ref.get("namespace"), ref.get("name")):
                        fail("CT-008", doc, "cross-namespace backend has no exact ReferenceGrant")

    for ident, expected in listeners.items():
        gateway = index[ident]
        actual = gateway["spec"].get("listeners", [])
        if set(l["name"] for l in actual) != set(expected) or len(actual) != len(expected):
            fail("CT-005", gateway, "listener set differs from registered routes")
        for listener in actual:
            namespaces = sorted(expected.get(listener["name"], set()))
            allow = {"kinds": [{"group": "gateway.networking.k8s.io", "kind": "HTTPRoute"}],
                     "namespaces": {"from": "Selector", "selector": {"matchExpressions": [
                         {"key": "kubernetes.io/metadata.name", "operator": "In", "values": namespaces}]}}}
            if listener.get("allowedRoutes") != allow:
                fail("CT-009", gateway, f"allowedRoutes is not namespace-restricted: {listener['name']}")
            if listener["name"] == "http" and (listener.get("port") != 80 or listener.get("protocol") != "HTTP" or listener.get("tls")):
                fail("CT-006", gateway, "HTTP redirect listener configuration differs")

    for namespace, name, port in referenced_backends:
        service = get("", "Service", namespace, name)
        if service is None:
            issues.append(f"CT-004: missing Service {namespace}/{name}")
        elif port not in {p["port"] for p in service["spec"].get("ports", [])}:
            fail("CT-004", service, "backend Service port not found")
        elif not service["spec"].get("selector") or service["spec"].get("type", "ClusterIP") != "ClusterIP":
            fail("CT-003", service, "backend must be a selector-based ClusterIP Service")

    expected_policies = {ident_tuple(p["identity"]): p["spec"] for p in access["spec"]["policies"]}
    security = cluster["spec"]["security"]
    exceptions = {ident_tuple(e["identity"]): e for e in security["exceptions"]}
    operators = {ident_tuple(e["identity"]): e for e in security["operatorResources"]}
    if (len(expected_policies) != len(access["spec"]["policies"])
            or len(exceptions) != len(security["exceptions"])
            or len(operators) != len(security["operatorResources"])):
        issues.append("CT-000: duplicate policy/exception/operator identity in contract")
    used_exceptions, used_operators, used_policies = set(), set(), set()
    for doc in resources:
        kind, ident = doc.get("kind"), identity(doc)
        if kind == "CustomResourceDefinition":
            continue
        group = ident[0]
        supported_gateway = {"HTTPRoute", "Gateway", "GatewayClass", "ReferenceGrant", "BackendTLSPolicy"}
        supported_nginx = {"NginxProxy", "NginxGateway", "ClientSettingsPolicy", "AccessPolicy"}
        if ((group == "gateway.networking.k8s.io" and kind not in supported_gateway)
                or (group == "gateway.nginx.org" and kind not in supported_nginx)
                or (group == "tailscale.com" and kind != "Connector")):
            fail("CT-001", doc, "unsupported Gateway API/controller resource")
        annotations = doc.get("metadata", {}).get("annotations", {})
        if kind in {"HTTPRoute", "Ingress"}:
            if ident not in expected_routes or annotations.get(ANNOTATION) != expected_routes.get(ident):
                fail("CT-001", doc, "unregistered route or missing surface annotation")
        if kind == "Gateway" and ident not in expected_gateways:
            fail("CT-005", doc, "unregistered Gateway")
        if kind in {"TCPRoute", "TLSRoute", "UDPRoute", "GRPCRoute", "ListenerSet"}:
            fail("CT-001", doc, "unsupported exposure kind")
        if kind in POLICY_KINDS:
            used_policies.add(ident)
            if doc.get("spec") != expected_policies.get(ident):
                fail("CT-008", doc, "unregistered or changed access policy")
            if kind == "ReferenceGrant" and any(not t.get("name") for t in doc["spec"].get("to", [])):
                fail("CT-008", doc, "ReferenceGrant must name its target")
        if kind == "Service":
            fields = service_fields(doc)
            if (fields["type"] != "ClusterIP" or fields["externalIPs"] or fields["externalName"]
                    or not fields["selector"] or any(p.get("nodePort") for p in fields["ports"])
                    or any(k.startswith(("tailscale.com/", "service.beta.kubernetes.io/")) for k in annotations)):
                used_exceptions.add(ident)
                if fields != exceptions.get(ident, {}).get("fields"):
                    fail("CT-002", doc, "unregistered or expanded Service exposure")
        fields = host_fields(doc)
        if fields["hostAccess"]:
            used_exceptions.add(ident)
            if fields != exceptions.get(ident, {}).get("fields"):
                fail("CT-003", doc, "unregistered hostNetwork/hostPort")
        if kind in OPERATOR_KINDS:
            used_operators.add(ident)
            if operator_fields(doc) != operators.get(ident, {}).get("fields"):
                fail("CT-002", doc, "operator-generated networking differs from contract")
    for expected, used, name in ((exceptions, used_exceptions, "exception"), (operators, used_operators, "operator"),
                                 (expected_policies, used_policies, "policy")):
        for ident in set(expected) - used:
            issues.append(f"CT-000: stale {name}: {'/'.join(ident)}")
    if set(access["spec"]["profiles"]) != used_profiles:
        issues.append("CT-000: unused route profile")
    api = cluster["spec"]["apps"]["apiHub"]["service"]
    service = get("", "Service", api["namespace"], api["name"])
    if not service or service["spec"].get("type", "ClusterIP") != api["type"] or sorted(p["port"] for p in service["spec"]["ports"]) != sorted(api["ports"]):
        issues.append("CT-002: api-hub Service differs from cluster contract")
    gateway_ip = cluster["spec"]["network"]["serviceIPs"]["gateway"]
    for publish, namespace, name in (("coreDNS", "kube-system", "coredns"), ("tailscaleSplitDNS", "tailscale", "tailscale-split-dns")):
        doc = get("", "ConfigMap", namespace, name)
        expected = {s["hostname"]: gateway_ip for s in access["spec"]["surfaces"].values()
                    if s["type"] == "gateway-http" and s["publish"][publish]}
        try:
            actual = hosts_from_corefile(doc.get("data", {}).get("Corefile", "")) if doc else {}
            if actual != expected:
                issues.append(f"CT-011: {namespace}/{name}: DNS host/IP inventory differs")
        except InventoryError:
            issues.append(f"CT-011: {namespace}/{name}: duplicate/unsupported DNS configuration")
    dns = get("", "Service", "tailscale", "tailscale-split-dns")
    if not dns or dns["spec"].get("loadBalancerIP") != cluster["spec"]["network"]["serviceIPs"]["tailscaleSplitDNS"]:
        issues.append("CT-011: Split DNS loadBalancerIP differs")
    return issues
