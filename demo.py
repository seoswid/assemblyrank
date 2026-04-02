"""Demo script for the Korean political news keyword pipeline."""

from __future__ import annotations

from pprint import pprint

from keyword_pipeline import analyze_documents
from stopwords import MemberContext, StopwordRegistry


EXAMPLE_DOCUMENTS = [
    "박상혁 의원이 김포 교통망 확충과 GTX-D 노선 논의를 촉구했다.",
    "[단독] 박상혁 국회의원, 김포 골드라인 혼잡 완화 대책 발표",
    "더불어민주당 박상혁 의원은 반도체 특별법 처리 필요성을 강조했다.",
    "김포시갑 박상혁 의원, 전세사기 피해 지원법 개정안 발의",
    "박상혁 의원 인터뷰… 첨단산업 육성과 교통 인프라를 통해 지역 성장 추진",
    "연합뉴스: 박상혁 의원, 청년 주거안정 정책 토론회 참석",
    "박상혁 의원이 교통대책 회의에서 GTX-D와 서울5호선 연장 문제를 언급했다.",
    "김포 한강2콤팩트시티 개발과 광역교통 개선이 핵심 이슈로 떠올랐다.",
    "전세사기 피해자 지원 확대와 공공임대 공급 강화 법안이 주목받고 있다.",
    "반도체 생태계 육성, 첨단전략산업 지원, 광역교통망 확충이 정책 이슈로 부상했다.",
]


def build_demo_context() -> MemberContext:
    """Return a realistic member context example."""
    return MemberContext(
        member_name="박상혁",
        party_name="더불어민주당",
        district_name="경기 김포시갑",
        aliases=["박상혁 의원"],
        related_regions=["김포", "김포시", "김포시갑", "경기"],
        related_people=["박용진", "박주민"],
    )


def main() -> None:
    """Run a human-readable demonstration."""
    registry = StopwordRegistry()
    context = build_demo_context()
    result = analyze_documents(EXAMPLE_DOCUMENTS, registry=registry, member_context=context)

    print("=== 제거 전 토큰 ===")
    print(result.original_tokens[:80])
    print()

    print("=== 제거 후 토큰 ===")
    print(result.filtered.kept_tokens[:80])
    print()

    print("=== 대표 후보 키워드 ===")
    pprint(result.top_terms[:15])
    print()

    print("=== 제거 사유 로그 ===")
    for token, reasons in sorted(result.filtered.removed_reasons.items()):
        print(f"{token}: {', '.join(reasons)}")


if __name__ == "__main__":
    main()
