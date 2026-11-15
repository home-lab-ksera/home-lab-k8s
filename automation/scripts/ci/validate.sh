#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"

log_section() {
  echo ""
  echo "== $1 =="
}

require_command() {
  local command_name="$1"
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "${command_name} not found" >&2
    exit 1
  fi
}

log_section "GitHub Actions workflow validation"
require_command actionlint
# シェル/Pythonの検証とは分け、workflow構文とcontextの利用可能範囲を検証する。
actionlint -shellcheck= -pyflakes= "$ROOT_DIR"/.github/workflows/*.yml

log_section "Setup behavior tests (isolated stubs)"
require_command python3
python3 -m unittest discover -s "$ROOT_DIR/automation/tests/setup" -v

log_section "Version update tests (no network)"
python3 -m unittest discover -s "$ROOT_DIR/automation/tests/version_updates" -v

log_section "Observability report tests (no network)"
python3 -m unittest discover -s "$ROOT_DIR/automation/tests/observability" -v

log_section "Observability alert rule tests"
require_command promtool
promtool test rules "$ROOT_DIR"/automation/tests/observability/*.test.yaml

log_section "GitOps validation regression tests"
python3 -m unittest discover -s "$ROOT_DIR/automation/tests/ci" -v

log_section "PostgreSQL credential reconciliation tests"
python3 -m unittest discover -s "$ROOT_DIR/automation/tests/postgresql" -v

log_section "Shellcheck"
require_command shellcheck

mapfile -t sh_files < <(find "$ROOT_DIR/automation" -name '*.sh' -print)
if ((${#sh_files[@]})); then
  shellcheck -S error -x "${sh_files[@]}"
else
  echo "No shell scripts found under automation/"
fi

log_section "Yamllint"
require_command yamllint

mapfile -t yaml_files < <(
  find \
    "$ROOT_DIR/manifests" \
    "$ROOT_DIR/automation/templates" \
    "$ROOT_DIR/automation/infrastructure" \
    -path '*/charts/*' -prune -o \
    \( -name '*.yaml' -o -name '*.yml' \) -print
)
if ((${#yaml_files[@]})); then
  yamllint -f parsable -c "$ROOT_DIR/.yamllint.yml" "${yaml_files[@]}"
else
  echo "No YAML files found for yamllint"
fi

require_command kustomize
require_command python3
if ! python3 -c 'import yaml' >/dev/null 2>&1; then
  echo "python3 PyYAML not found" >&2
  exit 1
fi

log_section "GitOps references and registration"
python3 "$ROOT_DIR/automation/scripts/ci/gitops-check.py"

log_section "Policy checks"
"$ROOT_DIR/automation/scripts/ci/policy-check.sh"

log_section "PostgreSQL version consistency"
python3 "$ROOT_DIR/automation/scripts/ci/postgresql-versions.py"

log_section "Kubeconform"
require_command kubeconform

pgo_schema_dir="$(mktemp -d)"
trap 'rm -rf "$pgo_schema_dir"' EXIT
python3 - "$ROOT_DIR" "$pgo_schema_dir" <<'PY'
import json
from pathlib import Path
import sys
import yaml

root, output = map(Path, sys.argv[1:])
version = yaml.safe_load((root / "manifests/platform/pgo/kustomization.yaml").read_text())["helmCharts"][0]["version"]
crd = yaml.safe_load((root / f"manifests/platform/pgo/charts/pgo-{version}/pgo/crds/postgres-operator.crunchydata.com_postgresclusters.yaml").read_text())
for version in crd["spec"]["versions"]:
    schema = version["schema"]["openAPIV3Schema"]
    (output / f"postgrescluster_{version['name']}.json").write_text(json.dumps(schema))

# 汎用 CRD カタログは旧 Image Updater の spec.namespace を要求するため、採用 chart と一致させる。
application = yaml.safe_load((root / "manifests/bootstrap/applications/platform/image-updater.yaml").read_text())
chart = next(source for source in application["spec"]["sources"] if source.get("chart") == "argocd-image-updater")
schema = root / "automation/schemas/argocd-image-updater" / chart["targetRevision"] / "imageupdater_v1alpha1.json"
(output / "imageupdater_v1alpha1.json").write_text(schema.read_text())
PY

kubeconform_schema_locations=(
  -schema-location "$pgo_schema_dir/{{ .ResourceKind }}_{{ .ResourceAPIVersion }}.json"
  -schema-location default
  -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{ .Group }}/{{ .ResourceKind }}_{{ .ResourceAPIVersion }}.json'
)

while IFS= read -r kfile; do
  kdir="$(dirname "$kfile")"
  echo "kustomize build --enable-helm $kdir | kubeconform"
  kustomize build --enable-helm "$kdir" \
    | kubeconform \
      -strict \
      -ignore-missing-schemas \
      "${kubeconform_schema_locations[@]}"
done < <(find "$ROOT_DIR/manifests" -name kustomization.yaml -print)

log_section "Consistency checks"
"$ROOT_DIR/automation/scripts/ci/consistency-check.sh"
