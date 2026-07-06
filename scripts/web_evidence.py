"""Controlled web evidence retrieval for gate and skeptic reviews."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from http_utils import http_get, http_post
from source_health import record_source_failure, record_source_success


TAVILY_SEARCH_URL = "https://api.tavily.com/search"
DEFAULT_PROVIDER = "tavily"
REALTIME_DEFAULT_QUERIES = 5
DEEP_DEFAULT_QUERIES = 8

PRIMARY_DOMAINS = {
    "semi.org",
    "trendforce.com",
    "digitimes.com",
    "digitimes.com.tw",
    "thelec.net",
    "thelec.kr",
    "xtech.nikkei.com",
    "prnewswire.com",
    "reuters.com",
    "sec.gov",
    "sse.com.cn",
    "szse.cn",
    "hkexnews.hk",
    "openai.com",
    "nvidia.com",
    "samsungsemiconductor.com",
    "samsung.com",
    "skhynix.com",
    "micron.com",
}

MACRO_KEYWORDS = (
    "cpi",
    "pce",
    "nonfarm",
    "非农",
    "fomc",
    "fed",
    "federal reserve",
    "美联储",
    "沃什",
    "warsh",
    "美债",
    "国债收益率",
    "treasury yield",
    "2年期",
    "10年期",
    "美元指数",
)

COUNTER_TERMS = {
    "price_cycle": "扩产 OR 投产 OR 产能释放 OR 价格回落 OR 库存上升 OR 交期缩短 OR 替代供应",
    "capex": "延期 OR 削减 OR 取消 OR 良率问题 OR 客户砍单 OR 竞争对手扩产",
    "export_control": "豁免 OR 替代来源 OR 库存缓冲 OR 价格回落 OR supply alternative",
}


@dataclass(frozen=True)
class EvidenceQuery:
    query_type: str
    query: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "y", "on", "是"}


def env_int(name: str, default: int, *, minimum: int = 1, maximum: int = 100) -> int:
    raw = os.getenv(name, "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def web_evidence_enabled() -> bool:
    if not env_flag("WEB_EVIDENCE_ENABLED", False):
        return False
    provider = provider_name()
    return bool(api_key(provider))


def provider_name() -> str:
    return (os.getenv("WEB_EVIDENCE_PROVIDER", DEFAULT_PROVIDER).strip() or DEFAULT_PROVIDER).lower()


def api_key(provider: str) -> str:
    if provider == "tavily":
        return os.getenv("WEB_EVIDENCE_API_KEY", "").strip() or os.getenv("TAVILY_API_KEY", "").strip()
    if provider == "brave":
        return os.getenv("WEB_EVIDENCE_API_KEY", "").strip() or os.getenv("BRAVE_SEARCH_API_KEY", "").strip()
    return os.getenv("WEB_EVIDENCE_API_KEY", "").strip()


def timeout_seconds() -> float:
    raw = os.getenv("WEB_EVIDENCE_TIMEOUT_SECONDS", "").strip()
    try:
        return max(3.0, min(60.0, float(raw))) if raw else 12.0
    except ValueError:
        return 12.0


def max_results() -> int:
    return env_int("WEB_EVIDENCE_MAX_RESULTS", 4, minimum=1, maximum=10)


def lookback_days() -> int:
    return env_int("WEB_EVIDENCE_LOOKBACK_DAYS", 30, minimum=1, maximum=365)


def tavily_time_range() -> str:
    days = lookback_days()
    if days <= 1:
        return "day"
    if days <= 7:
        return "week"
    if days <= 31:
        return "month"
    return "year"


def max_queries(mode: str) -> int:
    default = DEEP_DEFAULT_QUERIES if mode == "deep" else REALTIME_DEFAULT_QUERIES
    return env_int("WEB_EVIDENCE_MAX_QUERIES", default, minimum=1, maximum=12)


def ensure_web_evidence_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS web_evidence_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trigger_module TEXT NOT NULL,
            trigger_source TEXT,
            trigger_item_id TEXT,
            trigger_reason TEXT,
            mode TEXT NOT NULL DEFAULT 'realtime',
            provider TEXT NOT NULL,
            query_json TEXT NOT NULL,
            status TEXT NOT NULL,
            error TEXT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS web_evidence_docs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            query_type TEXT NOT NULL,
            query TEXT NOT NULL,
            result_rank INTEGER NOT NULL,
            url TEXT NOT NULL,
            canonical_url TEXT NOT NULL,
            title TEXT,
            source TEXT,
            published_at TEXT,
            retrieved_at TEXT NOT NULL,
            snippet TEXT,
            extracted_text TEXT,
            claim TEXT,
            evidence_type TEXT NOT NULL,
            stance TEXT NOT NULL,
            source_quality TEXT,
            score REAL,
            content_hash TEXT NOT NULL,
            raw_json TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(run_id, canonical_url, query_type),
            FOREIGN KEY(run_id) REFERENCES web_evidence_runs(id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_web_evidence_runs_trigger "
        "ON web_evidence_runs(trigger_module, trigger_source, trigger_item_id)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_web_evidence_runs_created ON web_evidence_runs(created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_web_evidence_docs_run ON web_evidence_docs(run_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_web_evidence_docs_url ON web_evidence_docs(canonical_url)")


def item_id(item: dict[str, Any]) -> str:
    return str(item.get("id") or item.get("url") or item.get("title") or "")


def text_blob(item: dict[str, Any], review: dict[str, Any] | None = None) -> str:
    review = review or {}
    parts = [
        item.get("title"),
        item.get("summary"),
        item.get("content"),
        item.get("full_text"),
        review.get("market_impact"),
        review.get("industry_impact"),
        review.get("reason"),
        review.get("daily_summary"),
        " ".join(str(x) for x in review.get("affected_targets") or []),
    ]
    return "\n".join(str(part or "") for part in parts)


def compact_text(value: Any, limit: int = 240) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def clean_query(value: str) -> str:
    query = " ".join(str(value or "").split())
    return query[:240]


def query_terms_from_title(title: str) -> str:
    title = re.sub(r"https?://\S+", "", title)
    title = re.sub(r"[【】\\[\\]（）()]", " ", title)
    return clean_query(title)


def contains_any(text: str, keywords: tuple[str, ...] | list[str]) -> bool:
    lower = text.lower()
    return any(keyword.lower() in lower for keyword in keywords)


def counter_query(title_terms: str, blob: str) -> str:
    lower = blob.lower()
    if contains_any(lower, ("出口管制", "export control", "管制", "禁令")):
        return f"{title_terms} {COUNTER_TERMS['export_control']}"
    if contains_any(lower, ("capex", "资本开支", "投资", "建厂", "工厂", "订单", "采购")):
        return f"{title_terms} {COUNTER_TERMS['capex']}"
    return f"{title_terms} {COUNTER_TERMS['price_cycle']}"


def should_include_macro_query(blob: str) -> bool:
    return contains_any(blob, MACRO_KEYWORDS)


def build_queries(source: str, item: dict[str, Any], review: dict[str, Any], *, mode: str = "realtime") -> list[EvidenceQuery]:
    title = query_terms_from_title(str(item.get("title") or ""))
    blob = text_blob(item, review)
    targets = [str(target).strip() for target in review.get("affected_targets") or [] if str(target).strip()]
    target_terms = " ".join(targets[:3])
    days = lookback_days()
    queries = [
        EvidenceQuery("prior_coverage", clean_query(f"{title} {target_terms} 早已 报道 转载 近{days}天")),
        EvidenceQuery("primary_source", clean_query(f"{title} {target_terms} official press release report source")),
        EvidenceQuery("counter_evidence", clean_query(counter_query(f"{title} {target_terms}".strip(), blob))),
        EvidenceQuery("market_pricing", clean_query(f"{title} {target_terms} 股价 已上涨 已反映 券商 晨会")),
    ]
    if should_include_macro_query(blob):
        queries.append(
            EvidenceQuery(
                "macro_background",
                clean_query(f"{title} CPI 非农 PCE FOMC Fed 美债收益率 市场预期"),
            )
        )
    if mode == "deep":
        queries.extend(
            [
                EvidenceQuery("relation_support", clean_query(f"{title} {target_terms} supplier customer supply chain relationship")),
                EvidenceQuery("prior_coverage", clean_query(f"{title} site:finance.sina.com.cn OR site:cls.cn OR site:yicai.com")),
                EvidenceQuery("counter_evidence", clean_query(f"{title} price decline capacity expansion inventory")),
            ]
        )
    deduped: list[EvidenceQuery] = []
    seen: set[str] = set()
    for query in queries:
        if not query.query or query.query in seen:
            continue
        seen.add(query.query)
        deduped.append(query)
    return deduped[: max_queries(mode)]


def canonicalize_url(url: str) -> str:
    parsed = urlsplit(str(url or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return str(url or "").strip()
    filtered = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in {"spm", "from", "share", "share_token"}
    ]
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/") or "/", urlencode(filtered), ""))


def domain_from_url(url: str) -> str:
    return urlsplit(url).netloc.lower().removeprefix("www.")


def source_quality(url: str) -> str:
    domain = domain_from_url(url)
    if not domain:
        return "unknown"
    if any(domain == item or domain.endswith("." + item) for item in PRIMARY_DOMAINS):
        return "primary_or_high_trust"
    if any(item in domain for item in ("sina.com", "cls.cn", "yicai.com", "stcn.com", "cnstock.com")):
        return "mainstream_media"
    return "secondary_or_unknown"


def evidence_type_for_query(query_type: str) -> str:
    mapping = {
        "prior_coverage": "prior_coverage",
        "primary_source": "primary_source",
        "counter_evidence": "counter_evidence",
        "market_pricing": "market_pricing",
        "macro_background": "macro_background",
        "relation_support": "relation_support",
    }
    return mapping.get(query_type, "unknown")


def stance_for_evidence(query_type: str, quality: str) -> str:
    if query_type in {"prior_coverage", "market_pricing", "counter_evidence"}:
        return "supports_downgrade"
    if query_type in {"primary_source", "relation_support"} and quality == "primary_or_high_trust":
        return "supports_push"
    if query_type == "macro_background":
        return "mixed"
    return "neutral"


def content_hash(*values: Any) -> str:
    raw = "\n".join(str(value or "") for value in values)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def maybe_extract_body(url: str) -> str:
    if not env_flag("WEB_EVIDENCE_FETCH_BODY", False):
        return ""
    try:
        import trafilatura
    except Exception:
        return ""
    response = http_get(url, timeout=timeout_seconds(), retries=0)
    html = response.content.decode("utf-8", errors="replace")
    extracted = trafilatura.extract(html, include_comments=False, include_tables=False) or ""
    return compact_text(extracted, 1200)


def tavily_search(query: EvidenceQuery) -> list[dict[str, Any]]:
    key = api_key("tavily")
    if not key:
        raise RuntimeError("缺少 WEB_EVIDENCE_API_KEY/TAVILY_API_KEY")
    payload = {
        "query": query.query,
        "search_depth": os.getenv("WEB_EVIDENCE_TAVILY_SEARCH_DEPTH", "basic").strip() or "basic",
        "max_results": max_results(),
        "time_range": tavily_time_range(),
        "include_answer": False,
        "include_raw_content": env_flag("WEB_EVIDENCE_TAVILY_INCLUDE_RAW_CONTENT", False),
        "include_images": False,
    }
    topic = os.getenv("WEB_EVIDENCE_TAVILY_TOPIC", "news").strip()
    if topic:
        payload["topic"] = topic
    response = http_post(
        os.getenv("WEB_EVIDENCE_TAVILY_URL", TAVILY_SEARCH_URL).strip() or TAVILY_SEARCH_URL,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        json_data=payload,
        timeout=timeout_seconds(),
        retries=1,
    )
    parsed = json.loads(response.content.decode("utf-8", errors="replace"))
    results = parsed.get("results") if isinstance(parsed, dict) else None
    if not isinstance(results, list):
        return []
    return [result for result in results if isinstance(result, dict)]


def brave_search(_query: EvidenceQuery) -> list[dict[str, Any]]:
    raise NotImplementedError("Brave Search provider 尚未接入；请使用 WEB_EVIDENCE_PROVIDER=tavily")


def provider_search(provider: str, query: EvidenceQuery) -> list[dict[str, Any]]:
    if provider == "tavily":
        return tavily_search(query)
    if provider == "brave":
        return brave_search(query)
    raise ValueError(f"不支持的 WEB_EVIDENCE_PROVIDER：{provider}")


def create_run(
    conn: sqlite3.Connection,
    *,
    trigger_module: str,
    source: str,
    item: dict[str, Any],
    trigger_reason: str,
    mode: str,
    provider: str,
    queries: list[EvidenceQuery],
) -> int:
    ensure_web_evidence_tables(conn)
    now = utc_now()
    cur = conn.execute(
        """
        INSERT INTO web_evidence_runs (
            trigger_module, trigger_source, trigger_item_id, trigger_reason,
            mode, provider, query_json, status, started_at, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'running', ?, ?)
        """,
        (
            trigger_module,
            source,
            item_id(item),
            trigger_reason,
            mode,
            provider,
            json.dumps([query.__dict__ for query in queries], ensure_ascii=False),
            now,
            now,
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def finish_run(conn: sqlite3.Connection, run_id: int, *, status: str, error: str = "") -> None:
    conn.execute(
        """
        UPDATE web_evidence_runs
        SET status = ?, error = ?, finished_at = ?
        WHERE id = ?
        """,
        (status, error[:1000], utc_now(), run_id),
    )
    conn.commit()


def save_doc(conn: sqlite3.Connection, run_id: int, query: EvidenceQuery, rank: int, result: dict[str, Any]) -> dict[str, Any] | None:
    url = str(result.get("url") or "").strip()
    if not url:
        return None
    canonical = canonicalize_url(url)
    title = str(result.get("title") or "").strip()
    snippet = str(result.get("content") or result.get("snippet") or "").strip()
    raw_content = str(result.get("raw_content") or "").strip()
    extracted = compact_text(raw_content, 1200) if raw_content else maybe_extract_body(url)
    claim = compact_text(extracted or snippet or title, 360)
    quality = source_quality(url)
    evidence_type = evidence_type_for_query(query.query_type)
    stance = stance_for_evidence(query.query_type, quality)
    retrieved_at = utc_now()
    source = str(result.get("source") or domain_from_url(url) or "").strip()
    published_at = str(result.get("published_date") or result.get("published_at") or "").strip()
    score = result.get("score")
    try:
        score_value = float(score) if score is not None else None
    except (TypeError, ValueError):
        score_value = None
    raw_json = json.dumps(result, ensure_ascii=False)[:8000]
    digest = content_hash(canonical, title, snippet, extracted)
    conn.execute(
        """
        INSERT OR IGNORE INTO web_evidence_docs (
            run_id, query_type, query, result_rank, url, canonical_url, title,
            source, published_at, retrieved_at, snippet, extracted_text, claim,
            evidence_type, stance, source_quality, score, content_hash, raw_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            query.query_type,
            query.query,
            rank,
            url,
            canonical,
            title,
            source,
            published_at,
            retrieved_at,
            compact_text(snippet, 600),
            extracted,
            claim,
            evidence_type,
            stance,
            quality,
            score_value,
            digest,
            raw_json,
            retrieved_at,
        ),
    )
    return {
        "query_type": query.query_type,
        "query": query.query,
        "rank": rank,
        "url": url,
        "canonical_url": canonical,
        "title": title,
        "source": source,
        "published_at": published_at,
        "claim": claim,
        "evidence_type": evidence_type,
        "stance": stance,
        "source_quality": quality,
        "score": score_value,
    }


def docs_for_run(conn: sqlite3.Connection, run_id: int, limit: int = 16) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT query_type, query, result_rank, url, canonical_url, title, source,
               published_at, claim, evidence_type, stance, source_quality, score
        FROM web_evidence_docs
        WHERE run_id = ?
        ORDER BY
            CASE source_quality WHEN 'primary_or_high_trust' THEN 0 WHEN 'mainstream_media' THEN 1 ELSE 2 END,
            result_rank ASC,
            id ASC
        LIMIT ?
        """,
        (run_id, limit),
    ).fetchall()
    return [
        {
            "query_type": row[0],
            "query": row[1],
            "rank": row[2],
            "url": row[3],
            "canonical_url": row[4],
            "title": row[5],
            "source": row[6],
            "published_at": row[7],
            "claim": row[8],
            "evidence_type": row[9],
            "stance": row[10],
            "source_quality": row[11],
            "score": row[12],
        }
        for row in rows
    ]


def summarize_pack(run_id: int, provider: str, mode: str, queries: list[EvidenceQuery], docs: list[dict[str, Any]]) -> dict[str, Any]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for doc in docs:
        buckets.setdefault(str(doc.get("evidence_type") or "unknown"), []).append(doc)
    conclusion_hints: list[str] = []
    if buckets.get("prior_coverage"):
        conclusion_hints.append("发现历史报道或二次传播线索，需判断旧闻/已定价风险。")
    if buckets.get("market_pricing"):
        conclusion_hints.append("发现市场定价相关线索，需结合股价与成交额判断是否已 price in。")
    if buckets.get("counter_evidence"):
        conclusion_hints.append("发现反向变量检索结果，需检查是否削弱原利好/利空方向。")
    if buckets.get("primary_source"):
        conclusion_hints.append("发现可能的一手/高可信来源，可用于确认事件源头。")
    if buckets.get("macro_background"):
        conclusion_hints.append("发现宏观背景线索，需区分产业变量与流动性/估值冲击。")
    return {
        "run_id": run_id,
        "provider": provider,
        "mode": mode,
        "queries": [query.__dict__ for query in queries],
        "documents": docs,
        "summary": {
            "doc_count": len(docs),
            "by_type": {key: len(value) for key, value in buckets.items()},
            "conclusion_hints": conclusion_hints,
        },
    }


def collect_web_evidence(
    conn: sqlite3.Connection,
    *,
    trigger_module: str,
    source: str,
    item: dict[str, Any],
    review: dict[str, Any],
    trigger_reason: str = "skeptic_review",
    mode: str = "realtime",
) -> dict[str, Any] | None:
    if not web_evidence_enabled():
        return None
    provider = provider_name()
    queries = build_queries(source, item, review, mode=mode)
    if not queries:
        return None
    run_id = create_run(
        conn,
        trigger_module=trigger_module,
        source=source,
        item=item,
        trigger_reason=trigger_reason,
        mode=mode,
        provider=provider,
        queries=queries,
    )
    try:
        seen_urls: set[str] = set()
        for query in queries:
            results = provider_search(provider, query)
            for rank, result in enumerate(results, start=1):
                canonical = canonicalize_url(str(result.get("url") or ""))
                if not canonical or canonical in seen_urls:
                    continue
                seen_urls.add(canonical)
                save_doc(conn, run_id, query, rank, result)
        conn.commit()
        finish_run(conn, run_id, status="ok")
        record_source_success(conn, "web_evidence", provider)
        conn.commit()
        docs = docs_for_run(conn, run_id)
        return summarize_pack(run_id, provider, mode, queries, docs)
    except Exception as exc:  # noqa: BLE001 - evidence retrieval must not break ingestion
        finish_run(conn, run_id, status="failed", error=str(exc))
        record_source_failure(conn, "web_evidence", provider, exc)
        conn.commit()
        raise


def prompt_pack(pack: dict[str, Any] | None, *, limit: int = 5000) -> str:
    if not pack:
        return "未启用或未获得联网证据。"
    compact = {
        "run_id": pack.get("run_id"),
        "provider": pack.get("provider"),
        "mode": pack.get("mode"),
        "summary": pack.get("summary"),
        "documents": [
            {
                "type": doc.get("evidence_type"),
                "stance": doc.get("stance"),
                "source_quality": doc.get("source_quality"),
                "source": doc.get("source"),
                "published_at": doc.get("published_at"),
                "title": doc.get("title"),
                "claim": doc.get("claim"),
                "url": doc.get("url"),
            }
            for doc in pack.get("documents", [])[:12]
        ],
    }
    return json.dumps(compact, ensure_ascii=False)[:limit]
