#!/usr/bin/env python3
"""Fail-closed read-only verification for a deployed production revision."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import pwd
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path


REQUIRED_TIMERS = (
    "surveil-company-disclosures.timer",
    "surveil-sina-stock-news.timer",
    "surveil-research-collector.timer",
    "surveil-official-collector.timer",
    "surveil-news-collector.timer",
    "surveil-market-daily.timer",
    "surveil-llm-decision-audit-cleanup.timer",
)
OPTIONAL_TIMER = "surveil-value-directory.timer"


class VerificationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def read_key_values(path: Path) -> dict[str, str]:
    require(path.is_file(), f"缺少文件：{path.name}")
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if "=" not in raw_line:
            continue
        key, value = raw_line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def resolve_config_path(root: Path, raw_path: str, name: str) -> Path:
    require(bool(raw_path.strip()), f"{name} 未配置")
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = root / path
    require(path.is_file(), f"{name} 文件不存在")
    require(os.access(path, os.R_OK), f"{name} 对当前服务账号不可读")
    return path


def run_systemctl(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["systemctl", *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        raise VerificationError(f"systemd 检查失败：{' '.join(args)}")
    return result


def verify_timer(unit: str) -> None:
    run_systemctl("is-enabled", "--quiet", unit)
    run_systemctl("is-active", "--quiet", unit)
    next_trigger = run_systemctl("show", unit, "--property=NextElapseUSecRealtime", "--value").stdout.strip()
    require(next_trigger.lower() not in {"", "n/a"}, f"定时任务缺少下次触发时间：{unit}")


def verify_service(unit: str) -> None:
    run_systemctl("is-enabled", "--quiet", unit)
    run_systemctl("is-active", "--quiet", unit)


def verify_database(db_path: Path) -> None:
    require(db_path.is_file(), "生产 SQLite 不存在")
    uri = f"file:{db_path}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        quick_check = conn.execute("PRAGMA quick_check").fetchone()
        require(quick_check == ("ok",), "SQLite quick_check 未通过")
        require(conn.execute("PRAGMA foreign_key_check").fetchone() is None, "SQLite 存在外键违规")
        current_failed = conn.execute(
            "SELECT COUNT(*) FROM market_reviews WHERE is_current=1 AND review_status='failed_retryable'"
        ).fetchone()[0]
        require(current_failed == 0, "存在当前 failed_retryable review")
        stale_pending = conn.execute(
            """
            SELECT COUNT(*) FROM market_reviews
            WHERE is_current=1 AND review_status='admitted_pending'
              AND datetime(created_at) < datetime('now', '-30 minutes')
            """
        ).fetchone()[0]
        require(stale_pending == 0, "存在超过 30 分钟的 admitted_pending review")


def exact_requirements(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        require(line.count("==") == 1, f"生产直接依赖未固定版本：{line}")
        name, version = line.split("==", 1)
        require(bool(name and version), f"无效依赖版本：{line}")
        pins[name] = version
    require(bool(pins), "requirements.txt 没有生产直接依赖")
    return pins


def verify_dependencies(path: Path) -> None:
    for name, expected in exact_requirements(path).items():
        try:
            installed = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError as exc:
            raise VerificationError(f"生产依赖未安装：{name}") from exc
        require(installed == expected, f"生产依赖版本不一致：{name}")


def verify_logrotate(path: Path, root: Path, service_user: str) -> None:
    require(path.is_file(), "普通日志 logrotate 配置不存在")
    metadata = path.stat()
    require(stat.S_IMODE(metadata.st_mode) == 0o644, "普通日志 logrotate 配置权限不是 0644")
    require(metadata.st_uid == 0 and metadata.st_gid == 0, "普通日志 logrotate 配置必须属于 root:root")
    content = path.read_text(encoding="utf-8")
    require(f"{root}/logs/*.log" in content, "普通日志 logrotate 路径不正确")
    for directive in ("daily", "rotate 14", "compress", "copytruncate", f"su {service_user} {service_user}"):
        require(directive in content, f"普通日志 logrotate 缺少策略：{directive}")
    require("reports/" not in content and "llm-decision-audit" not in content, "普通日志轮转错误覆盖了私有审计")


def fetch_json(port: int, path: str, token: str) -> dict[str, object]:
    from http_utils import http_get

    headers = {"X-Holdings-Token": token} if token else {}
    response = http_get(f"http://127.0.0.1:{port}{path}", headers=headers, timeout=10, retries=1)
    require(response.status_code == 200, f"Web API 返回非 200：{path}")
    payload = json.loads(response.content.decode("utf-8"))
    require(isinstance(payload, dict) and payload.get("ok") is True, f"Web API 校验失败：{path}")
    return payload


def verify_web(env: dict[str, str]) -> None:
    from http_utils import http_get, reset_http_client

    port = int(env.get("HOLDINGS_WEB_PORT", "8787"))
    token = env.get("HOLDINGS_WEB_TOKEN", "").strip()
    for name in ("SURVEIL_HTTP_PROXY", "HTTPS_PROXY", "HTTP_PROXY"):
        os.environ.pop(name, None)
    reset_http_client()
    response = http_get(f"http://127.0.0.1:{port}/", timeout=10, retries=1)
    require(response.status_code == 200, "Web 首页返回非 200")
    health = fetch_json(port, "/api/health/summary", token)
    require(health.get("total_failures") == 0, "Web 健康摘要存在当前任务或启用来源故障")
    profiles = fetch_json(port, "/api/source-profiles", token)
    runtime = profiles.get("runtime_status")
    require(isinstance(runtime, dict) and int(runtime.get("enabled_sources", 0)) > 0, "Web 来源配置没有启用来源")
    rules = fetch_json(port, "/api/current-rules", token)
    require(isinstance(rules.get("range_admission"), dict), "Web 未返回范围准入规则状态")
    require(isinstance(rules.get("llm_decision"), dict), "Web 未返回大模型决策规则状态")
    require(rules["range_admission"].get("status") == "loaded", "Web 范围准入规则未成功加载")
    require(rules["llm_decision"].get("status") == "loaded", "Web 大模型决策规则未成功加载")


def verify(root: Path, service_user: str, logrotate_config: Path) -> None:
    root = root.resolve()
    require(root.is_dir(), "部署目录不存在")
    revision = read_key_values(root / "REVISION")
    installed = read_key_values(root / "data" / "systemd-installed-revision")
    require(bool(revision.get("commit")), "部署 revision 缺少 commit")
    require(revision.get("commit") == installed.get("commit"), "部署 revision 与 systemd 安装 revision 不一致")
    require(revision.get("dirty") == "0", "部署 revision 标记为 dirty")

    sys.path.insert(0, str(root / "scripts"))
    from env_utils import load_env

    load_env(root / ".env", override=True)
    env = dict(os.environ)
    rule_core = resolve_config_path(root, env.get("RULE_CORE_CONFIG", ""), "RULE_CORE_CONFIG")
    llm_rules = resolve_config_path(root, env.get("LLM_DECISION_RULE_CONFIG", ""), "LLM_DECISION_RULE_CONFIG")
    account = pwd.getpwnam(service_user)
    metadata = llm_rules.stat()
    require(stat.S_IMODE(metadata.st_mode) == 0o600, "LLM_DECISION_RULE_CONFIG 权限不是 0600")
    require(metadata.st_uid == account.pw_uid and metadata.st_gid == account.pw_gid, "LLM_DECISION_RULE_CONFIG 所有者不正确")

    from llm_rule_catalog import load_rule_catalog
    from production_admission import load_production_rule_config

    load_production_rule_config({**env, "RULE_CORE_CONFIG": str(rule_core)})
    load_rule_catalog(llm_rules)

    timers = list(REQUIRED_TIMERS)
    if run_systemctl("is-enabled", "--quiet", OPTIONAL_TIMER, check=False).returncode == 0:
        timers.append(OPTIONAL_TIMER)
    for unit in timers:
        verify_timer(unit)

    services = ["surveil-holdings-web.service", "surveil-sina-flash.service"]
    feedback_enabled = any(
        env.get(name, "").strip().lower() in {"1", "true", "yes", "on"}
        for name in ("FEISHU_FEEDBACK_LISTENER_ENABLED", "FEISHU_FEEDBACK_ENABLED")
    )
    if feedback_enabled:
        services.append("surveil-feishu-feedback.service")
    if env.get("X_BEARER_TOKEN", "").strip():
        services.append("surveil-x-stream.service")
    for unit in services:
        verify_service(unit)

    verify_dependencies(root / "requirements.txt")
    verify_logrotate(logrotate_config, root, service_user)
    verify_database(root / "data" / "surveil.sqlite3")
    verify_web(env)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(os.getenv("SURVEIL_ROOT", "/opt/surveil")))
    parser.add_argument("--service-user", default=os.getenv("SURVEIL_SERVICE_USER", "surveil"))
    parser.add_argument("--logrotate-config", type=Path, default=Path("/etc/logrotate.d/surveil"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        verify(args.root, args.service_user, args.logrotate_config)
    except VerificationError as exc:
        print(f"生产验证失败：{exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - fail closed without exposing private runtime details
        print(f"生产验证失败：{type(exc).__name__}", file=sys.stderr)
        return 1
    print("生产验证通过：revision、私有规则、systemd、依赖、普通日志轮转、SQLite、Web API 和启用来源健康均正常。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
