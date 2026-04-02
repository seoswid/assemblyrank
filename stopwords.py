"""Stopword management for Korean political news keyword extraction.

This module provides:
1. A category-based stopword registry.
2. Member-specific context and dynamic stopword generation.
3. Regex-based cleanup rules that can explain why a token or span was removed.

The structures are intentionally designed so the registry can later be managed
through JSON or YAML files by operators.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from typing import Iterable, Mapping


STOPWORD_CATEGORIES: tuple[str, ...] = (
    "member_names",
    "party_names",
    "region_names",
    "assembly_terms",
    "political_common",
    "media_common",
    "low_value_terms",
    "person_titles",
)


DEFAULT_STOPWORDS: dict[str, set[str]] = {
    "member_names": set(),
    "party_names": {
        "더불어민주당",
        "민주당",
        "국민의힘",
        "국힘",
        "조국혁신당",
        "개혁신당",
        "진보당",
        "기본소득당",
        "무소속",
        "열린민주당",
        "미래통합당",
        "새누리당",
        "자유한국당",
    },
    "region_names": {
        "서울",
        "부산",
        "대구",
        "인천",
        "광주",
        "대전",
        "울산",
        "세종",
        "경기",
        "강원",
        "충북",
        "충남",
        "전북",
        "전남",
        "경북",
        "경남",
        "제주",
        "특별시",
        "광역시",
        "특별자치시",
        "특별자치도",
    },
    "assembly_terms": {
        "국회",
        "국회의원",
        "의원",
        "본회의",
        "상임위",
        "상임위원회",
        "국정감사",
        "정무위",
        "행안위",
        "산자위",
        "법사위",
        "예결위",
        "의정활동",
    },
    "political_common": {
        "논평",
        "발언",
        "촉구",
        "주장",
        "관련",
        "입장",
        "발표",
        "회의",
        "참석",
        "브리핑",
        "정치",
        "정치권",
        "정부",
        "여당",
        "야당",
        "여야",
        "정국",
        "추진",
        "검토",
        "논의",
        "질문",
        "답변",
    },
    "media_common": {
        "단독",
        "속보",
        "인터뷰",
        "사진",
        "영상",
        "기자",
        "연합뉴스",
        "뉴시스",
        "뉴스1",
        "헤럴드경제",
        "머니투데이",
        "오마이뉴스",
        "경향신문",
        "조선일보",
        "한겨레",
        "중앙일보",
        "동아일보",
    },
    "low_value_terms": {
        "이번",
        "최근",
        "오늘",
        "당시",
        "통해",
        "대한",
        "관련",
        "가운데",
        "경우",
        "이날",
        "지난",
        "내일",
        "어제",
        "앞서",
        "이후",
        "설명",
        "강조",
        "지적",
        "논란",
        "현장",
    },
    "person_titles": {
        "대표",
        "원내대표",
        "위원장",
        "장관",
        "대통령",
        "지사",
        "시장",
        "총리",
        "수석",
        "비서관",
        "실장",
        "차관",
        "후보",
        "의장",
    },
}


@dataclass(slots=True)
class RegexRemovalRule:
    """Regex-based cleanup rule."""

    name: str
    pattern: str
    reason: str
    flags: int = 0

    def compiled(self) -> re.Pattern[str]:
        """Return the compiled pattern."""
        return re.compile(self.pattern, self.flags)


DEFAULT_REGEX_RULES: tuple[RegexRemovalRule, ...] = (
    RegexRemovalRule(
        name="bracket_tag",
        pattern=r"\[(?:단독|속보|현장|영상|포토|사진|인터뷰)\]",
        reason="media_bracket_tag",
    ),
    RegexRemovalRule(
        name="paren_press_region",
        pattern=r"\((?:연합뉴스|뉴시스|뉴스1|서울=|세종=|수원=|대전=|광주=|대구=|부산=)[^)]+\)",
        reason="parenthetical_press_or_region",
    ),
    RegexRemovalRule(
        name="number_symbol_only",
        pattern=r"^[\d\W_]+$",
        reason="number_or_symbol_only",
    ),
    RegexRemovalRule(
        name="single_char_hangul",
        pattern=r"^[가-힣A-Za-z]$",
        reason="single_character",
    ),
    RegexRemovalRule(
        name="article_suffix",
        pattern=r"(기자|특파원|앵커|논설위원)$",
        reason="media_suffix",
    ),
)


@dataclass(slots=True)
class MemberContext:
    """Member-specific context used to derive dynamic stopwords."""

    member_name: str
    party_name: str
    district_name: str
    aliases: list[str] = field(default_factory=list)
    related_regions: list[str] = field(default_factory=list)
    related_people: list[str] = field(default_factory=list)


class StopwordRegistry:
    """Category-based stopword registry.

    The registry is intentionally mutable so operators can keep extending it.
    """

    def __init__(self, initial: Mapping[str, Iterable[str]] | None = None) -> None:
        base = {category: set(DEFAULT_STOPWORDS.get(category, set())) for category in STOPWORD_CATEGORIES}
        if initial:
            for category, words in initial.items():
                base.setdefault(category, set()).update(_normalize_words(words))
        self._words: dict[str, set[str]] = base

    def add_words(self, category: str, words: Iterable[str]) -> None:
        """Add words to a category."""
        self._words.setdefault(category, set()).update(_normalize_words(words))

    def remove_words(self, category: str, words: Iterable[str]) -> None:
        """Remove words from a category if present."""
        current = self._words.setdefault(category, set())
        for word in _normalize_words(words):
            current.discard(word)

    def get_all_words(self) -> set[str]:
        """Return the union of all stopwords."""
        merged: set[str] = set()
        for words in self._words.values():
            merged.update(words)
        return merged

    def get_words_by_category(self, category: str) -> set[str]:
        """Return a copy of the stopword set for one category."""
        return set(self._words.get(category, set()))

    def to_dict(self) -> dict[str, list[str]]:
        """Serialize registry to a JSON/YAML friendly structure."""
        return {category: sorted(words) for category, words in self._words.items()}

    @classmethod
    def from_dict(cls, data: Mapping[str, Iterable[str]]) -> "StopwordRegistry":
        """Create a registry from a plain mapping."""
        return cls(initial=data)

    def export(self, path: str | Path) -> None:
        """Export the registry to JSON or YAML depending on extension."""
        target = Path(path)
        payload = self.to_dict()
        if target.suffix.lower() == ".json":
            target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            return
        if target.suffix.lower() in {".yaml", ".yml"}:
            try:
                import yaml  # type: ignore
            except ImportError as error:  # pragma: no cover - optional dependency
                raise RuntimeError("PyYAML is required to export YAML.") from error
            target.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=True), encoding="utf-8")
            return
        raise ValueError(f"Unsupported export format: {target.suffix}")

    @classmethod
    def load(cls, path: str | Path) -> "StopwordRegistry":
        """Load registry content from JSON or YAML."""
        source = Path(path)
        if source.suffix.lower() == ".json":
            data = json.loads(source.read_text(encoding="utf-8"))
            return cls.from_dict(data)
        if source.suffix.lower() in {".yaml", ".yml"}:
            try:
                import yaml  # type: ignore
            except ImportError as error:  # pragma: no cover - optional dependency
                raise RuntimeError("PyYAML is required to load YAML.") from error
            data = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
            return cls.from_dict(data)
        raise ValueError(f"Unsupported import format: {source.suffix}")


def build_dynamic_stopwords(member_context: MemberContext) -> dict[str, set[str]]:
    """Create member-specific stopwords grouped by category.

    Dynamic stopwords are strong filters intended to remove:
    - the member's own name and aliases
    - party names and variants
    - district names and region variants
    - common title-combined forms such as '박상혁 의원'
    """

    member_variants = _member_name_variants(member_context.member_name, member_context.aliases)
    party_variants = _party_variants(member_context.party_name)
    region_variants = _region_variants(member_context.district_name, member_context.related_regions)
    related_people = set(_normalize_words(member_context.related_people))

    title_forms: set[str] = set()
    for name in member_variants | related_people:
        for title in DEFAULT_STOPWORDS["person_titles"] | {"의원", "국회의원"}:
            title_forms.add(f"{name} {title}")
            title_forms.add(f"{name}{title}")

    return {
        "member_names": member_variants | related_people | title_forms,
        "party_names": party_variants,
        "region_names": region_variants,
        "assembly_terms": set(),
        "political_common": set(),
        "media_common": set(),
        "low_value_terms": set(),
        "person_titles": set(DEFAULT_STOPWORDS["person_titles"]),
    }


def compiled_regex_rules() -> list[tuple[RegexRemovalRule, re.Pattern[str]]]:
    """Return compiled regex rules for reuse."""
    return [(rule, rule.compiled()) for rule in DEFAULT_REGEX_RULES]


def match_regex_reason(token: str, rules: Iterable[tuple[RegexRemovalRule, re.Pattern[str]]] | None = None) -> str | None:
    """Return the removal reason if a token matches a regex rule."""
    active_rules = list(rules or compiled_regex_rules())
    for rule, pattern in active_rules:
        if pattern.search(token):
            return rule.reason
    return None


def _normalize_words(words: Iterable[str]) -> set[str]:
    normalized = set()
    for word in words:
        clean = str(word).strip()
        if clean:
            normalized.add(clean)
    return normalized


def _member_name_variants(member_name: str, aliases: Iterable[str]) -> set[str]:
    variants = {member_name.strip(), member_name.replace(" ", "")}
    for alias in aliases:
        clean = alias.strip()
        if clean:
            variants.add(clean)
            variants.add(clean.replace(" ", ""))
    return {item for item in variants if item}


def _party_variants(party_name: str) -> set[str]:
    variants = {party_name.strip()}
    replacements = {
        "더불어민주당": {"민주당"},
        "국민의힘": {"국힘"},
        "조국혁신당": {"조국당"},
        "기본소득당": {"기본소득"},
    }
    variants.update(replacements.get(party_name.strip(), set()))
    for token in re.findall(r"[가-힣A-Za-z]{2,}", party_name):
        variants.add(token)
    return {item for item in variants if item}


def _region_variants(district_name: str, related_regions: Iterable[str]) -> set[str]:
    variants = {district_name.strip()}
    values = [district_name, *related_regions]
    suffix_pattern = re.compile(r"(특별시|광역시|특별자치시|특별자치도|시|군|구|동|읍|면|갑|을|병|정)$")
    for value in values:
        for token in re.findall(r"[가-힣A-Za-z]{2,}", value):
            variants.add(token)
            reduced = suffix_pattern.sub("", token)
            if len(reduced) >= 2:
                variants.add(reduced)
    return {item for item in variants if item}
