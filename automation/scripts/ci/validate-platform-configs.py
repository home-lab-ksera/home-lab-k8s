#!/usr/bin/env python3
"""マニフェストが指定する実イメージで内部設定の互換性を検証する。"""

from pathlib import Path
import re
import subprocess

import yaml


ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / "manifests/platform/observability/config"


def workload_image(name):
    path = ROOT / f"manifests/platform/observability/{name}.yaml"
    for doc in yaml.safe_load_all(path.read_text()):
        if doc and doc.get("kind") == "Deployment":
            return doc["spec"]["template"]["spec"]["containers"][0]["image"]
    raise ValueError(f"Deployment がありません: {path}")


def run(*args):
    subprocess.run(["docker", "run", "--rm", *args], check=True, timeout=600)


def main():
    run("-v", f"{CONFIG}:/config:ro", workload_image("loki"),
        "-config.file=/config/loki.yaml", "-verify-config=true")
    run("-v", f"{CONFIG}:/config:ro", workload_image("alloy"),
        "validate", "/config/config.alloy")
    workflow = (ROOT / ".github/workflows/renovate.yml").read_text()
    image = re.search(r"ghcr\.io/renovatebot/renovate:[\w.\-]+", workflow).group()
    run("--entrypoint", "renovate-config-validator", "-v", f"{ROOT}:/repo:ro",
        image, "--strict", "/repo/renovate.json", "/repo/.github/renovate-runner.json",
        "/repo/.github/renovate/app.json")


if __name__ == "__main__":
    main()
