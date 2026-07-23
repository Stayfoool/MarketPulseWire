#!/usr/bin/env python3
"""Regression checks for the ValueList browser-backed monitor."""

from __future__ import annotations

import os
import inspect
import sqlite3
import sys
import types
from pathlib import Path
from tempfile import TemporaryDirectory

import value_directory_preview
import value_directory_browser
import value_directory_monitor
from market_item import AdmissionResult, DecisionResult, InterpretationResult, MarketFlowResult
from market_runtime import MarketProcessOutcome
from source_profiles import runtime_source_profile
from value_directory_browser import (
    BrowserConfig,
    BrowserLaunchFailed,
    BrowserShutdownTimeout,
    classify_page_state,
    close_browser_context,
    collect_sources_with_previews,
    dedupe_entries,
    evaluate_list_payload_with_empty_wait,
    launch_browser_context,
    normalize_entry,
    profile_lock_state,
    source_config,
    wait_for_profile_release,
)
from value_directory_preview import (
    apply_preview_llm_response_preferences,
    apply_preview_to_item,
    extract_preview_facts,
    fallback_facts,
    flatten_paddleocr_result,
    paddle_ocr_instance,
)


def test_normalize_entry_extracts_stable_id_and_utc_date() -> None:
    item = normalize_entry(
        {
            "published": "2026-07-10",
            "title": "高盛-宁德时代(300750.SZ)：首次覆盖评为买入(摘要)-20260709【35页】",
            "url": "https://www.valuelist.cn/862550.html",
        }
    )
    assert item is not None
    assert item["id"] == "862550"
    assert item["published_at"] == "2026-07-09T16:00:00+00:00"
    assert item["source_module"] == "价值目录 / 国际投行-个股"
    assert item["full_text"].startswith("高盛-宁德时代")


def test_normalize_entry_supports_industry_macro_source() -> None:
    source = source_config("value_directory_ib_industry_macro")
    item = normalize_entry(
        {
            "published": "2026-07-11",
            "title": "瑞银-亚太科技策略：Agentic AI to carry Semis&Hardware further-20260701【198页】",
            "url": "https://www.valuelist.cn/862079.html",
        },
        source,
    )
    assert item is not None
    assert item["id"] == "862079"
    assert item["source_module"] == "价值目录 / 国际投行-行业宏观"
    assert item["categories"] == ["国际投行-行业宏观"]
    assert item["raw"]["source"] == "value_directory_ib_industry_macro"


def test_page_state_detection_separates_waf_login_and_empty() -> None:
    assert classify_page_state("宝塔防火墙正在检查您的访问", article_count=0) == "waf"
    assert classify_page_state("请先 登录 后继续", article_count=0, url="https://www.valuelist.cn/login") == "login"
    assert classify_page_state("正常页面", article_count=0) == "empty"
    assert classify_page_state("正常页面", article_count=3) == "ok"


class _ListWaitTimeout(Exception):
    pass


class _ListPage:
    def __init__(self, payloads: list[dict[str, object]], *, timeout: bool = False) -> None:
        self.payloads = list(payloads)
        self.timeout = timeout
        self.waits: list[tuple[str, int]] = []

    def evaluate(self, _script, _limit):
        if len(self.payloads) > 1:
            return self.payloads.pop(0)
        return self.payloads[0]

    def wait_for_selector(self, selector: str, *, timeout: int) -> None:
        self.waits.append((selector, timeout))
        if self.timeout:
            raise _ListWaitTimeout()


def test_empty_list_waits_once_for_delayed_articles() -> None:
    page = _ListPage(
        [
            {"url": "https://www.valuelist.cn/list", "title": "正常页面", "bodySample": "", "articleCount": 0},
            {"url": "https://www.valuelist.cn/list", "title": "正常页面", "bodySample": "", "articleCount": 1},
        ]
    )
    payload = evaluate_list_payload_with_empty_wait(page, 30, 45_000, timeout_error=_ListWaitTimeout)
    assert payload["articleCount"] == 1
    assert page.waits == [("article", 15_000)]


def test_persistent_empty_list_remains_empty_after_bounded_wait() -> None:
    page = _ListPage(
        [{"url": "https://www.valuelist.cn/list", "title": "正常页面", "bodySample": "", "articleCount": 0}],
        timeout=True,
    )
    payload = evaluate_list_payload_with_empty_wait(page, 30, 5_000, timeout_error=_ListWaitTimeout)
    assert payload["articleCount"] == 0
    assert page.waits == [("article", 5_000)]


def test_waf_and_login_states_do_not_wait_for_articles() -> None:
    for payload in (
        {"url": "https://www.valuelist.cn/list", "title": "人机验证", "bodySample": "宝塔防火墙", "articleCount": 0},
        {"url": "https://www.valuelist.cn/login", "title": "登录", "bodySample": "请登录", "articleCount": 0},
    ):
        page = _ListPage([payload])
        result = evaluate_list_payload_with_empty_wait(page, 30, 45_000, timeout_error=_ListWaitTimeout)
        assert result == payload
        assert page.waits == []


def test_profile_lock_state_distinguishes_live_and_dead_same_host_owner() -> None:
    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        profile = root / "profile"
        proc = root / "proc"
        profile.mkdir()
        (proc / "net").mkdir(parents=True)
        (proc / "net" / "unix").write_text(
            "Num RefCount Protocol Flags Type St Inode Path\n",
            encoding="utf-8",
        )
        (profile / "SingletonLock").symlink_to("test-host-123")
        dead = profile_lock_state(profile, proc_root=proc, hostname="test-host")
        (proc / "123").mkdir()
        live = profile_lock_state(profile, proc_root=proc, hostname="test-host")
    assert dead["lock_pid"] == 123
    assert dead["lock_pid_alive"] is False
    assert live["lock_pid_alive"] is True


def test_profile_lock_state_finds_registered_socket_holder() -> None:
    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        profile = root / "profile"
        proc = root / "proc"
        socket_path = root / "SingletonSocket"
        profile.mkdir()
        socket_path.touch()
        (profile / "SingletonSocket").symlink_to(socket_path)
        (proc / "net").mkdir(parents=True)
        (proc / "net" / "unix").write_text(
            f"Num RefCount Protocol Flags Type St Inode Path\n"
            f"000: 00000002 00000000 00010000 0001 01 4242 {socket_path}\n",
            encoding="utf-8",
        )
        fd_dir = proc / "456" / "fd"
        fd_dir.mkdir(parents=True)
        (fd_dir / "7").symlink_to("socket:[4242]")
        state = profile_lock_state(profile, proc_root=proc, hostname="test-host")
    assert state["socket_exists"] is True
    assert state["socket_registered"] is True
    assert state["socket_holder_pids"] == [456]


class _FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


def test_wait_for_profile_release_observes_owner_exit() -> None:
    states = [
        {"lock_pid_alive": True, "socket_registered": True, "socket_holder_pids": [123]},
        {"lock_pid_alive": True, "socket_registered": True, "socket_holder_pids": [123]},
        {"lock_pid_alive": False, "socket_registered": False, "socket_holder_pids": [], "lock_exists": True},
    ]
    clock = _FakeClock()

    def state_reader(_profile: Path) -> dict[str, object]:
        if len(states) > 1:
            return states.pop(0)
        return states[0]

    released, state = wait_for_profile_release(
        Path("/private/profile"),
        timeout_seconds=1,
        poll_seconds=0.1,
        state_reader=state_reader,
        monotonic=clock.monotonic,
        sleeper=clock.sleep,
    )
    assert released is True
    assert state["lock_pid_alive"] is False


class _FakeContext:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.closed = False

    def close(self) -> None:
        self.closed = True
        if self.error:
            raise self.error


def test_close_browser_context_reports_live_owner_timeout() -> None:
    config = BrowserConfig(Path("/private/profile"), None, False, 45_000)
    context = _FakeContext()
    saved_wait = value_directory_browser.wait_for_profile_release
    try:
        value_directory_browser.wait_for_profile_release = lambda _profile: (
            False,
            {
                "lock_exists": True,
                "lock_target": "host-123",
                "lock_pid_alive": True,
                "socket_registered": True,
                "socket_holder_pids": [123],
            },
        )
        try:
            close_browser_context(context, config)
        except BrowserShutdownTimeout as exc:
            error = str(exc)
        else:
            raise AssertionError("live profile owner must fail close")
    finally:
        value_directory_browser.wait_for_profile_release = saved_wait
    assert context.closed is True
    assert "host-123" in error
    assert "123" in error


def test_launch_browser_context_retains_bounded_underlying_error_and_lock_state() -> None:
    class FailingChromium:
        def launch_persistent_context(self, **_kwargs):
            raise RuntimeError("ProcessSingleton profile already in use")

    class FailingPlaywright:
        chromium = FailingChromium()

    with TemporaryDirectory() as tmpdir:
        profile = Path(tmpdir)
        (profile / "SingletonLock").symlink_to("test-host-999999")
        config = BrowserConfig(profile, "/missing/chromium", False, 45_000)
        try:
            launch_browser_context(FailingPlaywright(), config)
        except BrowserLaunchFailed as exc:
            error = str(exc)
        else:
            raise AssertionError("launch failure must retain diagnostics")
    assert "ProcessSingleton profile already in use" in error
    assert "test-host-999999" in error
    assert '"executable_exists":false' in error
    assert "SingletonCookie" not in error


class _BrowserManager:
    def __init__(self) -> None:
        self.playwright = types.SimpleNamespace(chromium=object())

    def __enter__(self):
        return self.playwright

    def __exit__(self, *_args):
        return False


def test_multi_source_collection_uses_one_browser_and_continues_after_detail_failure() -> None:
    events: list[str] = []
    context = types.SimpleNamespace(pages=[object()])
    original_config = value_directory_browser.browser_config
    original_launch = value_directory_browser.launch_browser_context
    original_close = value_directory_browser.close_browser_context
    original_entries = value_directory_browser.collect_entries_from_page
    original_preview = value_directory_browser.collect_preview_from_page
    try:
        value_directory_browser.browser_config = lambda: BrowserConfig(Path("/tmp/profile"), None, True, 45_000)
        value_directory_browser.launch_browser_context = lambda *_args: events.append("launch") or context
        value_directory_browser.close_browser_context = lambda *_args: events.append("close")

        def fake_entries(_page, source, **_kwargs):
            events.append(f"list:{source.source_id}")
            return [{"id": source.source_id, "url": f"https://www.valuelist.cn/{source.source_id}.html"}]

        def fake_preview(_page, url, **_kwargs):
            source_id = Path(url).stem
            events.append(f"preview:{source_id}")
            if source_id == "value_directory_ib_stocks":
                raise RuntimeError("detail unavailable")
            return {"state": "ok", "previewImages": [{"src": "https://img.valuelist.cn/preview.jpg"}]}

        value_directory_browser.collect_entries_from_page = fake_entries
        value_directory_browser.collect_preview_from_page = fake_preview
        result = collect_sources_with_previews(
            ["value_directory_ib_stocks", "value_directory_ib_industry_macro"],
            limit=10,
            preview_selector=lambda _source, _item: True,
            playwright_factory=_BrowserManager,
            timeout_error=TimeoutError,
        )
    finally:
        value_directory_browser.browser_config = original_config
        value_directory_browser.launch_browser_context = original_launch
        value_directory_browser.close_browser_context = original_close
        value_directory_browser.collect_entries_from_page = original_entries
        value_directory_browser.collect_preview_from_page = original_preview

    assert events == [
        "launch",
        "list:value_directory_ib_stocks",
        "list:value_directory_ib_industry_macro",
        "preview:value_directory_ib_stocks",
        "preview:value_directory_ib_industry_macro",
        "close",
    ]
    assert ("value_directory_ib_stocks", "value_directory_ib_stocks") in result.preview_errors
    assert ("value_directory_ib_industry_macro", "value_directory_ib_industry_macro") in result.previews


def test_multi_source_collection_keeps_list_failure_attributable() -> None:
    context = types.SimpleNamespace(pages=[object()])
    original_config = value_directory_browser.browser_config
    original_launch = value_directory_browser.launch_browser_context
    original_close = value_directory_browser.close_browser_context
    original_entries = value_directory_browser.collect_entries_from_page
    try:
        value_directory_browser.browser_config = lambda: BrowserConfig(Path("/tmp/profile"), None, True, 45_000)
        value_directory_browser.launch_browser_context = lambda *_args: context
        value_directory_browser.close_browser_context = lambda *_args: None

        def fake_entries(_page, source, **_kwargs):
            if source.source_id == "value_directory_ib_stocks":
                raise RuntimeError("list unavailable")
            return [{"id": "macro-1", "url": "https://www.valuelist.cn/macro-1.html"}]

        value_directory_browser.collect_entries_from_page = fake_entries
        result = collect_sources_with_previews(
            ["value_directory_ib_stocks", "value_directory_ib_industry_macro"],
            limit=10,
            preview_selector=lambda _source, _item: False,
            playwright_factory=_BrowserManager,
            timeout_error=TimeoutError,
        )
    finally:
        value_directory_browser.browser_config = original_config
        value_directory_browser.launch_browser_context = original_launch
        value_directory_browser.close_browser_context = original_close
        value_directory_browser.collect_entries_from_page = original_entries

    assert "value_directory_ib_stocks" in result.source_errors
    assert result.entries_by_source["value_directory_ib_industry_macro"][0]["id"] == "macro-1"


def test_dedupe_entries_keeps_first_valid_url() -> None:
    rows = dedupe_entries(
        [
            {"title": "A", "url": "https://www.valuelist.cn/1.html", "published": "2026-07-10"},
            {"title": "A duplicate", "url": "https://www.valuelist.cn/1.html", "published": "2026-07-10"},
            {"title": "", "url": "https://www.valuelist.cn/2.html", "published": "2026-07-10"},
        ]
    )
    assert len(rows) == 1
    assert rows[0]["id"] == "1"
    assert rows[0]["title"] == "A"


def test_shadow_payload_marks_seen_and_reviewed_without_delivery() -> None:
    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "surveil.sqlite3"
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE seen_items (
                    source TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    url TEXT,
                    title TEXT,
                    summary TEXT,
                    published_at TEXT,
                    first_seen_at TEXT,
                    PRIMARY KEY (source, item_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE article_reviews (
                    source TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    gate_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (source, item_id)
                )
                """
            )
            conn.execute(
                """
                INSERT INTO seen_items (
                    source, item_id, url, title, summary, published_at, first_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("value_directory_ib_stocks", "862550", "", "", "", "", "2026-07-10T00:00:00+00:00"),
            )
            conn.execute(
                "INSERT INTO article_reviews VALUES (?, ?, ?, ?, ?)",
                ("value_directory_ib_stocks", "862550", "demo", "{}", "2026-07-10T00:00:00+00:00"),
            )
        original_db = value_directory_monitor.DB_PATH
        try:
            value_directory_monitor.DB_PATH = db_path
            payload = value_directory_monitor.shadow_payload(
                [
                    {
                        "id": "862550",
                        "url": "https://www.valuelist.cn/862550.html",
                        "title": "高盛-宁德时代：首次覆盖买入",
                        "summary": "高盛-宁德时代：首次覆盖买入",
                        "published_at": "2026-07-09T16:00:00+00:00",
                    }
                ],
                started_at="2026-07-10T00:00:00+00:00",
            )
        finally:
            value_directory_monitor.DB_PATH = original_db
    assert payload["sent_feishu"] is False
    assert payload["ran_llm_review"] is False
    assert payload["candidates"][0]["already_seen"] is True
    assert payload["candidates"][0]["already_reviewed"] is True


def test_source_profile_registers_value_directory() -> None:
    profile = runtime_source_profile("value_directory_ib_stocks")
    assert profile is not None
    assert profile["category"] == "research_industry_media"
    assert "surveil-value-directory.timer" in profile["service_units"]
    assert profile["skeptic_enabled"] is False
    macro = runtime_source_profile("value_directory_ib_industry_macro")
    assert macro is not None
    assert macro["name"] == "价值目录 / 国际投行-行业宏观"
    assert "第一页预览" in macro["fetch_range"]


def test_preview_failure_is_recorded_without_fake_summary() -> None:
    item = {
        "id": "862592",
        "url": "https://www.valuelist.cn/862592.html",
        "title": "高盛-交易思路：做多中国人工智能价值链（GSXACART）-[GSX] Trade Idea：Long China AI Value Chain(GSXACART)-20260626【1页】",
        "summary": "title only",
        "raw": {},
    }
    preview = {"state": "ok", "previewImages": [{"src": "https://img.valuelist.cn/demo.jpg"}]}
    facts = fallback_facts(item, preview, RuntimeError("vision unavailable"))
    enriched = apply_preview_to_item(item, preview, facts)
    assert enriched["summary"] == "title only"
    assert enriched["raw"]["value_directory_preview"]["facts"]["status"] == "failed"
    assert "失败/不可用" in enriched["preview_lines"][0]


def test_paddleocr_result_flatten_supports_common_shapes() -> None:
    v2_result = [
        [
            [[[0, 0], [1, 0], [1, 1], [0, 1]], ("Rating Buy", 0.98)],
            [[[0, 2], [1, 2], [1, 3], [0, 3]], ("Target price CNY 1,325.00", 0.96)],
        ]
    ]
    v3_result = [{"rec_texts": ["Nomura", "Implied upside +20.6%"], "rec_scores": [0.99, 0.95]}]
    assert flatten_paddleocr_result(v2_result) == [
        ("Rating Buy", 0.98),
        ("Target price CNY 1,325.00", 0.96),
    ]
    assert flatten_paddleocr_result(v3_result) == [
        ("Nomura", 0.99),
        ("Implied upside +20.6%", 0.95),
    ]


def test_paddleocr_instance_retries_unknown_argument_errors() -> None:
    calls: list[dict[str, object]] = []

    class FakePaddleOCR:
        def __init__(self, **kwargs):
            calls.append(kwargs)
            if "show_log" in kwargs:
                raise ValueError("Unknown argument: show_log")
            self.kwargs = kwargs

    original_module = sys.modules.get("paddleocr")
    original_instance = value_directory_preview._PADDLE_OCR
    try:
        sys.modules["paddleocr"] = types.SimpleNamespace(PaddleOCR=FakePaddleOCR)
        value_directory_preview._PADDLE_OCR = None
        engine = paddle_ocr_instance()
    finally:
        value_directory_preview._PADDLE_OCR = original_instance
        if original_module is None:
            sys.modules.pop("paddleocr", None)
        else:
            sys.modules["paddleocr"] = original_module
    assert isinstance(engine, FakePaddleOCR)
    assert calls[0]["show_log"] is False
    assert calls[1]["use_textline_orientation"] is True


def test_preview_llm_policy_disables_deepseek_thinking() -> None:
    original_thinking = os.environ.get("LLM_THINKING_TYPE")
    original_allow = os.environ.get("LLM_ALLOW_DEEPSEEK_THINKING")
    original_json = os.environ.get("LLM_RESPONSE_FORMAT_JSON")
    try:
        os.environ["LLM_THINKING_TYPE"] = "enabled"
        os.environ.pop("LLM_ALLOW_DEEPSEEK_THINKING", None)
        os.environ.pop("LLM_RESPONSE_FORMAT_JSON", None)
        payload: dict[str, object] = {}
        apply_preview_llm_response_preferences(
            payload,
            base_url="https://api.deepseek.com",
            model="deepseek-v4-pro",
        )
    finally:
        if original_thinking is None:
            os.environ.pop("LLM_THINKING_TYPE", None)
        else:
            os.environ["LLM_THINKING_TYPE"] = original_thinking
        if original_allow is None:
            os.environ.pop("LLM_ALLOW_DEEPSEEK_THINKING", None)
        else:
            os.environ["LLM_ALLOW_DEEPSEEK_THINKING"] = original_allow
        if original_json is None:
            os.environ.pop("LLM_RESPONSE_FORMAT_JSON", None)
        else:
            os.environ["LLM_RESPONSE_FORMAT_JSON"] = original_json

    assert payload["thinking"] == {"type": "disabled"}
    assert payload["response_format"] == {"type": "json_object"}


def test_preview_ocr_text_path_uses_text_llm_without_vision() -> None:
    item = {
        "id": "862591",
        "url": "https://www.valuelist.cn/862591.html",
        "title": "野村-中际旭创(300308.SZ)：我们预计2027年后将实现长期增长-20260706【10页】",
        "summary": "title only",
        "source_module": "价值目录 / 国际投行-个股",
        "published_at": "2026-07-10T00:00:00+00:00",
    }
    preview = {
        "state": "ok",
        "articleText": "页面可见标题",
        "previewImages": [{"src": "https://img.valuelist.cn/862591.jpg"}],
    }
    original_ocr = value_directory_preview.extract_ocr_text
    original_text_llm = value_directory_preview.call_preview_text_llm
    original_vision_llm = value_directory_preview.call_preview_vision_llm
    original_ocr_env = os.environ.get("VALUE_DIRECTORY_PREVIEW_OCR_ENABLED")
    original_vision_env = os.environ.get("VALUE_DIRECTORY_PREVIEW_VISION_FALLBACK_ENABLED")
    try:
        os.environ["VALUE_DIRECTORY_PREVIEW_OCR_ENABLED"] = "1"
        os.environ["VALUE_DIRECTORY_PREVIEW_VISION_FALLBACK_ENABLED"] = "0"
        value_directory_preview.extract_ocr_text = lambda _url: {
            "engine": "paddleocr",
            "status": "ok",
            "text": "Action: Maintain Buy; raise TP to CNY 1,325, implying 20.6% upside\nRating Buy\nTarget price CNY 1,325.00",
            "line_count": 3,
        }

        def fake_text_llm(_item, _preview, *, ocr_text=""):
            assert "CNY 1,325" in ocr_text
            return (
                {
                    "core_content": "野村维持中际旭创买入评级并上调目标价至 CNY 1,325。",
                    "stance": "bullish",
                    "research_action": "buy",
                    "institution": "野村",
                    "report_date": "2026-07-06",
                    "rating": "Buy",
                    "target_price": "CNY 1,325.00",
                    "targets": ["中际旭创", "2.4T/3.2T 光模块", "NPO/CPO"],
                    "key_points": ["维持 Buy", "目标价上调至 CNY 1,325", "隐含 20.6% 上行空间"],
                    "preview_basis": "visible_first_page_ocr",
                    "confidence": "high",
                },
                "deepseek-v4-pro",
            )

        value_directory_preview.call_preview_text_llm = fake_text_llm
        value_directory_preview.call_preview_vision_llm = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("vision fallback should stay disabled")
        )
        facts = extract_preview_facts(item, preview)
    finally:
        value_directory_preview.extract_ocr_text = original_ocr
        value_directory_preview.call_preview_text_llm = original_text_llm
        value_directory_preview.call_preview_vision_llm = original_vision_llm
        if original_ocr_env is None:
            os.environ.pop("VALUE_DIRECTORY_PREVIEW_OCR_ENABLED", None)
        else:
            os.environ["VALUE_DIRECTORY_PREVIEW_OCR_ENABLED"] = original_ocr_env
        if original_vision_env is None:
            os.environ.pop("VALUE_DIRECTORY_PREVIEW_VISION_FALLBACK_ENABLED", None)
        else:
            os.environ["VALUE_DIRECTORY_PREVIEW_VISION_FALLBACK_ENABLED"] = original_vision_env

    assert facts["status"] == "ok"
    assert facts["model"] == "deepseek-v4-pro"
    assert facts["target_price"] == "CNY 1,325.00"
    assert facts["research_action"] == "buy"
    assert "action" not in facts
    assert facts["ocr"]["engine"] == "paddleocr"
    assert facts["preview_basis"] == "visible_first_page_ocr"


def test_preview_ocr_failure_falls_back_without_blocking() -> None:
    item = {
        "id": "862591",
        "title": "野村-中际旭创(300308.SZ)：我们预计2027年后将实现长期增长-20260706【10页】",
        "summary": "title only",
    }
    preview = {"state": "ok", "previewImages": [{"src": "https://img.valuelist.cn/862591.jpg"}]}
    original_ocr = value_directory_preview.extract_ocr_text
    original_vision_llm = value_directory_preview.call_preview_vision_llm
    original_ocr_env = os.environ.get("VALUE_DIRECTORY_PREVIEW_OCR_ENABLED")
    original_vision_env = os.environ.get("VALUE_DIRECTORY_PREVIEW_VISION_FALLBACK_ENABLED")
    try:
        os.environ["VALUE_DIRECTORY_PREVIEW_OCR_ENABLED"] = "1"
        os.environ["VALUE_DIRECTORY_PREVIEW_VISION_FALLBACK_ENABLED"] = "0"
        value_directory_preview.extract_ocr_text = lambda _url: {
            "engine": "paddleocr",
            "status": "too_short",
            "text": "",
            "error": "OCR 文字过短：0 chars",
        }
        value_directory_preview.call_preview_vision_llm = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("vision fallback should stay disabled")
        )
        facts = extract_preview_facts(item, preview)
    finally:
        value_directory_preview.extract_ocr_text = original_ocr
        value_directory_preview.call_preview_vision_llm = original_vision_llm
        if original_ocr_env is None:
            os.environ.pop("VALUE_DIRECTORY_PREVIEW_OCR_ENABLED", None)
        else:
            os.environ["VALUE_DIRECTORY_PREVIEW_OCR_ENABLED"] = original_ocr_env
        if original_vision_env is None:
            os.environ.pop("VALUE_DIRECTORY_PREVIEW_VISION_FALLBACK_ENABLED", None)
        else:
            os.environ["VALUE_DIRECTORY_PREVIEW_VISION_FALLBACK_ENABLED"] = original_vision_env

    assert facts["status"] == "failed"
    assert facts["model"] == "preview_failed"
    assert facts["ocr"]["status"] == "too_short"
    assert "OCR 文字过短" in facts["error"]


class _DummyContext:
    def __enter__(self):
        return object()

    def __exit__(self, *_):
        return False


def admitted_context(*families: str):
    admission = AdmissionResult(
        status="admitted",
        reason_code="content_scope_match",
        matched_families=tuple(families or ("semiconductor_ai",)),
        evidence=(),
        config_version="test-config",
    )
    return types.SimpleNamespace(result=admission, portfolio=object())


def test_recheck_uses_enriched_item_without_a_preliminary_decision_gate() -> None:
    item = {
        "id": "862592",
        "url": "https://www.valuelist.cn/862592.html",
        "title": "高盛-交易思路：做多中国人工智能价值链",
        "summary": "Trade Idea",
        "published_at": "2026-07-09T16:00:00+00:00",
    }
    existing = {"push_now": False, "pushed_at": "", "importance": "medium"}
    original_connect = value_directory_monitor.connect_db
    original_existing = value_directory_monitor.article_review_exists
    original_process = value_directory_monitor.process_market_item
    original_lifecycle = value_directory_monitor.set_seen_item_lifecycle_if_present
    original_admission = value_directory_monitor.production_admission_context
    calls: list[dict[str, object]] = []
    try:
        value_directory_monitor.connect_db = lambda: _DummyContext()
        value_directory_monitor.article_review_exists = lambda *_: existing
        value_directory_monitor.set_seen_item_lifecycle_if_present = lambda *_args, **_kwargs: None
        value_directory_monitor.production_admission_context = lambda *_args, **_kwargs: admitted_context()

        def fake_process(normalized, raw_item, **kwargs):
            calls.append({"normalized": normalized, "raw_item": raw_item, **kwargs})
            decision = DecisionResult(action="archive", importance="low", reason="当前规则复核。")
            return MarketProcessOutcome(
                flow_result=MarketFlowResult(
                    item=normalized,
                    decision=decision,
                    interpretation=InterpretationResult(),
                ),
                inserted=False,
                storage_ref={"store_kind": "article_reviews", "item_id": raw_item["id"]},
            )

        value_directory_monitor.process_market_item = fake_process
        assert value_directory_monitor.review_and_maybe_push(item, recheck_rules=True) is False
    finally:
        value_directory_monitor.connect_db = original_connect
        value_directory_monitor.article_review_exists = original_existing
        value_directory_monitor.process_market_item = original_process
        value_directory_monitor.set_seen_item_lifecycle_if_present = original_lifecycle
        value_directory_monitor.production_admission_context = original_admission
    assert len(calls) == 1
    assert calls[0]["reprocess_existing"] is True


def test_new_item_uses_unified_market_runtime_after_preview_enrichment() -> None:
    source = source_config("value_directory_ib_industry_macro")
    item = {
        "id": "862079",
        "url": "https://www.valuelist.cn/862079.html",
        "title": "瑞银-亚太科技策略：Agentic AI to carry Semis&Hardware further",
        "summary": "title only",
        "published_at": "2026-07-11T00:00:00+00:00",
        "raw": {},
    }
    calls: list[dict[str, object]] = []
    original_connect = value_directory_monitor.connect_db
    original_existing = value_directory_monitor.article_review_exists
    original_enrich = value_directory_monitor.enrich_item_with_preview
    original_process = value_directory_monitor.process_market_item
    original_lifecycle = value_directory_monitor.set_seen_item_lifecycle_if_present
    original_admission = value_directory_monitor.production_admission_context
    try:
        value_directory_monitor.connect_db = lambda: _DummyContext()
        value_directory_monitor.article_review_exists = lambda *_: None
        value_directory_monitor.set_seen_item_lifecycle_if_present = lambda *_args, **_kwargs: None
        value_directory_monitor.production_admission_context = lambda *_args, **_kwargs: admitted_context()
        def fake_enrich(raw_item):
            enriched = dict(raw_item)
            enriched["summary"] = "瑞银认为智能体 AI 将继续推动半导体与硬件上行。"
            enriched["raw"] = {
                "value_directory_preview": {
                    "facts": {
                        "status": "ok",
                        "core_content": enriched["summary"],
                        "research_action": "overweight",
                        "targets": ["半导体", "AI 硬件"],
                        "model": "test-preview-model",
                    }
                }
            }
            return enriched

        def fake_process(normalized, raw_item, **kwargs):
            calls.append({"normalized": normalized, "raw_item": raw_item, **kwargs})
            decision = DecisionResult(action="push", importance="high", reason="价值目录规则命中。")
            return MarketProcessOutcome(
                flow_result=MarketFlowResult(
                    item=normalized,
                    decision=decision,
                    interpretation=InterpretationResult(core_content=raw_item["summary"]),
                ),
                inserted=True,
                storage_ref={"store_kind": "article_reviews", "item_id": raw_item["id"]},
                delivery_status="sent",
            )

        value_directory_monitor.enrich_item_with_preview = fake_enrich
        value_directory_monitor.process_market_item = fake_process
        assert value_directory_monitor.review_and_maybe_push(item, source=source) is True
    finally:
        value_directory_monitor.connect_db = original_connect
        value_directory_monitor.article_review_exists = original_existing
        value_directory_monitor.enrich_item_with_preview = original_enrich
        value_directory_monitor.process_market_item = original_process
        value_directory_monitor.set_seen_item_lifecycle_if_present = original_lifecycle
        value_directory_monitor.production_admission_context = original_admission

    assert len(calls) == 1
    call = calls[0]
    normalized = call["normalized"]
    assert normalized.source == source.source_id
    assert normalized.content_type == "research_index"
    assert normalized.raw["value_directory_policy"]["preview_enabled"] is True
    assert call["store_kind"] == "article"
    assert call["deliver"] is True
    assert call["use_rule_dedup"] is True
    assert call["reprocess_existing"] is False
    assert call["production_admission"].status == "admitted"
    assert call["production_admission"].matched_families == ("semiconductor_ai",)


def test_value_directory_monitor_does_not_own_store_dedup_or_delivery() -> None:
    source = inspect.getsource(value_directory_monitor)
    for forbidden in (
        "send_card(",
        "reserve_rule_alert(",
        "save_article_review(",
        "mark_article_pushed(",
    ):
        assert forbidden not in source
    assert "process_market_item(" in source


def test_run_finishes_browser_collection_before_source_processing() -> None:
    events: list[str] = []
    original_collect = value_directory_monitor.collect_sources_with_previews
    original_enabled = value_directory_monitor.source_profile_enabled
    original_process = value_directory_monitor.process_collected_source
    try:
        value_directory_monitor.source_profile_enabled = lambda _source_id: True

        def fake_collect(source_ids, **_kwargs):
            events.extend(["browser_start", "browser_close"])
            return types.SimpleNamespace(
                entries_by_source={source_id: [] for source_id in source_ids},
                source_errors={},
                previews={},
                preview_errors={},
            )

        def fake_process(source, _entries, **kwargs):
            events.append(f"process:{source.source_id}")
            return {
                "ok": True,
                "mode": "shadow_dry_run",
                "sent_feishu": False,
                "source": source.source_id,
                "counts": {"raw_items": 0},
                "errors": [],
            }

        value_directory_monitor.collect_sources_with_previews = fake_collect
        value_directory_monitor.process_collected_source = fake_process
        payload = value_directory_monitor.run(
            production=False,
            limit=10,
            notify_baseline=False,
            source_ids=["value_directory_ib_stocks", "value_directory_ib_industry_macro"],
        )
    finally:
        value_directory_monitor.collect_sources_with_previews = original_collect
        value_directory_monitor.source_profile_enabled = original_enabled
        value_directory_monitor.process_collected_source = original_process

    assert payload["ok"] is True
    assert events == [
        "browser_start",
        "browser_close",
        "process:value_directory_ib_stocks",
        "process:value_directory_ib_industry_macro",
    ]


def test_collected_preview_does_not_launch_another_browser() -> None:
    item = {"id": "preview-1", "url": "https://www.valuelist.cn/preview-1.html", "title": "Test report"}
    preview = {"state": "ok", "previewImages": [{"src": "https://img.valuelist.cn/preview-1.jpg"}]}
    original_collect = value_directory_monitor.collect_preview
    original_extract = value_directory_monitor.extract_preview_facts
    try:
        value_directory_monitor.collect_preview = lambda _url: (_ for _ in ()).throw(
            AssertionError("processing phase must not launch a browser")
        )
        value_directory_monitor.extract_preview_facts = lambda _item, _preview: {
            "status": "ok",
            "core_content": "preview content",
            "model": "test",
            "preview_image_url": "https://img.valuelist.cn/preview-1.jpg",
        }
        enriched = value_directory_monitor.enrich_item_with_preview(item, preview)
    finally:
        value_directory_monitor.collect_preview = original_collect
        value_directory_monitor.extract_preview_facts = original_extract

    assert enriched["summary"] == "preview content"


def test_collect_production_rechecks_current_unpushed_reviews() -> None:
    source = source_config("value_directory_ib_stocks")
    entries = [
        {
            "id": "862591",
            "url": "https://www.valuelist.cn/862591.html",
            "title": "野村-中际旭创(300308.SZ)：我们预计2027年后将实现长期增长",
            "summary": "国际投行个股研报索引。",
            "published_at": "2026-07-11T00:00:10+00:00",
        },
        {
            "id": "862592",
            "url": "https://www.valuelist.cn/862592.html",
            "title": "高盛-其他公司：例行观点",
            "summary": "国际投行个股研报索引。",
            "published_at": "2026-07-11T00:00:10+00:00",
        },
    ]
    calls: list[tuple[str, bool]] = []
    original_save_new = value_directory_monitor.save_new_items_with_retry
    original_connect = value_directory_monitor.connect_db
    original_exists = value_directory_monitor.article_review_exists
    original_review = value_directory_monitor.review_and_maybe_push
    original_enabled = os.environ.get("VALUE_DIRECTORY_RECHECK_UNPUSHED")
    original_limit = os.environ.get("VALUE_DIRECTORY_RECHECK_UNPUSHED_LIMIT")
    try:
        os.environ["VALUE_DIRECTORY_RECHECK_UNPUSHED"] = "1"
        os.environ["VALUE_DIRECTORY_RECHECK_UNPUSHED_LIMIT"] = "30"
        value_directory_monitor.save_new_items_with_retry = lambda *_args, **_kwargs: []
        value_directory_monitor.connect_db = lambda: _DummyContext()

        def fake_exists(_conn, _source_id, item_id):
            if item_id == "862591":
                return {"push_now": False, "pushed_at": "", "importance": "medium"}
            return None

        def fake_review(item, *, source=None, recheck_rules=False, **_kwargs):
            calls.append((item["id"], recheck_rules))
            return True

        value_directory_monitor.article_review_exists = fake_exists
        value_directory_monitor.review_and_maybe_push = fake_review
        payload = value_directory_monitor.collect_production(
            entries,
            source=source,
            notify_baseline=False,
            started_at="2026-07-11T00:00:00+00:00",
        )
    finally:
        value_directory_monitor.save_new_items_with_retry = original_save_new
        value_directory_monitor.connect_db = original_connect
        value_directory_monitor.article_review_exists = original_exists
        value_directory_monitor.review_and_maybe_push = original_review
        if original_enabled is None:
            os.environ.pop("VALUE_DIRECTORY_RECHECK_UNPUSHED", None)
        else:
            os.environ["VALUE_DIRECTORY_RECHECK_UNPUSHED"] = original_enabled
        if original_limit is None:
            os.environ.pop("VALUE_DIRECTORY_RECHECK_UNPUSHED_LIMIT", None)
        else:
            os.environ["VALUE_DIRECTORY_RECHECK_UNPUSHED_LIMIT"] = original_limit

    assert calls == [("862591", True)]
    assert payload["counts"]["new_items"] == 0
    assert payload["counts"]["reviewed_items"] == 1
    assert payload["counts"]["rechecked_items"] == 1
    assert payload["counts"]["pushed_items"] == 1


def main() -> int:
    test_normalize_entry_extracts_stable_id_and_utc_date()
    test_normalize_entry_supports_industry_macro_source()
    test_page_state_detection_separates_waf_login_and_empty()
    test_empty_list_waits_once_for_delayed_articles()
    test_persistent_empty_list_remains_empty_after_bounded_wait()
    test_waf_and_login_states_do_not_wait_for_articles()
    test_profile_lock_state_distinguishes_live_and_dead_same_host_owner()
    test_profile_lock_state_finds_registered_socket_holder()
    test_wait_for_profile_release_observes_owner_exit()
    test_close_browser_context_reports_live_owner_timeout()
    test_launch_browser_context_retains_bounded_underlying_error_and_lock_state()
    test_multi_source_collection_uses_one_browser_and_continues_after_detail_failure()
    test_multi_source_collection_keeps_list_failure_attributable()
    test_dedupe_entries_keeps_first_valid_url()
    test_shadow_payload_marks_seen_and_reviewed_without_delivery()
    test_source_profile_registers_value_directory()
    test_preview_failure_is_recorded_without_fake_summary()
    test_paddleocr_result_flatten_supports_common_shapes()
    test_paddleocr_instance_retries_unknown_argument_errors()
    test_preview_llm_policy_disables_deepseek_thinking()
    test_preview_ocr_text_path_uses_text_llm_without_vision()
    test_preview_ocr_failure_falls_back_without_blocking()
    test_recheck_uses_enriched_item_without_a_preliminary_decision_gate()
    test_new_item_uses_unified_market_runtime_after_preview_enrichment()
    test_value_directory_monitor_does_not_own_store_dedup_or_delivery()
    test_run_finishes_browser_collection_before_source_processing()
    test_collected_preview_does_not_launch_another_browser()
    test_collect_production_rechecks_current_unpushed_reviews()
    print("value directory monitor checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
