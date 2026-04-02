"""Tests for the hierarchical stopword pipeline."""

from __future__ import annotations

import unittest

from keyword_pipeline import extract_candidate_terms, filter_tokens
from stopwords import MemberContext, StopwordRegistry, build_dynamic_stopwords


class StopwordPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = StopwordRegistry()
        self.context = MemberContext(
            member_name="박상혁",
            party_name="더불어민주당",
            district_name="경기 김포시갑",
            aliases=["박상혁 의원"],
            related_regions=["김포", "김포시", "김포시갑", "경기"],
        )

    def test_dynamic_stopwords_include_name_party_region(self) -> None:
        dynamic = build_dynamic_stopwords(self.context)
        self.assertIn("박상혁", dynamic["member_names"])
        self.assertIn("박상혁 의원", dynamic["member_names"])
        self.assertIn("더불어민주당", dynamic["party_names"])
        self.assertIn("민주당", dynamic["party_names"])
        self.assertIn("김포시갑", dynamic["region_names"])

    def test_name_party_region_are_removed(self) -> None:
        tokens = ["박상혁", "민주당", "김포", "반도체", "교통망"]
        result = filter_tokens(tokens, registry=self.registry, member_context=self.context)
        self.assertIn("반도체", result.kept_tokens)
        self.assertIn("교통망", result.kept_tokens)
        self.assertIn("박상혁", result.removed_tokens)
        self.assertIn("민주당", result.removed_tokens)
        self.assertIn("김포", result.removed_tokens)

    def test_policy_keywords_remain(self) -> None:
        tokens = ["반도체", "특별법", "전세사기", "지원법"]
        result = filter_tokens(tokens, registry=self.registry, member_context=self.context)
        self.assertEqual(result.kept_tokens, tokens)

    def test_small_document_still_generates_ngrams(self) -> None:
        candidates = extract_candidate_terms(["반도체", "특별법"])
        self.assertEqual(candidates.unigrams, ["반도체", "특별법"])
        self.assertEqual(candidates.bigrams, ["반도체 특별법"])
        self.assertEqual(candidates.trigrams, [])

    def test_bigram_trigram_generation(self) -> None:
        candidates = extract_candidate_terms(["전세사기", "피해", "지원법"])
        self.assertIn("전세사기 피해", candidates.bigrams)
        self.assertIn("피해 지원법", candidates.bigrams)
        self.assertIn("전세사기 피해 지원법", candidates.trigrams)


if __name__ == "__main__":
    unittest.main()
