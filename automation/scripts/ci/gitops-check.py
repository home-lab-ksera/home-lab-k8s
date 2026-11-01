#!/usr/bin/env python3
"""GitOps の参照切れと bootstrap への登録漏れを検出する。"""

from pathlib import Path
import subprocess
import sys

import yaml

ROOT = Path(__file__).resolve().parents[3]
REPO_URL = "https://github.com/home-lab-ksera/home-lab-k8s.git"


def applications(base):
    for path in sorted(base.rglob("*")):
        if path.suffix not in {".yaml", ".yml"}:
            continue
        for doc in yaml.safe_load_all(path.read_text()):
            if isinstance(doc, dict) and doc.get("kind") == "Application":
                yield path, doc


def check(root):
    issues = []
    bootstrap = root / "manifests/bootstrap"
    declared = list(applications(bootstrap))
    owned_paths = set()
    for path, app in declared:
        label = f"{path.relative_to(root)}: {app['metadata']['name']}"
        spec = app.get("spec", {})
        sources = spec.get("sources") or [spec.get("source", {})]
        for source in sources:
            if source.get("repoURL", "").removesuffix(".git") != REPO_URL.removesuffix(".git"):
                continue
            if source.get("targetRevision") != "HEAD":
                issues.append(f"{label}: local source targetRevision must be HEAD")
            source_path = source.get("path")
            # values 専用の multi-source はディレクトリをレンダリングしない。
            if source_path is None and source.get("ref"):
                continue
            if not isinstance(source_path, str) or not source_path:
                issues.append(f"{label}: local source path is required")
                continue
            target = (root / source_path).resolve()
            if not target.is_relative_to(root.resolve()) or not target.is_dir():
                issues.append(f"{label}: source path does not exist inside repository: {source_path}")
                continue
            owned_paths.add(target)
            directory = source.get("directory")
            if directory is not None:
                # 現行 repo の単一ファイル include を検証。未対応の glob は黙って通さない。
                include = directory.get("include")
                if not isinstance(include, str) or any(c in include for c in "*?[]{}"):
                    issues.append(f"{label}: directory.include must name one explicit YAML file")
                else:
                    included = (target / include).resolve()
                    if not included.is_relative_to(target) or not included.is_file():
                        issues.append(f"{label}: directory.include file is missing: {include}")
            elif not any((target / name).is_file() for name in ("kustomization.yaml", "kustomization.yml", "Kustomization", "Chart.yaml")):
                issues.append(f"{label}: source has no Kustomization or Chart.yaml: {source_path}")

    for app_dir in sorted((root / "manifests/apps").iterdir()):
        if app_dir.is_dir() and any(app_dir.iterdir()) and app_dir.resolve() not in owned_paths:
            issues.append(f"{app_dir.relative_to(root)}: no bootstrap Application owns this app")

    # 個々のファイルの存在だけでなく、実際に root が同期する集合を確認する。
    result = subprocess.run(
        ["kustomize", "build", str(bootstrap / "applications")],
        capture_output=True, text=True, check=False,
    )
    if result.returncode:
        issues.append(f"bootstrap render failed: {result.stderr.strip()}")
        return issues
    rendered = {
        (doc.get("metadata", {}).get("namespace", "argocd"), doc["metadata"]["name"])
        for doc in yaml.safe_load_all(result.stdout)
        if isinstance(doc, dict) and doc.get("kind") == "Application"
    }
    for path, app in declared:
        if path == bootstrap / "app-of-apps.yaml":
            continue
        metadata = app["metadata"]
        if (metadata.get("namespace", "argocd"), metadata["name"]) not in rendered:
            issues.append(f"{path.relative_to(root)}: Application is not included in bootstrap kustomization")
    return issues


def main():
    issues = check(ROOT)
    for issue in issues:
        print(f"[NG] {issue}", file=sys.stderr)
    if not issues:
        print("[OK] GitOps references and registration")
    return bool(issues)


if __name__ == "__main__":
    sys.exit(main())
