#!/usr/bin/env python3

from __future__ import annotations

import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

from contract_inventory import InventoryError, render_local


ROOT = Path(__file__).resolve().parents[3]
MANIFESTS = ROOT / "manifests"
BOOTSTRAP_APPS = MANIFESTS / "bootstrap/applications"

YAML_SUFFIXES = {".yaml", ".yml"}
WORKLOAD_KINDS = {"Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob", "Pod"}
ACCESS_KINDS = {"HTTPRoute", "Gateway", "Ingress", "ClientSettingsPolicy", "BackendTLSPolicy", "ReferenceGrant"}

SCAN_TEXT_DIRS = ["manifests", "automation", ".github"]
SCAN_TEXT_FILES = ["AGENTS.md", "README.md", "Makefile"]

SELF_FILES = {
    "automation/scripts/ci/policy-check.py",
    "automation/scripts/ci/policy-check.sh",
}

MONITORING_LEGACY_ALLOWLIST = {
    "automation/scripts/ci/consistency-check.sh",
}

ALLOWLISTED_PRUNE_FALSE = {
    ("Application", "argocd", "argocd-projects", "spec.syncPolicy.automated.prune"),
    ("Application", "argocd", "argocd-core", "spec.syncPolicy.automated.prune"),
}


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def fail(issues: list[str], rule: str, message: str) -> None:
    issues.append(f"{rule}: {message}")


def text_files() -> list[Path]:
    files: list[Path] = []
    for dirname in SCAN_TEXT_DIRS:
        base = ROOT / dirname
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            if rel(path) in SELF_FILES:
                continue
            if rel(path) == "automation/settings.toml":
                continue
            if path.suffix in {".sh", ".yaml", ".yml", ".md", ".json", ".jsonc", ".toml"}:
                files.append(path)
    for filename in SCAN_TEXT_FILES:
        path = ROOT / filename
        if path.exists():
            files.append(path)
    return files


def yaml_files(base: Path) -> list[Path]:
    if not base.exists():
        return []
    return [
        p for p in sorted(base.rglob("*"))
        if p.is_file() and p.suffix in YAML_SUFFIXES and "/charts/" not in p.as_posix()
    ]


def load_docs(path: Path) -> list[dict[str, Any]]:
    try:
        with path.open(encoding="utf-8") as f:
            return [doc for doc in yaml.safe_load_all(f) if isinstance(doc, dict)]
    except yaml.YAMLError as exc:
        return [{"__yaml_error__": str(exc)}]


def identity(doc: dict[str, Any], default_namespace: str | None = None) -> tuple[str, str, str]:
    meta = doc.get("metadata", {}) or {}
    kind = str(doc.get("kind", ""))
    namespace = str(meta.get("namespace") or default_namespace or "")
    name = str(meta.get("name", ""))
    return kind, namespace, name


def identity_label(ident: tuple[str, str, str]) -> str:
    kind, namespace, name = ident
    return f"{kind}/{namespace}/{name}" if namespace else f"{kind}/{name}"


def nested_get(obj: dict[str, Any], keys: list[str]) -> Any:
    cur: Any = obj
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def collect_images(doc: dict[str, Any]) -> list[str]:
    images: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            image = value.get("image")
            if isinstance(image, str):
                images.append(image)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(doc)
    return images


def check_text_rules(issues: list[str]) -> None:
    patterns = {
        "R-001": re.compile(r"user-applications|user-application-definitions"),
    }
    monitoring_pattern = re.compile(
        r"k8s-monitoring|grafana-cloud-monitoring|grafana-cloud-credentials|"
        r"promtail-grafana-cloud-config|grafana\.github\.io/helm-charts|grafana\.net|"
        r"deploy-grafana-monitoring|deploy-grafana-with-secret|deploy-grafana-monitoring-simple"
    )

    for path in text_files():
        path_rel = rel(path)
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for rule, pattern in patterns.items():
            if pattern.search(text):
                fail(issues, rule, f"legacy identifier remains in {path_rel}")
        if monitoring_pattern.search(text) and path_rel not in MONITORING_LEGACY_ALLOWLIST:
            fail(issues, "R-012", f"monitoring legacy identifier appears outside PH6 allowlist: {path_rel}")

    rollout_targets = [ROOT / ".github/workflows", ROOT / "automation/platform/.github/workflows"]
    for target in rollout_targets:
        for path in text_files_under(target):
            if "kubectl rollout restart" in path.read_text(encoding="utf-8", errors="ignore"):
                fail(issues, "R-003", f"direct rollout restart remains in app delivery path: {rel(path)}")


def text_files_under(base: Path) -> list[Path]:
    if not base.exists():
        return []
    return [p for p in base.rglob("*") if p.is_file()]


def check_manifest_rules(issues: list[str]) -> None:
    for path in yaml_files(MANIFESTS):
        path_rel = rel(path)
        for doc in load_docs(path):
            if "__yaml_error__" in doc:
                fail(issues, "YAML", f"{path_rel}: {doc['__yaml_error__']}")
                continue

            kind = doc.get("kind")
            ident = identity(doc)
            name = ident[2]

            if kind == "ExternalSecret" and not path_rel.startswith("manifests/platform/secrets/external-secrets/"):
                fail(issues, "R-005", f"top-level ExternalSecret outside post-ESO path: {path_rel} {identity_label(ident)}")

            if path_rel.startswith("manifests/apps/") and kind in ACCESS_KINDS:
                fail(issues, "R-008", f"access resource under apps path: {path_rel} {identity_label(ident)}")

            if kind in WORKLOAD_KINDS:
                namespace = ident[1]
                for image in collect_images(doc):
                    if image.startswith("harbor.qroksera.com/") and image.endswith(":latest"):
                        allowed = (
                            path_rel.startswith("manifests/apps/")
                            and namespace == "sandbox"
                            and image.startswith("harbor.qroksera.com/sandbox/")
                        )
                        if not allowed:
                            fail(issues, "R-004", f"first-party :latest is not sandbox-scoped: {path_rel} {identity_label(ident)} {image}")

            if kind == "Application":
                source = doc.get("spec", {}).get("source", {}) or {}
                target_revision = source.get("targetRevision")
                if target_revision == "HEAD" and not path_rel.startswith("manifests/bootstrap/"):
                    fail(issues, "R-006", f"targetRevision HEAD outside bootstrap: {path_rel} {identity_label(ident)}")

                prune = nested_get(doc, ["spec", "syncPolicy", "automated", "prune"])
                if prune is False:
                    allow_key = (*ident, "spec.syncPolicy.automated.prune")
                    if allow_key not in ALLOWLISTED_PRUNE_FALSE:
                        fail(issues, "R-006", f"prune:false is not allowlisted: {path_rel} {identity_label(ident)}")

                if name == "harbor-patch":
                    fail(issues, "R-011", f"legacy harbor-patch Application remains: {path_rel}")
                if "ignoreDifferences" in doc and "harbor-core" in yaml.safe_dump(doc.get("ignoreDifferences"), sort_keys=True):
                    fail(issues, "R-011", f"legacy harbor-core ignoreDifferences remains: {path_rel}")

    retired_paths = [MANIFESTS / "apps/cloudflared", MANIFESTS / "apps/argocd", MANIFESTS / "apps/rustfs"]
    for retired in retired_paths:
        if retired.exists() and any(p.is_file() for p in retired.rglob("*")):
            fail(issues, "R-008", f"retired access path has tracked files: {rel(retired)}")

    harbor_legacy_paths = [
        MANIFESTS / "infrastructure/gitops/harbor/harbor-routes.yaml",
        MANIFESTS / "infrastructure/gitops/harbor/harbor-image-cleanup-cronjob.yaml",
    ]
    for legacy_path in harbor_legacy_paths:
        if legacy_path.exists():
            fail(issues, "R-011", f"Harbor legacy split-owner artifact remains: {rel(legacy_path)}")


def child_app_docs() -> list[tuple[Path, dict[str, Any]]]:
    docs: list[tuple[Path, dict[str, Any]]] = []
    for path in yaml_files(BOOTSTRAP_APPS):
        if path.name == "kustomization.yaml":
            continue
        for doc in load_docs(path):
            if doc.get("kind") == "Application":
                docs.append((path, doc))
    return docs


def check_child_app_structure(issues: list[str]) -> None:
    names: dict[str, Path] = {}
    source_paths: dict[str, Path] = {}
    allowed_duplicate_paths = {"manifests/platform/argocd-config"}

    for path, doc in child_app_docs():
        path_rel = rel(path)
        name = str(doc.get("metadata", {}).get("name", ""))
        source = doc.get("spec", {}).get("source", {}) or {}
        source_path = source.get("path")

        if name in names:
            fail(issues, "R-007", f"duplicate child Application name {name}: {rel(names[name])} and {path_rel}")
        names[name] = path

        if source_path in {"manifests/apps", "manifests/bootstrap/applications/user-apps"}:
            fail(issues, "R-007", f"legacy aggregate owner path in {path_rel}: {source_path}")

        if path_rel.startswith("manifests/bootstrap/applications/user-apps/"):
            if not isinstance(source_path, str) or not re.fullmatch(r"manifests/apps/[a-z0-9][a-z0-9-]*", source_path):
                fail(issues, "R-007", f"user app child Application has invalid source path: {path_rel} {source_path}")

        if isinstance(source_path, str):
            if source_path in source_paths and source_path not in allowed_duplicate_paths:
                fail(issues, "R-007", f"duplicate child Application source path {source_path}: {rel(source_paths[source_path])} and {path_rel}")
            source_paths[source_path] = path


def render_child_app(path: str) -> tuple[int, str, str]:
    try:
        return 0, yaml.safe_dump_all(render_local(ROOT, path)), ""
    except InventoryError as exc:
        return 1, "", str(exc)


def default_namespace_for_app(doc: dict[str, Any]) -> str | None:
    destination = doc.get("spec", {}).get("destination", {}) or {}
    namespace = destination.get("namespace")
    return str(namespace) if namespace else None


def check_rendered_collision(issues: list[str]) -> None:
    if shutil.which("kustomize") is None:
        fail(issues, "R-009", "kustomize not found; rendered collision check cannot run")
        return

    owners_by_identity: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for app_path, app_doc in child_app_docs():
        source_path = (app_doc.get("spec", {}).get("source", {}) or {}).get("path")
        source = app_doc.get("spec", {}).get("source", {}) or {}
        app_name = app_doc.get("metadata", {}).get("name", rel(app_path))
        if not isinstance(source_path, str):
            continue
        if source.get("directory", {}).get("include"):
            continue
        if not (ROOT / source_path / "kustomization.yaml").exists():
            continue
        rc, stdout, stderr = render_child_app(source_path)
        if rc != 0:
            fail(issues, "R-009", f"failed to render {app_name} ({source_path}): {stderr.strip()}")
            continue
        try:
            docs = [doc for doc in yaml.safe_load_all(stdout) if isinstance(doc, dict)]
        except yaml.YAMLError as exc:
            fail(issues, "R-009", f"failed to parse rendered output for {app_name}: {exc}")
            continue
        for doc in docs:
            ident = identity(doc, default_namespace_for_app(app_doc))
            if not ident[0] or not ident[2]:
                continue
            owners_by_identity[ident].append(str(app_name))

    for ident, owners in sorted(owners_by_identity.items()):
        unique_owners = sorted(set(owners))
        if len(unique_owners) > 1:
            fail(issues, "R-009", f"rendered resource collision {identity_label(ident)} from {', '.join(unique_owners)}")


def main() -> int:
    issues: list[str] = []
    check_text_rules(issues)
    check_manifest_rules(issues)
    check_child_app_structure(issues)
    check_rendered_collision(issues)

    if issues:
        for issue in issues:
            print(f"[NG] {issue}", file=sys.stderr)
        return 1
    print("[OK] policy checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
