#!/usr/bin/env python3
"""宣言済みの固定版を安定版と比較し、--apply 時に更新候補を生成する。"""
from __future__ import annotations

import argparse
from functools import lru_cache
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
from urllib.parse import quote, urljoin

import yaml
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[3]
STABLE = re.compile(r"v?\d+\.\d+\.\d+")
ARGO_BASE = "manifests/platform/argocd-config/upstream/kustomization.yaml"


def fetch(url: str, headers: tuple[str, ...] = ()) -> bytes:
    # curl の失敗時は更新処理全体を止め、不完全な調査結果を公開しない。
    args = ["curl", "--fail", "--silent", "--show-error", "--location",
            "--retry", "2", "--max-time", "90"]
    for header in headers:
        args.extend(["--header", header])
    try:
        result = subprocess.run([*args, url], capture_output=True, timeout=300)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"取得タイムアウト: {url}") from None
    if result.returncode:
        # Authorization ヘッダーを含むコマンド全体はログに出さない。
        raise RuntimeError(f"取得失敗: {url}: {result.stderr.decode()}")
    return result.stdout


@lru_cache(maxsize=None)
def json_url(url: str, headers: tuple[str, ...] = ()):
    return json.loads(fetch(url, headers))


def stable_latest(versions) -> str:
    stable = [str(v) for v in versions if STABLE.fullmatch(str(v))]
    if not stable:
        raise ValueError("安定版が見つかりません")
    return max(stable, key=lambda v: Version(v.lstrip("v")))


@lru_cache(maxsize=None)
def registry(repo: str) -> tuple[str, str, tuple[str, ...]]:
    first = repo.split("/")[0]
    if "." not in first and ":" not in first:
        host, name = "registry-1.docker.io", repo if "/" in repo else "library/" + repo
        auth = f"https://auth.docker.io/token?service=registry.docker.io&scope=repository:{name}:pull"
    else:
        host, name = repo.split("/", 1)
        auth = {
            "ghcr.io": f"https://ghcr.io/token?service=ghcr.io&scope=repository:{name}:pull",
            "public.ecr.aws": f"https://public.ecr.aws/token/?service=public.ecr.aws&scope=repository:{name}:pull",
        }.get(host)
    headers = ()
    if auth:
        data = json_url(auth)
        headers = ("Authorization: Bearer " + (data.get("token") or data["access_token"]),)
    return host, name, headers


@lru_cache(maxsize=None)
def registry_tags(repo: str) -> list[str]:
    host, name, headers = registry(repo)
    # AWS CLI は ECR Public が tag listing を提供しないため公式Git tagsを使用。
    if repo == "public.ecr.aws/aws-cli/aws-cli":
        return [x["name"] for x in github("repos/aws/aws-cli/tags?per_page=100")]
    data = json_url(f"https://{host}/v2/{name}/tags/list", headers)
    if not data.get("tags"):
        raise ValueError(f"タグがありません: {repo}")
    return data["tags"]


def verify_image(repo: str, tag: str) -> None:
    host, name, headers = registry(repo)
    accept = "Accept: application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.docker.distribution.manifest.v2+json"
    data = json_url(f"https://{host}/v2/{name}/manifests/{quote(tag, safe='')}", (*headers, accept))
    if data.get("schemaVersion") != 2:
        raise ValueError(f"イメージmanifestを確認できません: {repo}:{tag}")


@lru_cache(maxsize=None)
def github(path: str):
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    headers = ("Authorization: Bearer " + token,) if token else ()
    return json_url("https://api.github.com/" + path, headers)


def image_latest(current: str, tags: list[str]) -> str:
    # OS variantを保持。Seleniumはブラウザ版とビルド日を数値比較する。
    selenium = re.fullmatch(r"(\d+)\.(\d+)-(\d{8})", current)
    if selenium:
        candidates = [t for t in tags if re.fullmatch(r"\d+\.\d+-\d{8}", t)]
        return max([current, *candidates], key=lambda t: tuple(map(int, re.findall(r"\d+", t))))
    match = re.fullmatch(r"(v?)(\d+\.\d+\.\d+)([-a-z0-9.]*)", current)
    if not match:
        raise ValueError(f"比較形式が未定義です: {current}")
    prefix, _, suffix = match.groups()
    pattern = re.compile(re.escape(prefix) + r"(\d+\.\d+\.\d+)" + re.escape(suffix))
    candidates = [(Version(m[1]), tag) for tag in tags if (m := pattern.fullmatch(tag))]
    return max([(Version(match[2]), current), *candidates])[1]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    if text.count(old) != 1:
        raise ValueError(f"置換位置が一意ではありません: {path}: {old}")
    path.write_text(text.replace(old, new, 1))


class Audit:
    def __init__(self, root: Path, apply: bool = False):
        self.root, self.apply = root, apply
        self.rows: list[dict] = []
        self.images: dict[tuple[str, str], str] = {}

    def record(self, name: str, current: str, latest: str, path: Path, source: str) -> bool:
        changed = current != latest
        self.rows.append(dict(name=name, current=current, latest=latest,
                              path=path.relative_to(self.root).as_posix(), source=source, changed=changed))
        return changed

    def image(self, path: Path, repo: str, tag: str, literal: str) -> None:
        # privateアプリ・可変タグ・digest固定は別の配信経路で管理する。
        if repo.startswith("harbor.qroksera.com/") or "@" in repo or "@" in tag:
            return
        if tag in {"latest", "stable", "dind"}:
            return
        if not re.match(r"v?\d", tag):
            raise ValueError(f"未対応の固定タグ: {repo}:{tag}")
        key = (repo, tag)
        if key not in self.images:
            latest = image_latest(tag, registry_tags(repo))
            if latest != tag:
                verify_image(repo, latest)
            self.images[key] = latest
        latest = self.images[key]
        if self.record(repo, tag, latest, path, f"https://{registry(repo)[0]}/v2/{registry(repo)[1]}/tags/list") and self.apply:
            replace_once(path, literal, literal.replace(tag, latest))

    def chart(self, path: Path, source: dict, wrapper: bool) -> None:
        name = source["name"] if wrapper else source["chart"]
        repo = source["repo"] if wrapper else source["repoURL"]
        current = str(source["version"] if wrapper else source["targetRevision"])
        download = None
        reference = repo
        if repo.startswith("https://"):
            index = yaml.load(fetch(repo.rstrip("/") + "/index.yaml"), Loader=yaml.CSafeLoader)
            entries = index["entries"][name]
            latest = stable_latest(x["version"] for x in entries)
            entry = next(x for x in entries if x["version"] == latest)
            download = urljoin(repo.rstrip("/") + "/", entry["urls"][0])
        else:
            oci = repo.removeprefix("oci://").rstrip("/")
            if not oci.endswith("/" + name):
                oci += "/" + name
            latest = stable_latest(registry_tags(oci))
            host, repository = oci.split("/", 1)
            reference = f"https://{host}/v2/{repository}/tags/list"
        if Version(latest.lstrip("v")) <= Version(current.lstrip("v")):
            latest = current
        if not self.record(name, current, latest, path, reference) or not self.apply:
            return
        if wrapper:
            self.vendor_chart(path.parent, name, current, latest, download)
        field = "version" if wrapper else "targetRevision"
        replace_once(path, f"{field}: {current}", f"{field}: {latest}")

    def vendor_chart(self, parent: Path, name: str, current: str, latest: str, url: str) -> None:
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            with tarfile.open(fileobj=io.BytesIO(fetch(url)), mode="r:gz") as archive:
                # 配布物でもパストラバーサルとリンクは許可しない。
                for member in archive.getmembers():
                    parts = Path(member.name).parts
                    if not parts or parts[0] != name or ".." in parts or member.issym() or member.islnk():
                        raise ValueError(f"不正なchartエントリ: {member.name}")
                archive.extractall(temp_path, filter="data")
            chart = yaml.safe_load((temp_path / name / "Chart.yaml").read_text())
            if chart["name"] != name or str(chart["version"]) != latest:
                raise ValueError(f"chart metadata不一致: {url}")
            destination = parent / "charts" / f"{name}-{latest}"
            if destination.exists():
                raise ValueError(f"更新先chartが既に存在します: {destination}")
            shutil.copytree(temp_path, destination)
        old = parent / "charts" / f"{name}-{current}"
        if old.exists():
            shutil.rmtree(old)

    def remote(self, path: Path, url: str) -> None:
        match = re.fullmatch(r"https://raw.githubusercontent.com/([^/]+/[^/]+)/(v?\d+\.\d+\.\d+)/(.*)", url)
        release = re.fullmatch(r"https://github.com/([^/]+/[^/]+)/releases/download/(v?\d+\.\d+\.\d+)/(.*)", url)
        if not (match or release):
            return
        repo, current, _ = (match or release).groups()
        latest = github(f"repos/{repo}/releases/latest")["tag_name"]
        if not STABLE.fullmatch(latest):
            raise ValueError(f"公式latest releaseが安定版ではありません: {repo}: {latest}")
        if Version(latest.lstrip("v")) <= Version(current.lstrip("v")):
            latest = current
        if self.record(repo, current, latest, path, f"https://github.com/{repo}/releases") and self.apply:
            updated = url.replace(f"/{current}/", f"/{latest}/")
            fetch(updated)  # リリース資産が公開済みであることも確認する。
            replace_once(path, url, updated)

    def collect(self) -> None:
        for path in sorted((self.root / "manifests").rglob("*.yaml")):
            if "charts" in path.relative_to(self.root).parts:
                continue
            for doc in yaml.safe_load_all(path.read_text()):
                if not isinstance(doc, dict):
                    continue
                source = doc.get("spec", {}).get("source", {})
                if source.get("chart"):
                    self.chart(path, source, wrapper=False)
                for chart in doc.get("helmCharts", []):
                    self.chart(path, chart, wrapper=True)
                for resource in doc.get("resources", []):
                    if isinstance(resource, str) and resource.startswith("https://"):
                        self.remote(path, resource)
                for image in doc.get("images", []):
                    if image.get("newTag"):
                        self.image(path, image.get("newName", image["name"]), str(image["newTag"]), f"newTag: {image['newTag']}")
                self.scan_images(path, doc)

    def scan_images(self, path: Path, value) -> None:
        if isinstance(value, dict):
            image = value.get("image")
            if isinstance(image, str) and ":" in image.rsplit("/", 1)[-1]:
                repo, tag = image.rsplit(":", 1)
                self.image(path, repo, tag, image)
            for child in value.values():
                self.scan_images(path, child)
        elif isinstance(value, list):
            for child in value:
                self.scan_images(path, child)

    def report(self) -> str:
        lines = ["## Kubernetes アプリ安定版の定期更新", "", "| 対象 | 現在 | 最新 | ファイル |", "| --- | --- | --- | --- |"]
        updates = [r for r in self.rows if r["changed"]]
        for row in updates:
            lines.append(f"| [{row['name']}]({row['source']}) | `{row['current']}` | `{row['latest']}` | `{row['path']}` |")
        if not updates:
            lines.append("\n更新はありません。")
        lines += ["", "安定版のみを対象とし、OS variantを維持します。メジャー更新も候補に含みます。",
                  "マージは手動です。リリースノート、データ移行、バックアップを確認してください。",
                  "マージ後はArgo CD本体を含めてGitOpsで同期されます。",
                  "private Harborのアプリ、可変タグ、digest固定はこの監査の対象外です。",
                  f"\n調査した固定版宣言: {len(self.rows)} 件。"]
        return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    audit = Audit(ROOT, args.apply)
    audit.collect()
    report = audit.report()
    (ROOT / "weekly-version-audit-summary.md").write_text(report)
    (ROOT / "weekly-version-audit-issue.md").write_text(report)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as out:
            out.write(report)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as out:
            out.write(f"updates_found={str(any(r['changed'] for r in audit.rows)).lower()}\n")
    print(report)


if __name__ == "__main__":
    main()
