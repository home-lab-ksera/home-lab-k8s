#!/usr/bin/env bash
set -euo pipefail

config_dir="${LIFECYCLE_CONFIG_DIR:-/config}"
desired="$(cat "$config_dir/lifecycle.json")"
# 同じ ID のルールだけを置換し、それ以外の既存ルールを維持する。
merge_query="{Rules: [Rules[?ID != 'expire-all-objects-after-30-days'], \`$desired\`.Rules][]}"
verify_query="contains(Rules, \`$desired\`.Rules[0])"
work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT

s3() {
  aws --endpoint-url "$S3_ENDPOINT" --cli-connect-timeout 10 --cli-read-timeout 30 s3api "$@"
}

while IFS= read -r bucket || [[ -n "$bucket" ]]; do
  [[ -n "$bucket" ]] || continue
  # 存在しないバケットや認証エラーでは失敗させ、設定漏れを同期失敗として可視化する。
  s3 head-bucket --bucket "$bucket"
  if s3 get-bucket-lifecycle-configuration --bucket "$bucket" \
    --query "$merge_query" --output json > "$work_dir/merged.json" 2> "$work_dir/error"; then
    :
  elif grep -q '(NoSuchLifecycleConfiguration)' "$work_dir/error"; then
    cp "$config_dir/lifecycle.json" "$work_dir/merged.json"
  else
    cat "$work_dir/error" >&2
    exit 1
  fi

  s3 put-bucket-lifecycle-configuration --bucket "$bucket" \
    --lifecycle-configuration "file://$work_dir/merged.json"
  verified="$(s3 get-bucket-lifecycle-configuration --bucket "$bucket" \
    --query "$verify_query" --output text)"
  if [[ "$verified" != "True" ]]; then
    echo "$bucket: ライフサイクルの読み戻し検証に失敗しました" >&2
    exit 1
  fi
  echo "$bucket: ライフサイクルを適用・検証しました"
done < "$config_dir/buckets.txt"

# viewer用ルールだけを更新し、他のCORSルールを維持する。
desired_cors="$(cat "$config_dir/cors.json")"
cors_merge="{CORSRules: [CORSRules[?ID != 'media-viewer'], \`$desired_cors\`.CORSRules][]}"
while IFS= read -r bucket || [[ -n "$bucket" ]]; do
  [[ -n "$bucket" ]] || continue
  if s3 get-bucket-cors --bucket "$bucket" --query "$cors_merge" --output json \
    > "$work_dir/cors.json" 2> "$work_dir/error"; then
    :
  elif grep -q '(NoSuchCORSConfiguration)' "$work_dir/error"; then
    cp "$config_dir/cors.json" "$work_dir/cors.json"
  else
    cat "$work_dir/error" >&2
    exit 1
  fi
  s3 put-bucket-cors --bucket "$bucket" --cors-configuration "file://$work_dir/cors.json"
  verified="$(s3 get-bucket-cors --bucket "$bucket" \
    --query "contains(CORSRules, \`$desired_cors\`.CORSRules[0])" --output text)"
  if [[ "$verified" != "True" ]]; then
    echo "$bucket: CORSの読み戻し検証に失敗しました" >&2
    exit 1
  fi
  echo "$bucket: viewer CORSを適用・検証しました"
done < "$config_dir/buckets.txt"
