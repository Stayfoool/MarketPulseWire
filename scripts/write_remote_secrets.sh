#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=remote_env.sh
source "$SCRIPT_DIR/remote_env.sh"
require_remote_host

echo "将写入远程服务器 $REMOTE_HOST:$REMOTE_ENV"
echo "直接回车 = 保留远程现有值；输入新值 = 覆盖对应配置。"
echo "DeepSeek 常用配置：Base URL=https://api.deepseek.com，模型=deepseek-chat"
echo "智谱 GLM 5.3 Flash 固定配置：Base URL=https://open.bigmodel.cn/api/paas/v4，模型=glm-5.3-flash"
echo

ssh -n -i "$REMOTE_SSH_KEY" -o IdentitiesOnly=yes "$REMOTE_USER@$REMOTE_HOST" \
  "REMOTE_ENV='$REMOTE_ENV' python3 - <<'PY'
from pathlib import Path
import os

env_path = Path(os.environ['REMOTE_ENV'])
sensitive = {
    'LLM_API_KEY',
    'LLM_GLM_API_KEY',
}
visible = [
    'LLM_PROVIDER',
    'LLM_BASE_URL',
    'LLM_MODEL',
]
values = {}
if env_path.exists():
    for raw_line in env_path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        values[key.strip()] = value.strip().strip('\"').strip(\"'\")

print('当前远程配置（敏感值不显示）：')
if not env_path.exists():
    print(f'- {env_path}: 不存在，将创建')
else:
    for key in visible:
        value = values.get(key, '')
        print(f'- {key}: {value or \"<未配置>\"}')
    for key in sensitive:
        if key in values:
            print(f'- {key}: ' + ('<已配置>' if values.get(key) else '<未配置>'))
PY"
echo

printf "请输入 DeepSeek API Key（回车保留现有值）: "
IFS= read -r -s LLM_API_KEY
echo
printf "请输入 DeepSeek Base URL（回车保留现有值）: "
IFS= read -r LLM_BASE_URL
printf "请输入 DeepSeek 模型名称（回车保留现有值）: "
IFS= read -r LLM_MODEL
printf "请输入智谱 GLM 5.3 Flash API Key（回车保留现有值）: "
IFS= read -r -s LLM_GLM_API_KEY
echo
printf "请输入当前模型 deepseek 或 zhipu_glm（回车保留现有值）: "
IFS= read -r LLM_PROVIDER

PAYLOAD_FILE="$(mktemp)"
REMOTE_PAYLOAD="/tmp/surveil-secrets-$$.json"
cleanup() {
  rm -f "$PAYLOAD_FILE"
}
trap cleanup EXIT

PAYLOAD_FILE="$PAYLOAD_FILE" \
LLM_API_KEY="$LLM_API_KEY" \
LLM_BASE_URL="$LLM_BASE_URL" \
LLM_MODEL="$LLM_MODEL" \
LLM_GLM_API_KEY="$LLM_GLM_API_KEY" \
LLM_PROVIDER="$LLM_PROVIDER" \
python3 - <<'PY'
from pathlib import Path
import json
import os

payload = {
    "LLM_API_KEY": os.environ["LLM_API_KEY"],
    "LLM_BASE_URL": os.environ["LLM_BASE_URL"],
    "LLM_MODEL": os.environ["LLM_MODEL"],
    "LLM_GLM_API_KEY": os.environ["LLM_GLM_API_KEY"],
    "LLM_PROVIDER": os.environ["LLM_PROVIDER"],
}
path = Path(os.environ["PAYLOAD_FILE"])
path.write_text(json.dumps(payload), encoding="utf-8")
path.chmod(0o600)
PY

scp -i "$REMOTE_SSH_KEY" -o IdentitiesOnly=yes "$PAYLOAD_FILE" "$REMOTE_USER@$REMOTE_HOST:$REMOTE_PAYLOAD" >/dev/null

ssh -n -i "$REMOTE_SSH_KEY" -o IdentitiesOnly=yes "$REMOTE_USER@$REMOTE_HOST" \
  "REMOTE_ENV='$REMOTE_ENV' REMOTE_PAYLOAD='$REMOTE_PAYLOAD' REMOTE_SERVICE_USER='$REMOTE_SERVICE_USER' python3 - <<'PY'
from pathlib import Path
import json
import os
import pwd
import grp

env_path = Path(os.environ['REMOTE_ENV'])
env_path.parent.mkdir(parents=True, exist_ok=True)
payload_path = Path(os.environ['REMOTE_PAYLOAD'])
payload = json.loads(payload_path.read_text(encoding='utf-8'))

updates = {}

llm_api_key = str(payload.get('LLM_API_KEY') or '').strip()
llm_base_url = str(payload.get('LLM_BASE_URL') or '').strip()
llm_model = str(payload.get('LLM_MODEL') or '').strip()
llm_glm_api_key = str(payload.get('LLM_GLM_API_KEY') or '').strip()
llm_provider = str(payload.get('LLM_PROVIDER') or '').strip()
if llm_provider and llm_provider not in {'deepseek', 'zhipu_glm'}:
    raise SystemExit('LLM_PROVIDER 只允许 deepseek 或 zhipu_glm')
if llm_api_key:
    updates['LLM_API_KEY'] = llm_api_key
if llm_base_url:
    updates['LLM_BASE_URL'] = llm_base_url
if llm_model:
    updates['LLM_MODEL'] = llm_model
if llm_glm_api_key:
    updates['LLM_GLM_API_KEY'] = llm_glm_api_key
if llm_provider:
    updates['LLM_PROVIDER'] = llm_provider

lines = env_path.read_text(encoding='utf-8').splitlines() if env_path.exists() else []
seen = set()
out = []
for line in lines:
    stripped = line.strip()
    key = stripped.split('=', 1)[0] if '=' in stripped and not stripped.startswith('#') else ''
    if key in updates:
        out.append(f'{key}={updates[key]}')
        seen.add(key)
    else:
        out.append(line)
for key, value in updates.items():
    if key not in seen:
        out.append(f'{key}={value}')
env_path.write_text('\\n'.join(out) + '\\n', encoding='utf-8')
env_path.chmod(0o600)
service_user = os.environ.get('REMOTE_SERVICE_USER') or 'surveil'
try:
    uid = pwd.getpwnam(service_user).pw_uid
    gid = grp.getgrnam(service_user).gr_gid
    os.chown(env_path, uid, gid)
except KeyError:
    pass
payload_path.unlink(missing_ok=True)
changed_sensitive = []
if llm_api_key:
    changed_sensitive.append('LLM_API_KEY=<redacted>')
if llm_glm_api_key:
    changed_sensitive.append('LLM_GLM_API_KEY=<redacted>')
changed_plain = [key for key in ('LLM_PROVIDER', 'LLM_BASE_URL', 'LLM_MODEL') if key in updates]
changed = changed_sensitive + changed_plain
if changed:
    print(f'已更新 {env_path}: ' + ', '.join(changed))
else:
    print(f'已检查 {env_path}: 未输入新值，保留现有模型配置')
PY"

unset LLM_API_KEY LLM_BASE_URL LLM_MODEL LLM_GLM_API_KEY LLM_PROVIDER
