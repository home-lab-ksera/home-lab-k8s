#!/usr/bin/env python3

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[3]
MANIFESTS = ROOT / "manifests"

EXPECTED_CORE_RESOURCES = {
    "storage-classes/local-storage-class.yaml",
    "storage-classes/local-ssd-storage-class.yaml",
}

EXPECTED_EXTERNAL_SECRET_DOMAINS = {
    "stores",
    "argocd",
    "harbor",
    "github-actions",
    "networking",
    "app-runtime",
    "observability",
}

EXPECTED_EXTERNAL_SECRET_FILES = {
    "harbor-admin-secret.yaml",
    "harbor-registry-sandbox.yaml",
    "github-multi-repo-secret.yaml",
    "harbor-auth-secret.yaml",
    "cloudflare-api-token.yaml",
    "tailscale-oauth.yaml",
    "slack-secret.yaml",
    "alertmanager-slack.yaml",
    "rustfs-auth-rustfs.yaml",
    "rustfs-auth-sandbox.yaml",
}

LEGACY_SECRET_IDENTIFIERS = {
    "github-auth-secret",
    "harbor-registry-secret",
    "grafana-cloud-credentials",
    "grafana-cloud-monitoring",
    "promtail-grafana-cloud-config",
}


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def load_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_docs(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return [doc for doc in yaml.safe_load_all(f) if isinstance(doc, dict)]


def yaml_files(base: Path) -> list[Path]:
    if not base.exists():
        return []
    return [p for p in sorted(base.rglob("*.yaml")) if p.is_file() and "/charts/" not in p.as_posix()]


def fail(issues: list[str], message: str) -> None:
    issues.append(message)


def check_bootstrap_target_revisions(issues: list[str]) -> None:
    for path in yaml_files(MANIFESTS / "bootstrap"):
        for doc in load_docs(path):
            if doc.get("kind") != "Application":
                continue
            source = doc.get("spec", {}).get("source", {}) or {}
            if source.get("targetRevision") == "main":
                name = doc.get("metadata", {}).get("name", "<unknown>")
                fail(issues, f"{rel(path)}: Application/{name} targetRevision must stay HEAD, not main")


def check_core_kustomization(issues: list[str]) -> None:
    path = MANIFESTS / "core/kustomization.yaml"
    if not path.exists():
        fail(issues, "manifests/core/kustomization.yaml が見つかりません")
        return
    data = load_yaml(path) or {}
    resources = set(data.get("resources") or [])
    missing = EXPECTED_CORE_RESOURCES - resources
    if missing:
        fail(issues, f"{rel(path)}: StorageClass resource が不足しています: {sorted(missing)}")


def check_harbor_node_mutations(issues: list[str]) -> None:
    path = MANIFESTS / "infrastructure/gitops/harbor/kustomization.yaml"
    if not path.exists():
        return
    data = load_yaml(path) or {}
    resources = set(data.get("resources") or [])
    if "node-mutations/" in resources or "node-mutations" in resources:
        fail(issues, f"{rel(path)}: harbor 既定 kustomization に node-mutations を含めません")


def check_external_secrets(issues: list[str]) -> None:
    base = MANIFESTS / "platform/secrets/external-secrets"
    for domain in EXPECTED_EXTERNAL_SECRET_DOMAINS:
        if not (base / domain / "kustomization.yaml").exists():
            fail(issues, f"ExternalSecret domain が不足しています: {domain}")

    if (base / "external-secret-resources.yaml").exists():
        fail(issues, "ExternalSecret monolith が残っています: external-secret-resources.yaml")

    for path in yaml_files(MANIFESTS / "platform/argocd-config"):
        for doc in load_docs(path):
            if doc.get("kind") == "ExternalSecret":
                name = doc.get("metadata", {}).get("name", "<unknown>")
                fail(issues, f"{rel(path)}: pre-ESO path に ExternalSecret が残っています: {name}")

    actual_files = {p.name for p in yaml_files(base)}
    missing_files = EXPECTED_EXTERNAL_SECRET_FILES - actual_files
    if missing_files:
        fail(issues, f"ExternalSecret split 後の expected file が不足しています: {sorted(missing_files)}")

    for path in yaml_files(base):
        text = path.read_text(encoding="utf-8")
        for legacy in LEGACY_SECRET_IDENTIFIERS:
            if legacy in text:
                fail(issues, f"{rel(path)}: legacy secret identifier が残っています: {legacy}")


def main() -> int:
    issues: list[str] = []
    check_bootstrap_target_revisions(issues)
    check_core_kustomization(issues)
    check_harbor_node_mutations(issues)
    check_external_secrets(issues)

    if issues:
        for issue in issues:
            print(f"[NG] {issue}", file=sys.stderr)
        return 1
    print("[OK] static consistency checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
