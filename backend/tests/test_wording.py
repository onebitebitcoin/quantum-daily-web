"""양자 카드뉴스 문구 게이트 테스트.

btc-daily-web 의 발효일 예외(EFFECTIVE_DATE/TITLE_RULE_DATE)가 여기서는 걷어졌다
(app/wording.py 참고) — 이 프로젝트는 첫 발행부터 계약이 있어 소급 적용을 걱정할
과거 발행분이 없다. 그래서 "발효일 이전은 통과" 테스트 대신 "예외가 없다"는 사실
자체를 지키는 테스트 하나가 그 자리를 대신한다.

BTC 표기 검사(BTC_TOKEN)도 이 도메인에는 없다 — 양자 카드뉴스에 "BTC"가 노출될 일이
없으므로 wording.py에서 아예 삭제됐다. 그래서 여기엔 BTC 관련 테스트가 없다.
"""

import datetime
import json
from pathlib import Path
from typing import Any

from app.schemas import EditionContent
from app.wording import BANNED_TERMS, EFFECTIVE_DATE, TITLE_RULE_DATE, find_problems

REFERENCE_CONTENT = Path(__file__).resolve().parents[2] / "reference" / "content.json"

# 실제 발행일처럼 쓸 임의의 날짜 하나. "예외가 없다"는 별도 테스트에서 date.min을
# 직접 검증하므로, 나머지 테스트는 평범한 날짜 하나로 충분하다.
SOME_DATE = "2026-08-26"


def build(date: str = SOME_DATE, **card_overrides: Any) -> EditionContent:
    """레퍼런스 콘텐츠에 카드 1장만 남기고 필드를 AI 소재로 갈아끼운 에디션.

    reference/content.json 은 비트코인 카드뉴스 시절 디자인 레퍼런스라(CLAUDE.md
    명시 예외로 그대로 둔다) 원문 자체는 비트코인 소재지만, 여기서는 스키마만
    재사용하고 title/body/quote/chips 는 AI 소재로 덮어쓴다.
    """
    payload = json.loads(REFERENCE_CONTENT.read_text(encoding="utf-8"))
    payload["meta"]["date"] = date
    card = payload["cards"][0]
    card.update(
        {
            "title": "벤치마크를 앞선 오픈AI 추론 모델",
            "body": "오픈AI가 새 추론 모델을 공개했습니다.",
            "quote": "아직 성능 검증이 끝난 게 아닙니다.",
            "chips": ["#오픈AI", "#추론모델", "#벤치마크"],
            "qa": None,
        }
    )
    card.update(card_overrides)
    payload["cards"] = [card]
    payload.pop("trending", None)
    return EditionContent.model_validate(payload)


def test_clean_edition_passes() -> None:
    assert find_problems(build()) == []


def test_banned_transliteration_is_caught() -> None:
    problems = find_problems(build(body="양자 컴퓨터가 새 기록을 세웠다고 밝혔습니다."))

    assert len(problems) == 1
    assert "양자 컴퓨터" in problems[0] and "양자컴퓨터" in problems[0]
    assert "카드 1 body" in problems[0]


def test_every_banned_term_is_caught() -> None:
    """BANNED_TERMS 표의 항목 전부가 실제로 걸리는지 확인한다.

    표만 늘려두고 실제로는 안 걸리는 항목이 남는 회귀(오탈자·매칭 로직 변경)를
    잡기 위한 테이블 기반 테스트다.
    """
    for banned, correct in BANNED_TERMS.items():
        problems = find_problems(build(body=f"{banned}이 관련 소식을 냈다고 밝혔습니다."))
        assert len(problems) >= 1, banned
        assert any(banned in p and correct in p for p in problems), (banned, problems)


def test_plain_style_body_is_caught() -> None:
    problems = find_problems(build(body="오픈AI가 새 추론 모델을 공개했다."))

    assert len(problems) == 1
    assert "했습니다체가 아니다" in problems[0]


def test_plain_style_quote_is_caught() -> None:
    problems = find_problems(build(quote="아직 성능 검증이 끝난 게 아니다."))

    assert [p for p in problems if "quote" in p and "했습니다체" in p]


def test_composed_polite_endings_are_accepted() -> None:
    # 아닙니다/탑니다/기댑니다는 "습니다"로 끝나지 않는다 — 종성 ㅂ으로 봐야 통과한다.
    for ending in ("같은 배관을 탑니다.", "안전성에만 기댑니다.", "아직 검증되지 않습니다."):
        assert find_problems(build(body=ending)) == [], ending


def test_plain_style_anida_is_not_mistaken_for_polite() -> None:
    # "아니다"도 '니다'로 끝난다. 종성을 안 보면 평서체가 그대로 통과한다.
    problems = find_problems(build(body="지금은 검증이 끝난 게 아니다."))

    assert len(problems) == 1
    assert "했습니다체가 아니다" in problems[0]


def test_trailing_quote_mark_does_not_break_the_ending_check() -> None:
    assert find_problems(build(quote='"아직 성능 검증이 끝난 게 아닙니다."')) == []


def test_quote_may_be_absent() -> None:
    assert find_problems(build(quote=None)) == []


def test_qa_answers_are_checked_too() -> None:
    qa = [{"question": "왜 그런가요?", "answer": "양자 컴퓨터가 맡고 있습니다.", "sources": []}]
    problems = find_problems(build(qa=qa))

    assert len(problems) == 1
    assert "qa[1].answer" in problems[0]


def test_there_is_no_effective_date_exception_any_more() -> None:
    """btc-daily-web 과 달리 여기는 발효일 예외가 없다.

    EFFECTIVE_DATE/TITLE_RULE_DATE 가 datetime.date.min 이라 존재하는 어떤 날짜도
    그보다 이르지 않다 — 즉 find_problems 의 스킵 분기(`meta.date < EFFECTIVE_DATE`)를
    탈 수 있는 날짜가 원천적으로 없다는 뜻이다. date.min 그 자체를 넣어도 검사가
    그대로 도는지로 이를 확인한다.
    """
    assert EFFECTIVE_DATE == datetime.date.min
    assert TITLE_RULE_DATE == datetime.date.min

    problems = find_problems(build(date=EFFECTIVE_DATE.isoformat(), body="검증이 안 끝났다."))

    assert len(problems) == 1
    assert "했습니다체가 아니다" in problems[0]


# ---- 2.1.1 제목 명사형 종결 ----


def test_noun_ending_title_passes() -> None:
    assert find_problems(build(title="벤치마크를 앞선 오픈AI 추론 모델")) == []


def test_plain_style_title_is_caught() -> None:
    problems = find_problems(build(title="오픈AI가 새 추론 모델을 공개했다"))

    assert len(problems) == 1
    assert "명사로 끝낸다" in problems[0]


def test_interrogative_title_is_caught() -> None:
    """'~는가/~인가'는 사설형 제목에서 반복되던 형태다."""
    for title in ("주도권은 어디로 가는가", "이것이 최선인가", "얼마나 더 발전할까"):
        problems = find_problems(build(title=title))
        assert len(problems) == 1, title
        assert "명사로 끝낸다" in problems[0]


def test_nouns_ending_in_ga_are_not_mistaken_for_questions() -> None:
    """단순히 '가'로 판정하면 재평가·물가·국가·전문가가 전부 걸린다 — 앞 음절까지 봐야 한다."""
    for title in ("업계의 재평가", "오른 전기요금 물가", "AI 규제에 나선 국가"):
        assert find_problems(build(title=title)) == [], title


def test_noun_exception_keeps_a_da_ending_noun() -> None:
    assert find_problems(build(title="데이터센터 지붕 위에 앉은 판다")) == []


def test_trending_topic_is_checked_but_source_titles_are_not() -> None:
    payload = json.loads(REFERENCE_CONTENT.read_text(encoding="utf-8"))
    payload["meta"]["date"] = SOME_DATE
    for card in payload["cards"]:
        # 레퍼런스 원문 제목 중 일부는 서술형으로 끝난다(비트코인 카드뉴스 시절
        # 디자인 레퍼런스라 이 프로젝트의 제목 규칙을 지키지 않는다) — 여기서
        # 보는 건 트렌딩/커버 예외지 제목 규칙이 아니므로 노이즈를 없앤다.
        card.update({"title": "소식 정리", "body": "그렇습니다.", "quote": None, "qa": None})
    items: list[dict[str, Any]] = [
        {"rank": rank, "topic": f"토픽 {rank}", "heat": 100 - rank, "mentions": 3, "sources": 2}
        for rank in range(1, 11)
    ]
    items[0]["topic"] = "양자 컴퓨터 투자"
    # 기사 원제는 그대로 옮기는 게 계약이라 여기서 걸면 안 된다.
    items[0]["links"] = [
        {"title": "594 BTC moved", "href": "https://e.com/a", "source": "Decrypt"}
    ]
    payload["trending"] = {
        "eyebrow": "24H TRENDING",
        "title": "지난 24시간 가장 뜨거웠던 토픽",
        "note": "뉴스 10건 3매체 집계",
        "items": items,
    }
    problems = find_problems(EditionContent.model_validate(payload))

    assert len(problems) == 1
    assert "트렌딩 1위 topic" in problems[0]


def test_cover_quote_is_not_subject_to_the_polite_ending_rule() -> None:
    """인용문은 번역된 남의 말이라 어미를 고칠 대상이 아니다.

    튜링을 "…없습니다"로 바꿀 수는 없다. 나중에 누가 "커버도 검사해야지" 하고
    find_problems 에 cover 를 추가하면 이 테스트가 막는다.
    """
    payload = json.loads(REFERENCE_CONTENT.read_text(encoding="utf-8"))
    payload["meta"]["date"] = SOME_DATE
    for card in payload["cards"]:
        # 레퍼런스 원문 제목 중 일부는 서술형으로 끝난다 — 여기서 보는 건 커버
        # 인용구 예외지 제목 규칙이 아니므로 노이즈를 없앤다.
        card.update({"title": "소식 정리", "body": "그렇습니다.", "quote": None, "qa": None})
    payload["cover"]["quote"] = {
        "id": "turing-can-machines-think",
        "text": '나는 "기계가 생각할 수 있는가"라는 질문을 다뤄보려 한다.',
        "author": "앨런 튜링",
        "portrait": None,
    }
    payload.pop("trending", None)

    assert find_problems(EditionContent.model_validate(payload)) == []
