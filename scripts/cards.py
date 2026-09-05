"""Feishu card builders for Surveil notifications."""

from __future__ import annotations

from typing import Any
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from feishu_image import image_key_from_url
from link_enrichment import analysis_text_with_links, display_url
from market_card_view import decision_basis_reasons, interpretation_core
from media_sources import is_overseas_media_source, overseas_media_module
from post_analysis import analyze_post, company_label, extract_tickers


def truncate(value: str, limit: int) -> str:
    cleaned = " ".join(value.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3] + "..."


def md_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("*", "\\*").replace("_", "\\_")


def div_markdown(content: str) -> dict[str, Any]:
    return {
        "tag": "div",
        "text": {
            "tag": "lark_md",
            "content": content,
        },
    }


def note_text(content: str) -> dict[str, Any]:
    return {
        "tag": "note",
        "elements": [
            {
                "tag": "plain_text",
                "content": content,
            }
        ],
    }


def format_time(value: str) -> str:
    if not value:
        return "unknown"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        bj = parsed.astimezone(ZoneInfo("Asia/Shanghai"))
        utc = parsed.astimezone(timezone.utc)
        return f"{bj:%Y-%m-%d %H:%M:%S} 北京时间（UTC {utc:%Y-%m-%d %H:%M:%S}）"
    except ValueError:
        return value


def now_beijing() -> str:
    return datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S 北京时间")


def text_chunks(text: str, limit: int = 1300) -> list[str]:
    paragraphs = [part.strip() for part in text.replace("\r", "").split("\n") if part.strip()]
    if not paragraphs:
        paragraphs = [text.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        candidate = paragraph if not current else f"{current}\n\n{paragraph}"
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        while len(paragraph) > limit:
            chunks.append(paragraph[:limit])
            paragraph = paragraph[limit:]
        current = paragraph
    if current:
        chunks.append(current)
    return chunks


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def review_analysis(review: dict[str, Any]) -> dict[str, Any]:
    analysis = review.get("analysis")
    if isinstance(analysis, dict):
        return analysis
    return review


def thin_core_content(item: dict[str, Any], review: dict[str, Any]) -> str:
    unified = interpretation_core(review)
    if unified:
        return truncate(unified, 700)
    analysis = review_analysis(review)
    for value in (
        analysis.get("core_content"),
        review.get("daily_summary"),
        item.get("summary"),
        item.get("full_text"),
        item.get("title"),
    ):
        text = str(value or "").strip()
        if text:
            return truncate(text, 700)
    return ""


def value_directory_preview_lines(item: dict[str, Any]) -> list[str]:
    lines = [str(line).strip() for line in as_list(item.get("preview_lines")) if str(line).strip()]
    if lines:
        return lines[:4]
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
    preview = raw.get("value_directory_preview") if isinstance(raw.get("value_directory_preview"), dict) else {}
    facts = preview.get("facts") if isinstance(preview.get("facts"), dict) else {}
    if not facts:
        return []
    status = str(facts.get("status") or "")
    if status == "ok" and facts.get("core_content"):
        lines.append(f"第一页提取：{facts.get('core_content')}")
    elif status:
        lines.append(f"第一页提取：失败/不可用（{facts.get('error') or status}）")
    meta_parts = []
    for label, key in (("机构", "institution"), ("日期", "report_date"), ("方向", "stance"), ("研报动作", "research_action"), ("评级", "rating"), ("目标价", "target_price")):
        value = str(facts.get(key) or (facts.get("action") if key == "research_action" else "") or "").strip()
        if value and value.lower() != "unknown":
            meta_parts.append(f"{label}：{value}")
    if meta_parts:
        lines.append("；".join(meta_parts))
    points = [str(point).strip() for point in as_list(facts.get("key_points")) if str(point).strip()]
    if points:
        lines.append("要点：" + "；".join(points[:3]))
    return lines[:4]


def hide_value_directory_stock_preview(item: dict[str, Any], source: str) -> bool:
    if source != "value_directory_ib_stocks" and str(item.get("source_module") or "").strip() != "价值目录 / 国际投行-个股":
        return False
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
    preview = raw.get("value_directory_preview") if isinstance(raw.get("value_directory_preview"), dict) else {}
    facts = preview.get("facts") if isinstance(preview.get("facts"), dict) else {}
    return str(facts.get("status") or "") == "ok"


def cls_metadata_lines(item: dict[str, Any], review: dict[str, Any] | None = None) -> list[str]:
    metadata = item.get("cls_metadata")
    if not isinstance(metadata, dict):
        raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
        metadata = raw.get("cls_metadata")
    if not isinstance(metadata, dict) and isinstance(review, dict):
        review_raw = review.get("raw") if isinstance(review.get("raw"), dict) else {}
        enrichment = review_raw.get("_source_enrichment") if isinstance(review_raw.get("_source_enrichment"), dict) else {}
        metadata = enrichment.get("cls_metadata")
    if not isinstance(metadata, dict):
        return []

    type_value = str(metadata.get("type") or "").strip()
    product_label = str(metadata.get("product_label") or "").strip()
    share_img_name = str(metadata.get("share_img_name") or "").strip()
    targets: list[str] = []
    for target in as_list(metadata.get("author_targets")):
        if not isinstance(target, dict):
            continue
        name = str(target.get("name") or "").strip()
        code = str(target.get("code") or "").strip()
        label = " ".join(part for part in (name, code) if part)
        if label and label not in targets:
            targets.append(label)

    lines: list[str] = []
    header = []
    if type_value:
        header.append(f"type：{type_value}")
    if product_label:
        header.append(f"栏目：{product_label}")
    if header:
        lines.append("；".join(header))
    if share_img_name:
        lines.append(f"share_img：{share_img_name}")
    if targets:
        lines.append("author_extends：" + "；".join(targets))
    elif metadata.get("author_extends"):
        lines.append("author_extends：" + truncate(str(metadata.get("author_extends") or ""), 500))
    return lines


def build_market_item_card(
    source: str,
    item: dict[str, Any],
    review: dict[str, Any] | None = None,
) -> dict[str, Any]:
    review = review if isinstance(review, dict) else item.get("review")
    if not isinstance(review, dict):
        raise ValueError("market information card requires a unified review")
    title = str(item.get("title") or "")
    url = str(item.get("url") or "")
    text = str(item.get("full_text") or item.get("summary") or "")
    body_source = str(item.get("body_source") or "RSS")
    source_label = str(item.get("source_module") or source_module(source, url))
    push_basis, omitted_basis = decision_basis_reasons(review)
    core = thin_core_content(item, review)
    preview_lines = [] if hide_value_directory_stock_preview(item, source) else value_directory_preview_lines(item)
    cls_lines = cls_metadata_lines(item, review)
    elements: list[dict[str, Any]] = [
        div_markdown(f"**发送时间**：{md_escape(now_beijing())}"),
        div_markdown(f"**来源模块**：{md_escape(source_label)}"),
        div_markdown(f"**发布时间**：{md_escape(format_time(str(item.get('published_at', ''))))}"),
        {"tag": "hr"},
        div_markdown(f"**标题**\n{md_escape(title)}"),
    ]
    if core:
        elements.append(div_markdown(f"**核心内容**\n{md_escape(core)}"))
    if preview_lines:
        elements.append(div_markdown("**第一页提取**\n" + md_escape("\n".join(preview_lines))))
    if cls_lines:
        elements.append(div_markdown("**财联社元数据**\n" + md_escape("\n".join(cls_lines))))
    if push_basis:
        basis_lines = [f"- {truncate(reason, 180)}" for reason in push_basis]
        if omitted_basis:
            basis_lines.append(f"- 另命中 {omitted_basis} 项同级决策规则")
        elements.append(div_markdown("**推送依据**\n" + md_escape("\n".join(basis_lines))))
    if text:
        elements.append(div_markdown(f"**原文/摘要**\n{md_escape(truncate(text, 900))}"))
    elements.append(note_text(f"正文来源：{body_source}；完整决策审计已写入后台。"))
    if url:
        elements.append(
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "打开原文"},
                        "type": "primary",
                        "multi_url": {
                            "url": url,
                            "pc_url": url,
                            "ios_url": url,
                            "android_url": url,
                        },
                    }
                ],
            }
        )
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "green",
            "title": {
                "tag": "plain_text",
                "content": item.get("source_display") or source_label or f"{source} 新文章",
            },
        },
        "elements": elements,
    }


def build_serenity_card(post: dict[str, Any]) -> dict[str, Any]:
    text = post.get("full_text") or post.get("text") or ""
    preview = truncate(text, 1000)
    url = post.get("url") or ""
    tickers = extract_tickers(text)
    metrics = post.get("public_metrics") or {}
    media = post.get("_media") or []
    links = post.get("_links") or []
    analysis_lines = analyze_post(analysis_text_with_links(text, links))

    elements: list[dict[str, Any]] = [
        div_markdown(f"**发送时间**：{md_escape(now_beijing())}"),
        div_markdown(f"**发布时间**：{md_escape(format_time(str(post.get('created_at', ''))))}"),
        div_markdown("**内容类型**：X API 公开帖。当前 X API 没有返回“付费订阅内容”标记，因此不能判定为付费订阅帖。"),
    ]
    if metrics:
        elements.append(
            div_markdown(
                "**互动**："
                f"回复 {metrics.get('reply_count')} / "
                f"转发 {metrics.get('retweet_count')} / "
                f"喜欢 {metrics.get('like_count')} / "
                f"引用 {metrics.get('quote_count')}"
            )
        )
    if tickers:
        elements.append(div_markdown("**涉及标的**：" + md_escape("；".join(company_label(ticker) for ticker in tickers))))

    elements.append({"tag": "hr"})
    full_chunks = text_chunks(text)
    for index, chunk in enumerate(full_chunks, start=1):
        title = "**原文全文**" if index == 1 else f"**原文全文（续 {index}）**"
        elements.append(div_markdown(f"{title}\n{md_escape(chunk)}"))

    if links:
        elements.append({"tag": "hr"})
        elements.append(div_markdown("**外链内容**"))
        for index, link in enumerate(links, start=1):
            effective_url = str(link.get("effective_url") or link.get("url") or "")
            title = str(link.get("title") or "").strip()
            description = str(link.get("description") or "").strip()
            body = str(link.get("text") or "").strip()
            status = str(link.get("status") or "unknown")
            error = str(link.get("error") or "").strip()
            parts = [f"**链接 {index}**：{md_escape(display_url(effective_url))}"]
            if title:
                parts.append(f"标题：{md_escape(title)}")
            if description:
                parts.append(f"摘要：{md_escape(description)}")
            if body:
                parts.append(f"正文摘录：{md_escape(truncate(body, 900))}")
            if status != "ok" or error:
                parts.append(f"抓取状态：{md_escape(status)}；{md_escape(error or '未抽取到正文')}")
            elements.append(div_markdown("\n".join(parts)))
    elements.extend(
        [
            {"tag": "hr"},
            div_markdown("**快速解读**\n" + md_escape("\n".join(analysis_lines[1:]))),
        ]
    )

    embedded_count = 0
    for item in media[:3]:
        media_url = item.get("url")
        if not media_url:
            continue
        image_key = image_key_from_url(media_url)
        if not image_key:
            continue
        elements.append(
            {
                "tag": "img",
                "img_key": image_key,
                "alt": {
                    "tag": "plain_text",
                    "content": "Serenity 配图",
                },
                "mode": "fit_horizontal",
            }
        )
        embedded_count += 1

    actions: list[dict[str, Any]] = []
    if url:
        actions.append(
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "打开推文"},
                "type": "primary",
                "multi_url": {
                    "url": url,
                    "pc_url": url,
                    "ios_url": url,
                    "android_url": url,
                },
            }
        )
    for index, item in enumerate(media[:3], start=1):
        media_url = item.get("url")
        if not media_url:
            continue
        actions.append(
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": f"打开图片 {index}"},
                "type": "default",
                "multi_url": {
                    "url": media_url,
                    "pc_url": media_url,
                    "ios_url": media_url,
                    "android_url": media_url,
                },
            }
        )
    for index, link in enumerate(links[:3], start=1):
        link_url = str(link.get("effective_url") or link.get("url") or "")
        if not link_url:
            continue
        actions.append(
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": f"打开外链 {index}"},
                "type": "default",
                "multi_url": {
                    "url": link_url,
                    "pc_url": link_url,
                    "ios_url": link_url,
                    "android_url": link_url,
                },
            }
        )
    if actions:
        elements.append({"tag": "action", "actions": actions})

    if media and embedded_count == 0:
        elements.append(note_text("未配置飞书应用凭证或图片上传失败，已回退为图片按钮链接。"))

    return {
        "config": {
            "wide_screen_mode": True,
        },
        "header": {
            "template": "blue",
            "title": {
                "tag": "plain_text",
                "content": "Serenity 新帖",
            },
        },
        "elements": elements,
    }


def source_module(source: str, url: str) -> str:
    if is_overseas_media_source(source):
        return overseas_media_module(source)
    official_sources = {
        "openai_news": "OpenAI / 官方新闻",
        "nvidia_blog": "NVIDIA / 官方博客",
        "nvidia_developer_blog": "NVIDIA / Developer Blog",
        "samsung_semiconductor_news": "Samsung Semiconductor / 官方新闻",
        "samsung_global_semiconductor": "Samsung Newsroom / Semiconductors",
        "skhynix_newsroom": "SK hynix / Newsroom",
        "micron_news_releases": "Micron / News Releases",
    }
    if source in official_sources:
        return official_sources[source]
    if source == "trendforce_page":
        if "/research/download/" in url:
            return "TrendForce / Research Report 或 Selected Topics"
        if "/news/" in url:
            return "TrendForce / News"
        if "/presscenter/analysis" in url:
            return "TrendForce / Press Centre / In-Depth Analyses"
        return "TrendForce / 官方页面监控"
    if source.startswith("trendforce_"):
        if "/presscenter/" in url:
            return "TrendForce / Press Centre / News"
        if "/research/" in url:
            return "TrendForce / Research Report"
        if "selected_topics" in url:
            return "TrendForce / Selected Topics"
        return "TrendForce / RSS"
    if source == "semianalysis":
        return "SemiAnalysis / RSS"
    return source
