"""GitOps の配備対象を収集する。秘密を含み得るrender結果はメモリ内だけで扱う。"""
from __future__ import annotations

import copy
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml

REPO = "https://github.com/home-lab-ksera/home-lab-k8s"
CLUSTER_KINDS = {"Namespace", "Node", "PersistentVolume", "StorageClass", "ClusterRole",
                 "ClusterRoleBinding", "CustomResourceDefinition", "APIService", "CSIDriver",
                 "ClusterIssuer", "ClusterSecretStore", "GatewayClass", "IngressClass",
                 "ValidatingWebhookConfiguration", "MutatingWebhookConfiguration",
                 "PriorityClass", "RuntimeClass", "ValidatingAdmissionPolicy",
                 "ValidatingAdmissionPolicyBinding", "VolumeAttachment", "CSINode",
                 "CertificateSigningRequest", "FlowSchema", "PriorityLevelConfiguration"}


class InventoryError(ValueError):
    """診断には入力値やコマンドstderrを含めない。"""


class UniqueLoader(yaml.SafeLoader):
    pass


def unique_mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, (str, int, float, bool)) or key in result:
            raise InventoryError("duplicate or invalid YAML mapping key")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def documents(text):
    try:
        docs = list(yaml.load_all(text, Loader=UniqueLoader))
    except (yaml.YAMLError, RecursionError) as exc:
        raise InventoryError("invalid YAML (values omitted)") from exc
    result = []
    def append(doc, depth=0):
        if depth > 20:
            raise InventoryError("manifest List nesting limit exceeded")
        if isinstance(doc, dict):
            if doc.get("apiVersion") and doc.get("kind", "").endswith("List") and "items" in doc:
                if not isinstance(doc["items"], list):
                    raise InventoryError("invalid manifest List")
                for item in doc["items"]:
                    if not isinstance(item, dict):
                        raise InventoryError("invalid manifest List item")
                    append(item, depth + 1)
            else:
                result.append(doc)
    for doc in docs:
        append(doc)
    return result


def read_yaml(path):
    docs = documents(Path(path).read_text())
    if len(docs) != 1:
        raise InventoryError("expected one YAML object")
    return docs[0]


def identity(doc):
    meta = doc.get("metadata", {})
    api = doc.get("apiVersion", "")
    return (api.split("/")[0] if "/" in api else "", doc.get("kind", ""),
            meta.get("namespace", ""), meta.get("name", ""))


def label(doc):
    return "/".join(identity(doc))


def inside(root, path):
    target = (root / path).resolve()
    if not target.is_relative_to(root.resolve()) or not target.exists():
        raise InventoryError("source path missing or outside repository")
    return target


def run(args, cwd, env=None):
    try:
        result = subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=True,
                                timeout=180, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InventoryError(f"{args[0]} unavailable or timed out (output omitted)") from exc
    if result.returncode:
        raise InventoryError(f"{args[0]} failed with exit {result.returncode} (output omitted)")
    return result.stdout


def preflight(root, directory, visited=None):
    """Kustomizeからのexec/remote base/path脱出をrender前に止める。"""
    visited = set() if visited is None else visited
    directory = inside(root, directory)
    if directory in visited:
        return
    visited.add(directory)
    if len(visited) > 200:
        raise InventoryError("Kustomize traversal limit exceeded")
    files = [directory / n for n in ("kustomization.yaml", "kustomization.yml", "Kustomization")]
    config = read_yaml(next((p for p in files if p.exists()), files[0]))
    for key in ("generators", "transformers", "helmGlobals"):
        if config.get(key):
            raise InventoryError(f"unsupported Kustomize option: {key}")
    refs = list(config.get("resources", [])) + list(config.get("bases", [])) + list(config.get("components", []))
    for ref in refs:
        if ref.startswith("https://"):
            if not re.fullmatch(r"https://raw\.githubusercontent\.com/argoproj/argo-cd/v\d+\.\d+\.\d+/manifests/install\.yaml", ref):
                raise InventoryError("unapproved remote Kustomize resource")
            continue
        target = inside(root, directory / ref)
        if target.is_dir():
            preflight(root, target, visited)
        else:
            documents(target.read_text())
    for key in ("patches", "patchesStrategicMerge", "patchesJson6902"):
        for patch in config.get(key, []):
            ref = patch.get("path") if isinstance(patch, dict) else patch
            if ref:
                documents(inside(root, directory / ref).read_text())
    for key in ("configMapGenerator", "secretGenerator"):
        for generator in config.get(key, []):
            for ref in generator.get("files", []) + generator.get("envs", []):
                inside(root, directory / ref.split("=", 1)[-1])
    for chart in config.get("helmCharts", []):
        if not re.fullmatch(r"v?\d+\.\d+\.\d+(?:[-+][\w.-]+)?", str(chart.get("version", ""))):
            raise InventoryError("Helm chart must have a fixed version")
        if chart.get("valuesFile"):
            read_yaml(inside(root, directory / chart["valuesFile"]))
        for ref in chart.get("additionalValuesFiles", []):
            read_yaml(inside(root, directory / ref))
    # vendored chartがrepo外を読むsymlinkも拒否する。
    charts = directory / "charts"
    if charts.exists():
        for path in charts.rglob("*"):
            if path.is_symlink():
                inside(root, path)


def render_local(root, path, env=None):
    target = inside(root, path)
    preflight(root, target)
    return documents(run(["kustomize", "build", "--enable-helm", str(target)], root, env))


def expand_appset(doc):
    spec = doc["spec"]
    if set(spec) - {"goTemplate", "goTemplateOptions", "generators", "template", "syncPolicy"}:
        raise InventoryError("unsupported ApplicationSet option")
    if spec.get("syncPolicy"):
        raise InventoryError("ApplicationSet syncPolicy requires explicit support")
    for generator in spec.get("generators", []):
        if set(generator) != {"list"} or set(generator["list"]) != {"elements"}:
            raise InventoryError("only explicit ApplicationSet list elements are supported")
        for values in generator["list"]["elements"]:
            def substitute(value):
                if isinstance(value, str):
                    def replace(match):
                        key = match[1]
                        if key not in values or not isinstance(values[key], (str, int, bool)):
                            raise InventoryError("unresolved ApplicationSet variable")
                        return str(values[key])
                    value = re.sub(r"{{\s*\.(\w+)\s*}}", replace, value)
                    if "{{" in value or "}}" in value:
                        raise InventoryError("unsupported ApplicationSet expression")
                    return value
                if isinstance(value, list):
                    return [substitute(v) for v in value]
                if isinstance(value, dict):
                    return {k: substitute(v) for k, v in value.items()}
                return value
            yield {"apiVersion": "argoproj.io/v1alpha1", "kind": "Application",
                   **substitute(spec["template"])}


@dataclass
class Inventory:
    resources: list = field(default_factory=list)
    sources: list = field(default_factory=list)
    owners: dict = field(default_factory=dict)


def collect(root):
    root = Path(root).resolve()
    inventory = Inventory()
    queue = [read_yaml(root / "manifests/bootstrap/app-of-apps.yaml")]
    seen_apps = set()
    scopes = set(CLUSTER_KINDS)
    with tempfile.TemporaryDirectory(prefix="contract-render-") as tmp:
        work = Path(tmp)
        env = dict(os.environ, KUBECONFIG=str(work / "no-kubeconfig"),
                   HELM_PLUGINS=str(work / "no-plugins"), HELM_CONFIG_HOME=str(work / "helm-config"),
                   HELM_CACHE_HOME=str(work / "helm-cache"), HELM_DATA_HOME=str(work / "helm-data"))
        while queue:
            app = queue.pop(0)
            app_id = identity(app)
            if app_id in seen_apps or len(seen_apps) >= 150:
                raise InventoryError("duplicate/cyclic Application or traversal limit")
            seen_apps.add(app_id)
            name = app["metadata"]["name"]
            spec = app["spec"]
            dest = spec["destination"]
            if dest.get("server") != "https://kubernetes.default.svc":
                raise InventoryError("unresolved remote cluster destination")
            namespace = dest.get("namespace", "default")
            sources = spec.get("sources") or [spec["source"]]
            refs = {s["ref"]: s for s in sources if "ref" in s}
            if len(refs) != sum("ref" in s for s in sources):
                raise InventoryError("duplicate values source ref")
            for number, source in enumerate(sources):
                if set(source) - {"repoURL", "targetRevision", "path", "chart", "helm", "directory", "ref"}:
                    raise InventoryError(f"unsupported source option in Application {name}")
                local = source.get("repoURL", "").removesuffix(".git") == REPO
                if local and source.get("targetRevision") != "HEAD":
                    raise InventoryError(f"local source must use HEAD: {name}")
                if local and "path" not in source and "ref" in source:
                    continue
                rendered = []
                if "chart" in source:
                    if "directory" in source or "path" in source:
                        raise InventoryError("chart source cannot also specify path/directory")
                    helm = source.get("helm", {})
                    if set(helm) - {"releaseName", "values", "valuesObject", "valueFiles", "parameters", "skipCrds"}:
                        raise InventoryError(f"unsupported Helm option: {name}")
                    version = str(source.get("targetRevision", ""))
                    if not re.fullmatch(r"v?\d+\.\d+\.\d+(?:[-+][\w.-]+)?", version):
                        raise InventoryError(f"unpinned chart: {name}")
                    repo = source["repoURL"]
                    chart = source["chart"]
                    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", chart):
                        raise InventoryError("invalid chart name")
                    if repo.startswith("ghcr.io/"):
                        repo = "oci://" + repo
                    args = ["helm", "template", helm.get("releaseName", name)]
                    if repo.startswith("oci://"):
                        args += [repo.rstrip("/") if repo.rstrip("/").endswith("/" + chart)
                                 else repo.rstrip("/") + "/" + chart]
                    elif repo.startswith("https://"):
                        args += [chart, "--repo", repo]
                    else:
                        raise InventoryError("unsupported Helm registry")
                    args += ["--version", version, "--namespace", namespace, "--kube-version", "1.34.0"]
                    if not helm.get("skipCrds", False):
                        args += ["--include-crds"]
                    for value_file in helm.get("valueFiles", []):
                        if not value_file.startswith("$"):
                            raise InventoryError("external chart valueFile must use local source ref")
                        ref, suffix = value_file[1:].split("/", 1)
                        ref_source = refs.get(ref, {})
                        if ref_source.get("repoURL", "").removesuffix(".git") != REPO or ref_source.get("targetRevision") != "HEAD":
                            raise InventoryError("unresolved Helm values reference")
                        value_path = inside(root, suffix)
                        read_yaml(value_path)
                        args += ["--values", str(value_path)]
                    # Argo CD: valuesObject > values > valueFiles、parametersが最優先。
                    values = helm.get("valuesObject")
                    if values is None and "values" in helm:
                        values = read_inline(helm["values"])
                    if values is not None:
                        values_path = work / f"values-{len(inventory.sources)}.yaml"
                        values_path.write_text(yaml.safe_dump(values))
                        values_path.chmod(0o600)
                        args += ["--values", str(values_path)]
                    for parameter in helm.get("parameters", []):
                        if set(parameter) - {"name", "value", "forceString"}:
                            raise InventoryError("unsupported Helm parameter")
                        args += ["--set-string" if parameter.get("forceString") else "--set",
                                 parameter["name"] + "=" + parameter["value"]]
                    try:
                        rendered = documents(run(args, root, env))
                    except InventoryError as exc:
                        raise InventoryError(f"Application {name}, chart {chart}: {exc}") from exc
                elif local:
                    if "helm" in source:
                        raise InventoryError("local Helm source requires explicit renderer support")
                    target = inside(root, source["path"])
                    if "directory" in source:
                        directory = source["directory"]
                        if set(directory) != {"include"} or not re.fullmatch(r"[\w.-]+\.ya?ml", directory["include"]):
                            raise InventoryError("unsupported directory source")
                        rendered = documents(inside(root, target / directory["include"]).read_text())
                    else:
                        rendered = render_local(root, source["path"], env)
                else:
                    raise InventoryError(f"unsupported source: {name}")
                inventory.sources.append({"application": name, "source": number,
                                          "path": source.get("path", ""), "chart": source.get("chart", ""),
                                          "revision": source["targetRevision"], "resources": len(rendered)})
                for doc in rendered:
                    if doc.get("kind") == "CustomResourceDefinition" and doc["spec"]["scope"] == "Cluster":
                        scopes.add(doc["spec"]["names"]["kind"])
                for doc in rendered:
                    doc = copy.deepcopy(doc)
                    meta = doc.setdefault("metadata", {})
                    if doc.get("kind") not in scopes:
                        meta.setdefault("namespace", namespace)
                    ident = identity(doc)
                    if ident in inventory.owners:
                        raise InventoryError(f"resource owned more than once: {'/'.join(ident)}")
                    inventory.owners[ident] = name
                    inventory.resources.append(doc)
                    if doc.get("kind") == "Application":
                        queue.append(doc)
                    elif doc.get("kind") == "ApplicationSet":
                        queue.extend(expand_appset(doc))
    return inventory


def read_inline(text):
    docs = documents(text)
    if len(docs) != 1:
        raise InventoryError("invalid inline values")
    return docs[0]
