"""Audited international-bank names shared by deterministic content rules."""

from __future__ import annotations

import re


INTERNATIONAL_BANK_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("高盛", ("高盛", "goldman sachs")),
    ("摩根士丹利", ("摩根士丹利", "morgan stanley")),
    ("摩根大通", ("摩根大通", "jpmorgan", "jp morgan", "j.p. morgan")),
    ("花旗", ("花旗", "citi", "citigroup")),
    ("瑞银", ("瑞银", "ubs")),
    ("美银", ("美银", "美国银行", "bank of america", "bofa")),
    ("巴克莱", ("巴克莱", "barclays")),
    ("德意志银行", ("德意志银行", "德银", "deutsche bank")),
    ("汇丰", ("汇丰", "hsbc")),
    ("法国巴黎银行", ("法国巴黎银行", "法巴", "bnp paribas")),
    ("法国兴业银行", ("法国兴业银行", "法兴", "societe generale", "société générale")),
    ("富国银行", ("富国银行", "wells fargo")),
    ("伯恩斯坦", ("伯恩斯坦", "bernstein")),
    ("杰富瑞", ("杰富瑞", "jefferies")),
    ("野村", ("野村", "nomura")),
    ("麦格理", ("麦格理", "macquarie")),
)

FED_PATH_BANKS = frozenset(
    {
        "高盛",
        "摩根士丹利",
        "摩根大通",
        "花旗",
        "瑞银",
        "美银",
        "巴克莱",
        "德意志银行",
        "汇丰",
        "法国巴黎银行",
        "法国兴业银行",
        "富国银行",
    }
)


def bank_alias_matches(lowered_text: str, alias: str) -> bool:
    normalized = alias.lower().strip()
    if not normalized:
        return False
    if re.search(r"[a-z0-9]", normalized):
        pattern = re.escape(normalized).replace(r"\ ", r"\s+")
        return re.search(rf"(?<![a-z0-9]){pattern}(?![a-z0-9])", lowered_text) is not None
    return normalized in lowered_text


def matched_bank_names(text: str, allowed_banks: set[str] | None = None) -> list[str]:
    lowered = text.lower()
    banks: list[str] = []
    for display, aliases in INTERNATIONAL_BANK_ALIASES:
        if allowed_banks and display.casefold() not in allowed_banks and not any(
            alias.casefold() in allowed_banks for alias in aliases
        ):
            continue
        if any(bank_alias_matches(lowered, alias) for alias in aliases):
            banks.append(display)
    return banks


def bank_mention_position(text: str, display_name: str) -> int | None:
    lowered = text.casefold()
    aliases = next((aliases for display, aliases in INTERNATIONAL_BANK_ALIASES if display == display_name), ())
    positions = [lowered.find(alias.casefold()) for alias in aliases if lowered.find(alias.casefold()) >= 0]
    return min(positions) if positions else None


def banks_in_mention_order(text: str, allowed_banks: set[str] | None = None) -> list[str]:
    banks = matched_bank_names(text, allowed_banks=allowed_banks)
    return sorted(banks, key=lambda bank: bank_mention_position(text, bank) if bank_mention_position(text, bank) is not None else len(text))
