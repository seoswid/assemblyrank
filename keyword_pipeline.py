"""Keyword extraction pipeline for Korean political news.

This module combines:
- category-based stopwords
- dynamic stopwords per member
- regex-based cleanup
- kiwipiepy tokenization
- n-gram candidate extraction
- scikit-learn integration helpers
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

from kiwipiepy import Kiwi
from sklearn.feature_extraction.text import CountVectorizer

from stopwords import (
    MemberContext,
    StopwordRegistry,
    build_dynamic_stopwords,
    compiled_regex_rules,
    match_regex_reason,
)


DEFAULT_ALLOWED_POS: tuple[str, ...] = ("NNG", "NNP", "SL")


@dataclass(slots=True)
class TokenInfo:
    """One token produced by the tokenizer."""

    text: str
    pos: str


@dataclass(slots=True)
class FilterResult:
    """Token filtering result with debugging information."""

    kept_tokens: list[str]
    removed_tokens: list[str]
    removed_reasons: dict[str, list[str]]


@dataclass(slots=True)
class CandidateTerms:
    """Container for unigram, bigram, and trigram candidates."""

    unigrams: list[str]
    bigrams: list[str]
    trigrams: list[str]


@dataclass(slots=True)
class KeywordExtractionResult:
    """Full debug-friendly result for one document collection."""

    original_tokens: list[str]
    filtered: FilterResult
    candidates: CandidateTerms
    top_terms: list[tuple[str, int]]


def create_kiwi() -> Kiwi:
    """Create a Kiwi tokenizer instance."""
    return Kiwi()


def tokenize_text(
    text: str,
    kiwi: Kiwi | None = None,
    allowed_pos: Sequence[str] = DEFAULT_ALLOWED_POS,
) -> list[TokenInfo]:
    """Tokenize Korean text and keep only selected POS tags by default."""
    tokenizer = kiwi or create_kiwi()
    allowed = set(allowed_pos)
    tokens: list[TokenInfo] = []
    for token in tokenizer.tokenize(text):
        if token.tag in allowed:
            tokens.append(TokenInfo(text=token.form, pos=token.tag))
    return tokens


def build_effective_stopword_map(
    registry: StopwordRegistry,
    member_context: MemberContext,
) -> dict[str, str]:
    """Build a token -> category map from fixed and dynamic stopwords."""
    token_reason_map: dict[str, str] = {}

    for category in registry.to_dict():
        for word in registry.get_words_by_category(category):
            token_reason_map[word] = category

    dynamic = build_dynamic_stopwords(member_context)
    for category, words in dynamic.items():
        for word in words:
            token_reason_map[word] = f"dynamic:{category}"

    return token_reason_map


def filter_tokens(
    tokens: Sequence[TokenInfo | str],
    registry: StopwordRegistry,
    member_context: MemberContext,
) -> FilterResult:
    """Filter tokens with fixed stopwords, dynamic stopwords, and regex rules.

    Returns debug-friendly removal reasons so operators can inspect why each
    token disappeared.
    """

    stopword_reason_map = build_effective_stopword_map(registry, member_context)
    regex_rules = compiled_regex_rules()

    kept_tokens: list[str] = []
    removed_tokens: list[str] = []
    removed_reasons: dict[str, list[str]] = {}

    for item in tokens:
        token = item.text if isinstance(item, TokenInfo) else str(item)
        clean = token.strip()
        if not clean:
            continue

        reasons: list[str] = []

        if clean in stopword_reason_map:
            reasons.append(stopword_reason_map[clean])
        compact = clean.replace(" ", "")
        if compact in stopword_reason_map and stopword_reason_map[compact] not in reasons:
            reasons.append(stopword_reason_map[compact])

        regex_reason = match_regex_reason(clean, regex_rules)
        if regex_reason:
            reasons.append(regex_reason)

        if reasons:
            removed_tokens.append(clean)
            removed_reasons[clean] = reasons
            continue

        kept_tokens.append(clean)

    return FilterResult(
        kept_tokens=kept_tokens,
        removed_tokens=removed_tokens,
        removed_reasons=removed_reasons,
    )


def extract_candidate_terms(tokens: Sequence[str]) -> CandidateTerms:
    """Build unigram, bigram, and trigram candidates from filtered tokens."""
    unigrams = list(tokens)
    bigrams = [" ".join(tokens[index:index + 2]) for index in range(max(0, len(tokens) - 1))]
    trigrams = [" ".join(tokens[index:index + 3]) for index in range(max(0, len(tokens) - 2))]
    return CandidateTerms(unigrams=unigrams, bigrams=bigrams, trigrams=trigrams)


def analyze_documents(
    documents: Sequence[str],
    registry: StopwordRegistry,
    member_context: MemberContext,
    kiwi: Kiwi | None = None,
    allowed_pos: Sequence[str] = DEFAULT_ALLOWED_POS,
) -> KeywordExtractionResult:
    """Run the full extraction flow for a list of documents."""
    tokenizer = kiwi or create_kiwi()
    original_tokens: list[str] = []
    filtered_tokens: list[str] = []
    removed_tokens: list[str] = []
    removed_reasons: dict[str, list[str]] = {}

    for document in documents:
        token_infos = tokenize_text(document, kiwi=tokenizer, allowed_pos=allowed_pos)
        original_tokens.extend(token.text for token in token_infos)
        filtered = filter_tokens(token_infos, registry=registry, member_context=member_context)
        filtered_tokens.extend(filtered.kept_tokens)
        removed_tokens.extend(filtered.removed_tokens)
        for token, reasons in filtered.removed_reasons.items():
            removed_reasons.setdefault(token, [])
            for reason in reasons:
                if reason not in removed_reasons[token]:
                    removed_reasons[token].append(reason)

    candidates = extract_candidate_terms(filtered_tokens)
    counts = Counter(candidates.unigrams + candidates.bigrams + candidates.trigrams)
    top_terms = counts.most_common(20)
    return KeywordExtractionResult(
        original_tokens=original_tokens,
        filtered=FilterResult(
            kept_tokens=filtered_tokens,
            removed_tokens=removed_tokens,
            removed_reasons=removed_reasons,
        ),
        candidates=candidates,
        top_terms=top_terms,
    )


def make_sklearn_analyzer(
    registry: StopwordRegistry,
    member_context: MemberContext,
    kiwi: Kiwi | None = None,
    allowed_pos: Sequence[str] = DEFAULT_ALLOWED_POS,
) -> Callable[[str], list[str]]:
    """Create a sklearn-compatible analyzer returning 1-3 gram candidates."""
    tokenizer = kiwi or create_kiwi()

    def analyzer(document: str) -> list[str]:
        token_infos = tokenize_text(document, kiwi=tokenizer, allowed_pos=allowed_pos)
        filtered = filter_tokens(token_infos, registry=registry, member_context=member_context)
        candidates = extract_candidate_terms(filtered.kept_tokens)
        return candidates.unigrams + candidates.bigrams + candidates.trigrams

    return analyzer


def build_vectorizer(
    registry: StopwordRegistry,
    member_context: MemberContext,
    kiwi: Kiwi | None = None,
    max_df: float | int = 0.95,
    min_df: float | int = 1,
) -> CountVectorizer:
    """Build a sklearn vectorizer using the custom analyzer.

    Korean text uses a user-defined analyzer here, so built-in English
    stopwords are intentionally disabled.
    """

    analyzer = make_sklearn_analyzer(
        registry=registry,
        member_context=member_context,
        kiwi=kiwi,
    )
    return CountVectorizer(
        analyzer=analyzer,
        ngram_range=(1, 3),
        max_df=max_df,
        min_df=min_df,
        stop_words=None,
    )
