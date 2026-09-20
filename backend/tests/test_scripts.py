import datetime
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.schemas import EditionContent
from scripts import collect_daily, generate_qa, push_edition, recent_editions, verify_edition

NOW = datetime.datetime(2026, 7, 31, 3, 0, tzinfo=datetime.UTC)

REFERENCE_CONTENT = Path(__file__).resolve().parents[2] / "reference" / "content.json"


def reference_payload(date: str = "2026-07-30") -> dict[str, Any]:
    """push_edition/generate_qa 테스트용 에디션 — reference/content.json 을 씨앗으로 쓴다.

    reference/content.json 은 비트코인 카드뉴스 시절 디자인 레퍼런스라(CLAUDE.md
    명시 예외) 서술형 제목·평서체 본문이 섞여 있어 app/wording.py 의 문구 게이트를
    통과하지 못한다 — 이 프로젝트에는 더 이상 발효일 예외가 없어(wording.py 참고)
    push_edition.main() 이 그 자리에서 SystemExit 을 낸다. 여기서 보는 건 문구가
    아니라 스키마·커버 일치·API 흐름이므로, 카드 내용을 게이트를 통과하는 형태로
    갈아끼워 노이즈를 없앤다.
    """
    payload = json.loads(REFERENCE_CONTENT.read_text(encoding="utf-8"))
    payload["meta"]["date"] = date
    payload["cover"] = collect_daily.apply_date_to_cover(
        payload["cover"], datetime.date.fromisoformat(date)
    )
    for card in payload["cards"]:
        card["title"] = f"소식 정리 {card['num']}"
        card["body"] = "그렇습니다."
        card["quote"] = None
    return payload


def make_news(**overrides: Any) -> dict[str, Any]:
    base = {
        "source_ref": "Cointelegraph",
        "crawled_at": "2026-07-31T02:00:00+00:00",
        "is_duplicate": False,
        "dup_count": 0,
    }
    base.update(overrides)
    return base


# 대량 생성 테스트(등급별 N건 채우기 등)에서 쓰는 회전 제목 재료. 같은 문장을
# 그대로 반복하면(예전 방식) collect_daily.cluster_events 가 전부 한 사건으로
# 접어버려 "N건을 채운다" 전제가 깨진다 — 공유 단어를 "briefing" 하나 + 순환하는
# 용어 하나로 제한해 두 제목 사이 공유 앵커가 EVENT_MIN_ANCHORS(3) 밑에 머물게
# 한다(용어가 우연히 겹쳐도 앵커 2개뿐이라 안 묶인다).
_QUANTUM_TITLE_TERMS = [
    "IonQ", "Quantinuum", "D-Wave", "Rigetti", "Pasqal",
    "QuEra", "PsiQuantum", "Qiskit", "qubit", "QKD", "PQC",
]
_PHYSICS_TITLE_TERMS = [
    "superconducting", "cryogenic", "photonic", "laser", "neutrino",
    "CERN", "LHC", "condensed matter", "atomic clock", "cold atom", "semiconductor",
]


def rotating_title(terms: list[str], i: int) -> str:
    return f"{terms[i % len(terms)]} briefing {i}"


def make_video(**overrides: Any) -> dict[str, Any]:
    base = {
        "id": "abc123",
        "topic": "양자컴퓨팅",
        "summary": "요약",
        "published_at": "2026-07-31T02:00:00+00:00",
        "added_at": "2026-07-31T02:00:00+00:00",
        "view_count": 100,
    }
    base.update(overrides)
    return base


def make_qa_response(prefix: str = "Q") -> dict[str, Any]:
    questions = [
        {
            "question": f"{prefix}{i}",
            "answer": f"A{i}",
            "sources": [f"https://example.com/{i}"],
        }
        for i in range(1, generate_qa.QUESTIONS_PER_CARD + 1)
    ]
    return {
        "steps": [
            {
                "type": "model_output",
                "content": [{"type": "text", "text": json.dumps({"questions": questions})}],
            }
        ]
    }


# ---- collect_daily 사건 클러스터링 (cluster_events / collapse_events) ----


def event_news(title: str, **overrides: Any) -> dict[str, Any]:
    """클러스터링 테스트용 후보 — 제목만 다르고 나머지는 창 안쪽 기본값."""
    return make_news(title=title, **overrides)


def test_event_signature_drops_the_bracket_head_and_the_outlet_tail() -> None:
    """말머리와 매체 꼬리는 사건과 무관한데 여러 기사에 공통으로 붙어 유사도를 부풀린다."""
    sig = collect_daily._event_signature(
        {"title": "[특징주] 오픈AI 할라페뇨 추론칩 공개 - 조선비즈"}
    )

    assert {item for item in sig if item.startswith("W:")} == {
        "W:오픈",
        "W:할라페뇨",
        "W:추론칩",
        "W:공개",
    }


def test_event_signature_keeps_hangul_bigrams_but_not_latin_ones() -> None:
    """한글만 바이그램을 담는다 — 영문 바이그램은 in·er 같은 흔한 쌍으로 다 붙어버린다."""
    sig = collect_daily._event_signature({"title": "엔비디아 vera rubin the new chip"})

    assert {"W:엔비디아", "W:vera", "W:rubin", "W:chip"} <= sig
    assert {"k:엔비", "k:비디", "k:디아"} <= sig
    assert not any(item.startswith("k:") and item[2:].isascii() for item in sig)
    # 영문 기능어는 모든 기사가 공유하므로 서명에서 뺀다.
    assert "W:the" not in sig
    assert "W:new" not in sig


def test_company_name_alone_does_not_make_two_articles_one_event() -> None:
    """회사명 하나로 군이 뭉치던 버그의 회귀 테스트.

    하한을 바이그램 개수로 걸었을 때는 "엔비디아"가 엔비/비디/디아 셋이라 그것만
    겹쳐도 통과했다 — 서로 다른 사건 5건이 후보 1위에 10건짜리 한 군으로 뭉쳤다.
    지금은 온전한 단어를 따로 세므로(EVENT_MIN_ANCHORS), 겹침 비율이 임계값을
    넘더라도 회사명 하나로는 못 넘는다.
    """
    a = collect_daily._event_signature({"title": "엔비디아 신용 노출"})
    b = collect_daily._event_signature({"title": "엔비디아 젯슨 출시"})

    assert collect_daily._overlap(a, b) >= collect_daily.EVENT_SIM_THRESHOLD
    assert collect_daily._shared_anchors(a, b) == 1
    assert collect_daily._same_event(a, b) is False


def test_cluster_events_folds_one_event_written_by_several_outlets() -> None:
    items = [
        event_news("오픈AI, 자체 추론칩 할라페뇨 공개", source_ref="지디넷"),
        event_news("오픈AI 추론칩 '할라페뇨' 공개…엔비디아 의존 줄인다", source_ref="전자신문"),
        event_news("[속보] 오픈AI 할라페뇨 추론칩 공개 - 조선비즈", source_ref="조선비즈"),
        event_news("구글, 제미나이 3 모델 출시", source_ref="블로터"),
    ]

    groups = collect_daily.cluster_events(items)

    assert [[n["source_ref"] for n in group] for group in groups] == [
        ["지디넷", "전자신문", "조선비즈"],
        ["블로터"],
    ]


def test_cluster_events_does_not_chain_through_a_middle_article() -> None:
    """A~B 이고 B~C 라고 A~C 까지 한 군이 되면 안 된다.

    단일 연결만 쓰면 주제가 조금씩 옮겨가는 사슬로 무관한 사건이 한 군이 된다.
    군의 머리와도 EVENT_HEAD_THRESHOLD 이상 겹치라는 두 번째 조건이 그걸 끊는다.
    """
    head = event_news("오픈AI 할라페뇨 추론칩 공개", source_ref="head")
    middle = event_news(
        "오픈AI 할라페뇨 추론칩 공개에 엔비디아 데이터센터 주문 감소 전망", source_ref="middle"
    )
    tail = event_news("엔비디아 데이터센터 주문 감소 전망에 월가 목표주가 하향", source_ref="tail")

    # tail 은 middle 하고만 보면 같은 사건 판정이 난다 — 끊는 건 머리 관문이다.
    assert collect_daily._same_event(
        collect_daily._event_signature(middle), collect_daily._event_signature(tail)
    )
    assert (
        collect_daily._overlap(
            collect_daily._event_signature(tail), collect_daily._event_signature(head)
        )
        < collect_daily.EVENT_HEAD_THRESHOLD
    )

    groups = collect_daily.cluster_events([head, middle, tail])

    assert [[n["source_ref"] for n in group] for group in groups] == [
        ["head", "middle"],
        ["tail"],
    ]


def test_event_signature_drops_the_domain_word_quantum() -> None:
    """'quantum' 은 이 코퍼스 제목의 53%에 들어 있어 사건을 특정하지 못한다.

    ai-daily-web 에는 이 방어가 없다 — 거기서는 도메인 낱말이 "ai" 두 글자라
    앵커 정규식([a-z]{3,})에 애초에 안 걸렸다. 'quantum' 은 일곱 글자라 그냥 두면
    무관한 기사가 이 한 낱말로 이어진다 (2026-09-20 실측: "PQC 제조업 전망"과
    "초유체 큐비트"가 한 군이 됐고, 접힌 건수가 83건·최대 군 9매체였다).
    """
    sig = collect_daily._event_signature(
        {"title": "Superfluid qubit could scale up quantum computers"}
    )

    assert "W:quantum" not in sig
    assert "W:computers" not in sig
    assert "W:could" not in sig
    assert "W:superfluid" in sig  # 사건을 특정하는 낱말은 남는다
    assert "W:qubit" in sig


def test_unrelated_quantum_articles_do_not_collapse_into_one_event() -> None:
    """도메인 낱말만 공유하는 두 기사는 같은 사건이 아니다."""
    a = {"title": "PQC predicts quantum computing could reshape manufacturing by 2030",
         "url": "https://example.com/a", "source_ref": "A"}
    b = {"title": "Superfluid qubit could help scale up quantum computers",
         "url": "https://example.com/b", "source_ref": "B"}

    assert len(collect_daily.cluster_events([a, b])) == 2


def test_collapse_events_promotes_the_group_member_that_has_an_image() -> None:
    """이미지는 대표를 바꿔서 얻는다 — 다른 매체 이미지를 가져다 붙이지 않는다."""
    first = event_news(
        "오픈AI, 자체 추론칩 할라페뇨 공개",
        source_ref="지디넷",
        url="https://a.example/1",
        image_url=None,
    )
    with_image = event_news(
        "오픈AI 추론칩 '할라페뇨' 공개…엔비디아 의존 줄인다",
        source_ref="전자신문",
        url="https://b.example/2",
        image_url="https://img.example/b.jpg",
    )
    third = event_news(
        "[속보] 오픈AI 할라페뇨 추론칩 공개 - 조선비즈",
        source_ref="조선비즈",
        url="https://c.example/3",
        image_url="https://img.example/c.jpg",
    )

    collapsed = collect_daily.collapse_events([first, with_image, third])

    assert len(collapsed) == 1
    rep = collapsed[0]
    # 이미지와 링크가 같은 기사에서 나온다.
    assert rep["image_url"] == "https://img.example/b.jpg"
    assert rep["url"] == "https://b.example/2"
    assert rep["cluster_size"] == 3
    assert rep["also_covered_by"] == ["지디넷", "조선비즈"]
    assert [t["title"] for t in rep["cluster_titles"]] == [first["title"], third["title"]]
    assert [t["url"] for t in rep["cluster_titles"]] == [
        "https://a.example/1",
        "https://c.example/3",
    ]


def test_collapse_events_keeps_the_first_article_when_the_group_has_no_image() -> None:
    first = event_news("오픈AI, 자체 추론칩 할라페뇨 공개", source_ref="지디넷")
    second = event_news("오픈AI 추론칩 '할라페뇨' 공개…엔비디아 의존 줄인다", source_ref="전자신문")

    collapsed = collect_daily.collapse_events([first, second])

    assert collapsed[0]["source_ref"] == "지디넷"
    assert collapsed[0]["cluster_size"] == 2


def test_collapse_events_marks_a_lone_article_as_a_group_of_one() -> None:
    """카드를 고르는 쪽이 필드 유무를 따지지 않게, 단독 기사에도 같은 키를 붙인다."""
    collapsed = collect_daily.collapse_events([event_news("구글, 제미나이 3 모델 출시")])

    assert collapsed[0]["cluster_size"] == 1
    assert collapsed[0]["also_covered_by"] == []
    assert collapsed[0]["cluster_titles"] == []


def test_collapse_events_does_not_mutate_the_input_items() -> None:
    items = [
        event_news("오픈AI, 자체 추론칩 할라페뇨 공개", source_ref="지디넷"),
        event_news("오픈AI 추론칩 '할라페뇨' 공개…엔비디아 의존 줄인다", source_ref="전자신문"),
    ]
    before = [dict(n) for n in items]

    collect_daily.collapse_events(items)

    assert items == before


def test_filter_news_folds_events_before_counting_the_limit() -> None:
    """상한은 접은 뒤에 센다 — 접기 전에 자르면 상위 칸을 같은 사건이 나눠 먹는다."""
    distinct = [
        make_news(source_ref=f"quantum-{i}", title=rotating_title(_QUANTUM_TITLE_TERMS, i))
        for i in range(collect_daily.NEWS_LIMIT)
    ]
    same_event = [
        make_news(source_ref=f"dup-{i}", title=rotating_title(_QUANTUM_TITLE_TERMS, 0))
        for i in range(4)
    ]

    result = collect_daily.filter_news(distinct + same_event, NOW)

    # 접고 세야 "서로 다른 사건 100건"이다. 잘라 놓고 접었다면 96건이 됐다.
    assert len(result) == collect_daily.NEWS_LIMIT
    assert len({n["title"] for n in result}) == collect_daily.NEWS_LIMIT
    assert result[0]["cluster_size"] == 5


def test_filter_news_puts_a_multi_outlet_event_above_a_lone_fresher_article() -> None:
    """최종 정렬 축은 등급 → 매체 수 → 트렌딩 → 최신순이다.

    축 순서가 뒤집혀 있을 때는 7개 매체가 쓴 오픈AI 할라페뇨 발표가 단독 기사들
    아래로 내려가 있었다(2026-08-26 실측).
    """
    older = "2026-07-31T02:00:00+00:00"
    event = [
        event_news("오픈AI, 자체 추론칩 할라페뇨 공개", source_ref="지디넷", crawled_at=older),
        event_news(
            "오픈AI 추론칩 '할라페뇨' 공개…엔비디아 의존 줄인다",
            source_ref="전자신문",
            crawled_at=older,
        ),
        event_news(
            "[속보] 오픈AI 할라페뇨 추론칩 공개 - 조선비즈",
            source_ref="조선비즈",
            crawled_at=older,
        ),
    ]
    lone = event_news(
        "구글, 제미나이 3 모델 출시", source_ref="블로터", crawled_at="2026-07-31T02:55:00+00:00"
    )

    result = collect_daily.filter_news([lone, *event], NOW)

    assert [n["source_ref"] for n in result] == ["지디넷", "블로터"]
    assert result[0]["cluster_size"] == 3
    assert result[0]["also_covered_by"] == ["전자신문", "조선비즈"]


# ---- collect_daily.filter_news ----


def test_filter_news_excludes_duplicates() -> None:
    items = [make_news(is_duplicate=True), make_news(is_duplicate=False)]

    result = collect_daily.filter_news(items, NOW)

    assert len(result) == 1
    assert result[0]["is_duplicate"] is False


def test_filter_news_caps_how_much_one_source_can_take() -> None:
    """arXiv 가 하루 70편을 쏟아내도 후보를 독식하면 안 된다.

    소스가 10곳뿐이라 상한이 없으면 논문이 후보 100칸 중 41칸을 먹고 이미지
    보유율이 87%에서 43%로 떨어진다(2026-09-20 실측).
    """
    flood = [
        make_news(source="arxivquantph", url=f"a{i}", title=f"Paper about entanglement {i}")
        for i in range(collect_daily.SOURCE_CAP + 20)
    ]
    others = [
        make_news(source="quantumreport", url=f"q{i}", title=f"Funding round {i}")
        for i in range(10)
    ]

    result = collect_daily.filter_news(flood + others, NOW)

    assert sum(1 for n in result if n["source"] == "arxivquantph") == collect_daily.SOURCE_CAP
    assert sum(1 for n in result if n["source"] == "quantumreport") == 10


def test_filter_news_excludes_items_published_before_the_window() -> None:
    """보관 창이 긴 소스를 처음 크롤하면 과거 기사가 전부 "방금 수집됨"이 된다.

    Physics World 피드 하나가 444일치 160건을 들고 있다 — 수집 시각만 보면
    2025년 노벨상 회고가 이번 주 후보로 올라온다 (2026-09-20 첫 수집 실측:
    후보 100건 중 37건이 게시 기준으로 창 밖이었다).
    """
    # my-news 의 published_at 은 오프셋이 없다 — 이 형태 그대로 시험해야 의미가 있다.
    items = [
        make_news(source_ref="archive", crawled_at=NOW.isoformat(),
                  published_at="2025-10-09T00:00:00"),
        make_news(source_ref="fresh", crawled_at=NOW.isoformat(),
                  published_at="2026-07-30T00:00:00"),
    ]

    result = collect_daily.filter_news(items, NOW)

    assert [n["source_ref"] for n in result] == ["fresh"]


def test_filter_news_keeps_items_without_a_published_at() -> None:
    """게시 시각을 안 주는 소스를 통째로 버리면 안 된다 — 있는 정보로만 거른다."""
    items = [make_news(source_ref="no-pub", crawled_at=NOW.isoformat(), published_at=None)]

    assert [n["source_ref"] for n in collect_daily.filter_news(items, NOW)] == ["no-pub"]


def test_filter_news_excludes_items_older_than_the_window() -> None:
    inside = NOW - datetime.timedelta(hours=collect_daily.NEWS_WINDOW_HOURS - 1)
    outside = NOW - datetime.timedelta(hours=collect_daily.NEWS_WINDOW_HOURS + 1)
    items = [make_news(crawled_at=outside.isoformat()), make_news(crawled_at=inside.isoformat())]

    result = collect_daily.filter_news(items, NOW)

    assert len(result) == 1
    assert result[0]["crawled_at"] == inside.isoformat()


def test_filter_news_ignores_dup_count_for_ordering() -> None:
    """dup_count 는 더 이상 정렬에 관여하지 않는다.

    실측상 dup_count 가 거의 항상 0이라 죽은 정렬키였다 — 그 자리를 시간 균등
    선별(NEWS_BUCKETS)이 대신한다. dup_count 를 crawled_at 과 반대로 둬서(가장
    오래된 게 dup_count 최대) 옛 정렬키였다면 뒤집혔을 순서가 crawled_at
    내림차순 그대로 나오는지 확인한다.
    """
    a = make_news(source_ref="A", dup_count=0, crawled_at="2026-07-31T02:55:00+00:00")
    b = make_news(source_ref="B", dup_count=3, crawled_at="2026-07-31T02:50:00+00:00")
    c = make_news(source_ref="C", dup_count=9, crawled_at="2026-07-31T02:45:00+00:00")

    result = collect_daily.filter_news([a, b, c], NOW)

    assert [n["source_ref"] for n in result] == ["A", "B", "C"]


def test_filter_news_orders_within_a_bucket_by_recency() -> None:
    """같은 6시간 구간 안에서는 crawled_at 이 더 최근인 기사가 먼저 나온다."""
    items = [
        make_news(source_ref="oldest", crawled_at="2026-07-31T02:00:00+00:00"),
        make_news(source_ref="newest", crawled_at="2026-07-31T02:50:00+00:00"),
        make_news(source_ref="middle", crawled_at="2026-07-31T02:25:00+00:00"),
    ]

    result = collect_daily.filter_news(items, NOW)

    assert [n["source_ref"] for n in result] == ["newest", "middle", "oldest"]


def test_filter_news_round_robins_evenly_across_the_buckets() -> None:
    """한 구간에 기사가 쏠려 있어도 라운드로빈이라 모든 구간이 같은 몫을 가져간다.

    주간 발행이라 창이 168시간이고 NEWS_BUCKETS 가 7이라 구간 하나가 하루다 —
    요일별로 고르게 뽑힌다. b0 에 넉넉히, 나머지 구간에 각 NEWS_LIMIT//7 건을
    두면 라운드로빈(구간당 1건씩 순회)이 딱 그 라운드 수 만에 NEWS_LIMIT 에 닿는다.
    구간끼리 시간이 겹치지 않으므로 최종 반환은 b0→b1→…→b6 순서가 된다.

    구간 수를 상수에서 읽는다 — 창 길이를 바꿀 때 이 테스트가 같이 따라가야 한다.
    """
    buckets = collect_daily.NEWS_BUCKETS
    share = collect_daily.NEWS_LIMIT // buckets
    bucket_hours = collect_daily.NEWS_WINDOW_HOURS / buckets

    # b0 에는 상한만큼 넉넉히, 나머지 구간에는 각자 share 만큼만 둔다.
    rows = [
        make_news(
            source_ref=f"b0-{i}", crawled_at=(NOW - datetime.timedelta(minutes=i + 1)).isoformat()
        )
        for i in range(collect_daily.NEWS_LIMIT)
    ]
    for b in range(1, buckets):
        base = NOW - datetime.timedelta(hours=bucket_hours * (b + 0.5))
        rows += [
            make_news(
                source_ref=f"b{b}-{i}",
                crawled_at=(base - datetime.timedelta(minutes=i)).isoformat(),
            )
            for i in range(share)
        ]

    result = collect_daily.filter_news(rows, NOW)
    got = [n["source_ref"] for n in result]

    # 구간마다 share 만큼씩 가져간다. NEWS_LIMIT 이 구간 수로 나누어떨어지지
    # 않으면(100 / 7 = 14 … 2) 남는 자리는 후보가 남아 있는 구간이 채운다 —
    # 이 표본에서는 b0 만 여유가 있으므로 b0 몫이 그만큼 늘어난다.
    leftover = collect_daily.NEWS_LIMIT - share * buckets
    counts = {b: sum(1 for r in got if r.startswith(f"b{b}-")) for b in range(buckets)}
    assert counts[0] == share + leftover
    assert all(counts[b] == share for b in range(1, buckets))
    assert len(got) == collect_daily.NEWS_LIMIT
    # 구간끼리 시간이 겹치지 않으므로 b0 → b1 → … 순서로 이어붙은 모양이 된다.
    assert got == sorted(got, key=lambda r: (int(r.split("-")[0][1:]), int(r.split("-")[1])))


def test_filter_news_fills_the_limit_from_remaining_buckets_when_others_are_empty() -> None:
    """구간1·2가 비어 있어도(기사가 구간0·3에만 몰려 있어도) NEWS_LIMIT 을 채운다.

    라운드로빈이 빈 구간(1,2)을 건너뛰고 남은 두 구간에서만 번갈아 뽑는다 —
    NEWS_LIMIT 에 닿으려면 각 절반씩 필요하고, 구간3은 정확히 그만큼 갖고 있어
    마침 그 시점에 소진된다.
    """
    half = collect_daily.NEWS_LIMIT // 2
    bucket_hours = collect_daily.NEWS_WINDOW_HOURS / collect_daily.NEWS_BUCKETS
    b0 = [
        make_news(
            source_ref=f"b0-{i}", crawled_at=(NOW - datetime.timedelta(minutes=i + 1)).isoformat()
        )
        for i in range(collect_daily.NEWS_LIMIT)
    ]
    b3_base = NOW - datetime.timedelta(hours=bucket_hours * 3.5)
    b3 = [
        make_news(
            source_ref=f"b3-{i}", crawled_at=(b3_base - datetime.timedelta(minutes=i)).isoformat()
        )
        for i in range(half)
    ]

    result = collect_daily.filter_news(b0 + b3, NOW)
    refs = [n["source_ref"] for n in result]

    assert len(refs) == collect_daily.NEWS_LIMIT
    assert sum(1 for r in refs if r.startswith("b0-")) == half
    assert sum(1 for r in refs if r.startswith("b3-")) == half


def test_filter_news_caps_at_the_limit() -> None:
    """전부 같은 6시간 구간(bucket0)에 몰려 나머지 3구간이 비어도 상한을 넘지 않는다.

    빈 구간이 있어도 유일하게 채워진 구간이 NEWS_LIMIT 몫을 전부 대신 내줘야 한다.
    """
    items = [make_news(source_ref=str(i)) for i in range(collect_daily.NEWS_LIMIT + 10)]

    result = collect_daily.filter_news(items, NOW)

    assert len(result) == collect_daily.NEWS_LIMIT


# ---- collect_daily.classify_relevance / 관련도 우선순위 ----


def test_classify_relevance_marks_quantum_only_articles_as_quantum() -> None:
    news = make_news(title="아이온큐, 256큐비트 양자컴퓨터 공개")

    assert collect_daily.classify_relevance(news) == "quantum"


def test_classify_relevance_marks_crypto_articles_tagged_ai_as_other() -> None:
    """크립토 배제 축의 핵심 방어선 — #인공지능 태그가 붙어도 본문이 코인 시황이면 other다.

    실제 위험 유형: 코인 매체(토큰포스트 등)가 시황 기사에 습관적으로 #인공지능
    태그를 붙이는 경우가 있다 — 태그를 곧이곧대로 믿으면 코인 시황이 ai 등급으로
    올라가 후보 상단을 차지하게 된다.
    """
    news = make_news(
        title="비트코인 8만달러 회복, 알트코인도 동반 상승",
        tags="['#인공지능', '#비트코인', '#알트코인']",
    )

    assert collect_daily.classify_relevance(news) == "other"


def test_classify_relevance_marks_physics_articles_as_physics() -> None:
    news = make_news(title="초전도 박막의 극저온 특성, 희석냉동기로 측정")

    assert collect_daily.classify_relevance(news) == "physics"


def test_classify_relevance_rejects_quantization_articles() -> None:
    """'양자화'는 quantization 의 번역어다 — LLM 경량화 기사가 양자 등급으로 새면 안 된다."""
    news = make_news(title="Qwen3.8 27B 4비트 양자화 벤치마크: 성능 유지")

    assert collect_daily.classify_relevance(news) != "quantum"


def test_classify_relevance_rejects_quantum_dot_tv_and_quantum_leap() -> None:
    """퀀텀닷 TV 는 디스플레이 기사이고 퀀텀점프는 비유다. 둘 다 실제로 후보에 섞여 들어왔다."""
    assert collect_daily.classify_relevance(
        make_news(title="삼성, 올해 QD TV 생산 80% 줄인다")) != "quantum"
    assert collect_daily.classify_relevance(
        make_news(title="K-방산, 자주포 넘어 퀀텀점프")) != "quantum"


def test_classify_relevance_does_not_treat_the_word_token_as_an_ai_signal() -> None:
    """'토큰'은 AI_TERMS에 없다.

    코인 시세 기사가 '토큰'이라는 단어만으로 ai 로 잘못 분류되면 안 된다.

    AI_TERMS 주석이 남긴 이유 그대로다: "토큰"을 넣으면 코인 기사를 그대로 끌고 온다.
    """
    news = make_news(title="리플 토큰 가격 급등, 커뮤니티 환호")

    assert collect_daily.classify_relevance(news) == "other"


def test_classify_relevance_matches_ascii_terms_on_word_boundaries() -> None:
    """'rag' 가 'storage' 안에, 'ai' 가 'said' 안에 걸리면 무관한 기사가 ai로 잘못 분류된다."""
    news = make_news(title="AWS said its storage price cuts boosted margin")

    assert collect_daily.classify_relevance(news) == "other"


def test_filter_news_attaches_relevance_to_every_candidate() -> None:
    items = [make_news(title="아이온큐, 256큐비트 양자컴퓨터 공개")]

    result = collect_daily.filter_news(items, NOW)

    assert result[0]["relevance"] == "quantum"


def test_filter_news_fills_quantum_before_physics_before_other() -> None:
    """등급이 바깥 축이다 — 더 최근이어도 other 는 ai 뒤로 밀린다.

    other 를 가장 최근으로 두고 ai 를 가장 오래된 것으로 둔다. 예전처럼 최신순
    단일 정렬이었다면 other 가 맨 앞에 왔을 배치다.
    """
    other = make_news(
        source_ref="other", title="코스피 사흘째 상승 마감", crawled_at="2026-07-31T02:55:00+00:00"
    )
    industry = make_news(
        source_ref="physics",
        title="초전도 박막 극저온 특성 측정",
        crawled_at="2026-07-31T02:50:00+00:00",
    )
    ai = make_news(
        source_ref="quantum",
        title="아이온큐 256큐비트 공개",
        crawled_at="2026-07-31T02:45:00+00:00",
    )

    result = collect_daily.filter_news([other, industry, ai], NOW)

    assert [n["source_ref"] for n in result] == ["quantum", "physics", "other"]


def test_filter_news_truncates_lower_tiers_when_quantum_fills_the_limit() -> None:
    """ai 만으로 NEWS_LIMIT 이 차면 다른 등급 기사는 후보에 아예 안 들어온다."""
    ai = [
        make_news(
            source_ref=f"quantum-{i}",
            title=rotating_title(_QUANTUM_TITLE_TERMS, i),
            crawled_at=(NOW - datetime.timedelta(minutes=i + 1)).isoformat(),
        )
        for i in range(collect_daily.NEWS_LIMIT)
    ]
    other = [make_news(source_ref="other", title="코스피 사흘째 상승 마감")]

    result = collect_daily.filter_news(ai + other, NOW)

    assert len(result) == collect_daily.NEWS_LIMIT
    assert all(n["relevance"] == "quantum" for n in result)


def test_filter_news_puts_priority_urls_first_within_a_bucket() -> None:
    """같은 구간·같은 등급이면 트렌딩에 걸린 기사가 최신순보다 앞선다."""
    items = [
        make_news(
            source_ref="newest",
            title="오픈AI 소식 A",
            url="https://example.com/newest",
            crawled_at="2026-07-31T02:55:00+00:00",
        ),
        make_news(
            source_ref="hot",
            title="오픈AI 소식 B",
            url="https://example.com/hot",
            crawled_at="2026-07-31T02:00:00+00:00",
        ),
    ]

    result = collect_daily.filter_news(items, NOW, priority_urls=["https://example.com/hot"])

    assert [n["source_ref"] for n in result] == ["hot", "newest"]


def test_filter_news_without_priority_urls_stays_on_recency() -> None:
    items = [
        make_news(
            source_ref="older", title="오픈AI 소식 A", crawled_at="2026-07-31T02:00:00+00:00"
        ),
        make_news(
            source_ref="newer", title="오픈AI 소식 B", crawled_at="2026-07-31T02:55:00+00:00"
        ),
    ]

    result = collect_daily.filter_news(items, NOW)

    assert [n["source_ref"] for n in result] == ["newer", "older"]


def test_filter_news_does_not_add_relevance_to_input_items() -> None:
    items = [make_news(title="오픈AI 소식")]

    collect_daily.filter_news(items, NOW)

    assert "relevance" not in items[0]


def test_trending_article_urls_dedupes_and_stops_at_the_priority_cutoff() -> None:
    """상위 TRENDING_PRIORITY_TOPICS 개까지만 보고, 토픽끼리 겹친 url 은 한 번만 낸다."""
    topics = [
        {"topic": f"t{i}", "articles": [{"url": f"https://example.com/{i}"}]}
        for i in range(collect_daily.TRENDING_PRIORITY_TOPICS + 2)
    ]
    topics[1]["articles"].append({"url": "https://example.com/0"})

    urls = collect_daily.trending_article_urls(topics)

    assert urls == [
        f"https://example.com/{i}" for i in range(collect_daily.TRENDING_PRIORITY_TOPICS)
    ]


# ---- collect_daily.physics_topups / 산업 예약 자리 ----


def test_physics_topups_keeps_only_the_physics_tier() -> None:
    """quantum 등급은 일부러 버린다 — 그쪽은 asset=quantum 피드가 이미 주워 온다."""
    items = [
        make_news(url="p", title="초전도 박막의 극저온 특성을 희석냉동기로 측정"),
        make_news(url="b", title="비트코인 8만달러 회복", tags="['ai']"),
        make_news(url="q", title="아이온큐 256큐비트 공개"),
    ]

    result = collect_daily.physics_topups(items, NOW)

    assert [n["url"] for n in result] == ["p"]


def test_physics_topups_skips_urls_already_in_the_base_feed() -> None:
    items = [make_news(url="dup", title="엔비디아 GPU 공급 부족")]

    assert collect_daily.physics_topups(items, NOW, {"dup"}) == []


def test_physics_topups_respects_the_news_window() -> None:
    stale = NOW - datetime.timedelta(hours=collect_daily.NEWS_WINDOW_HOURS + 1)
    items = [make_news(url="old", title="엔비디아 GPU 공급 부족", crawled_at=stale.isoformat())]

    assert collect_daily.physics_topups(items, NOW) == []


def test_filter_news_reserves_slots_for_physics_when_quantum_would_fill_the_limit() -> None:
    """ai 가 상한을 다 먹어도 산업(반도체·전력)이 후보에 보여야 한다."""
    ai = [
        make_news(
            source_ref=f"quantum-{i}",
            title=rotating_title(_QUANTUM_TITLE_TERMS, i),
            crawled_at=(NOW - datetime.timedelta(minutes=i + 1)).isoformat(),
        )
        for i in range(collect_daily.NEWS_LIMIT + 20)
    ]
    industry = [
        make_news(
            source_ref=f"physics-{i}",
            title=rotating_title(_PHYSICS_TITLE_TERMS, i),
            crawled_at=(NOW - datetime.timedelta(minutes=i + 1)).isoformat(),
        )
        for i in range(collect_daily.PHYSICS_RESERVE + 5)
    ]

    result = collect_daily.filter_news(ai + industry, NOW)

    assert len(result) == collect_daily.NEWS_LIMIT
    kept = sum(1 for n in result if n["relevance"] == "physics")
    assert kept == collect_daily.PHYSICS_RESERVE


def test_filter_news_gives_the_reserve_back_when_physics_is_short() -> None:
    """물리 기사가 예약분보다 적으면 남는 자리는 quantum 이 도로 가져간다."""
    quantum = [
        make_news(
            source_ref=f"quantum-{i}",
            title=rotating_title(_QUANTUM_TITLE_TERMS, i),
            crawled_at=(NOW - datetime.timedelta(minutes=i + 1)).isoformat(),
        )
        for i in range(collect_daily.NEWS_LIMIT + 20)
    ]
    physics = [make_news(source_ref="physics-0", title="초전도 박막 극저온 특성 측정")]

    result = collect_daily.filter_news(quantum + physics, NOW)

    assert len(result) == collect_daily.NEWS_LIMIT
    assert sum(1 for n in result if n["relevance"] == "physics") == 1
    assert sum(1 for n in result if n["relevance"] == "quantum") == collect_daily.NEWS_LIMIT - 1


# ---- collect_daily 이미지 중복배제 (average hash) ----
#
# 실제 이미지를 내려받지 않는다 — hash_image 를 가짜 함수로 주입해 필터 로직만 본다.
# 해시 계산 자체(average_hash)는 Pillow 로 만든 단색 이미지로 따로 확인한다.


def solid_png(shade: int) -> bytes:
    """한 가지 밝기로 채운 8x8 PNG. average_hash 테스트용."""
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("L", (8, 8), shade).save(buffer, format="PNG")
    return buffer.getvalue()


def test_average_hash_is_stable_for_the_same_image() -> None:
    image = solid_png(128)

    assert collect_daily.average_hash(image) == collect_daily.average_hash(image)


def test_average_hash_survives_resize() -> None:
    """리사이즈된 같은 그림은 해밍거리가 임계값 안에 들어와야 한다.

    토큰포스트처럼 같은 그림을 다른 크기·파일명으로 다시 올리는 매체를 잡는 근거다.
    실측(2026-08-23)에서는 _th_860x0 · -560x305 같은 실제 리사이즈 변형이 전부
    거리 0 으로 나왔다 — IMAGE_HASH_MAX_DISTANCE 를 0 쪽에 붙여 잡은 이유다.
    """
    import io

    from PIL import Image

    original = Image.new("L", (200, 120))
    for x in range(200):
        for y in range(120):
            original.putpixel((x, y), (x * 255) // 200)
    big, small = io.BytesIO(), io.BytesIO()
    original.save(big, format="PNG")
    original.resize((80, 48), Image.Resampling.LANCZOS).save(small, format="PNG")

    a = collect_daily.average_hash(big.getvalue())
    b = collect_daily.average_hash(small.getvalue())

    assert a is not None and b is not None
    assert collect_daily.hamming_distance(a, b) <= collect_daily.IMAGE_HASH_MAX_DISTANCE


def test_average_hash_returns_none_for_undecodable_bytes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert collect_daily.average_hash(b"not an image") is None
    assert capsys.readouterr().err != ""


def test_hamming_distance_counts_differing_bits() -> None:
    assert collect_daily.hamming_distance(0b1010, 0b1010) == 0
    assert collect_daily.hamming_distance(0b1010, 0b1011) == 1
    assert collect_daily.hamming_distance(0b0000, 0b1111) == 4


def test_filter_news_drops_image_url_matching_a_recent_edition() -> None:
    """최근 발행분과 같은 이미지면 image_url 만 뗀다 — 기사 자체는 남는다."""
    items = [make_news(source_ref="a", image_url="https://cdn/new.jpg")]

    result = collect_daily.filter_news(items, NOW, [0b1010], lambda _url: 0b1010)

    assert len(result) == 1
    assert result[0]["image_url"] is None
    assert result[0]["source_ref"] == "a"


def test_filter_news_keeps_image_url_that_is_far_enough() -> None:
    far = (1 << (collect_daily.IMAGE_HASH_MAX_DISTANCE + 1)) - 1  # 임계값보다 1비트 더 다르다
    items = [make_news(source_ref="a", image_url="https://cdn/new.jpg")]

    result = collect_daily.filter_news(items, NOW, [0], lambda _url: far)

    assert result[0]["image_url"] == "https://cdn/new.jpg"


def test_image_threshold_stays_tight_enough_to_avoid_false_positives() -> None:
    """임계값이 느슨해지는 회귀를 막는다.

    처음 12 로 뒀을 때 실제로 베이지색 서류함 일러스트와 네온 실루엣이 거리 11 로
    묶여 멀쩡한 후보의 image_url 이 떨어졌다. 실측 표본에서 서로 다른 이미지의
    최소 거리가 6이었으므로 임계값은 그보다 작아야 한다.
    """
    assert collect_daily.IMAGE_HASH_MAX_DISTANCE < 6


def test_filter_news_drops_duplicate_images_within_the_same_draft() -> None:
    """URL 이 달라도 같은 그림이면 뒤에 오는 쪽을 뗀다 — 토큰포스트 재업로드 대응."""
    items = [
        make_news(
            source_ref="a", crawled_at="2026-07-31T02:00:00+00:00", image_url="https://cdn/x.jpg"
        ),
        make_news(
            source_ref="b", crawled_at="2026-07-31T01:00:00+00:00", image_url="https://cdn/y.jpg"
        ),
    ]

    result = collect_daily.filter_news(items, NOW, (), lambda _url: 0b1010)

    assert result[0]["image_url"] == "https://cdn/x.jpg"  # 먼저 나온 쪽을 살린다
    assert result[1]["image_url"] is None


def test_filter_news_keeps_image_when_hashing_fails() -> None:
    """다운로드/디코딩 실패는 판정 불가 — 지레 떼지 않는다."""
    items = [make_news(source_ref="a", image_url="https://cdn/x.jpg")]

    result = collect_daily.filter_news(items, NOW, [0b1010], lambda _url: None)

    assert result[0]["image_url"] == "https://cdn/x.jpg"


def test_filter_news_without_hash_image_behaves_as_before() -> None:
    """hash_image 를 안 넘기면 예전과 동일하게 동작한다(기존 호출부 보호)."""
    items = [make_news(source_ref="a", image_url="https://cdn/x.jpg")]

    result = collect_daily.filter_news(items, NOW)

    assert result[0]["image_url"] == "https://cdn/x.jpg"


def test_filter_news_does_not_mutate_input_items() -> None:
    original = make_news(source_ref="a", image_url="https://cdn/x.jpg")
    items = [original]

    collect_daily.filter_news(items, NOW, [0b1010], lambda _url: 0b1010)

    assert original["image_url"] == "https://cdn/x.jpg"


# ---- collect_daily.trending_pool_* (집계용 코퍼스 — 카드 후보 필터와 목적이 다르다) ----


def test_trending_pool_news_keeps_duplicates() -> None:
    """중복 기사는 카드에선 버리지만 집계에선 신호다 — 여러 매체가 같은 사건을 다뤘다는 뜻."""
    items = [make_news(is_duplicate=True), make_news(is_duplicate=False)]

    assert len(collect_daily.trending_pool_news(items, NOW)) == 2


def test_trending_pool_news_has_no_cap() -> None:
    """카드 후보는 20건에서 자르지만 "그날 뭐가 핫했나"는 전체를 봐야 나온다."""
    items = [make_news(source_ref=str(i)) for i in range(120)]

    assert len(collect_daily.trending_pool_news(items, NOW)) == 120


def test_trending_pool_news_is_bounded_to_the_weekly_window() -> None:
    items = [
        make_news(crawled_at="2026-07-20T00:00:00+00:00"),  # 창(7일) 밖
        make_news(crawled_at="2026-07-31T01:00:00+00:00"),
    ]

    result = collect_daily.trending_pool_news(items, NOW)

    assert [n["crawled_at"] for n in result] == ["2026-07-31T01:00:00+00:00"]


def test_trending_pool_videos_does_not_require_a_summary() -> None:
    """요약은 카드 문구를 쓸 때 필요하다. 아직 안 붙었다고 그날 화제작이 통계에서 빠지면 안 된다."""
    items = [make_video(id="no-summary", summary="")]

    assert len(collect_daily.trending_pool_videos(items, NOW)) == 1


def test_trending_pool_videos_excludes_other_topics() -> None:
    items = [make_video(id="quantum"), make_video(id="btc", topic="비트코인")]

    result = collect_daily.trending_pool_videos(items, NOW)

    assert [v["id"] for v in result] == ["quantum"]


def test_trending_pool_videos_uses_the_same_weekly_window_as_the_cards() -> None:
    """주간 발행이라 집계도 "이번 주 무슨 일이 있었나"를 묻는다 — 카드와 같은 7일이다."""
    items = [
        make_video(id="fresh", published_at="2026-07-31T01:00:00+00:00"),
        make_video(id="stale", published_at="2026-07-20T01:00:00+00:00"),  # 창 밖
    ]

    result = collect_daily.trending_pool_videos(items, NOW)

    assert [v["id"] for v in result] == ["fresh"]


# ---- collect_daily.filter_videos ----


def test_filter_videos_requires_ai_topic_and_summary() -> None:
    items = [
        make_video(id="wrong-topic", topic="비트코인"),
        make_video(id="no-summary", summary=""),
    ]

    assert collect_daily.filter_videos(items, NOW) == []


def test_filter_videos_excludes_published_before_window() -> None:
    items = [make_video(published_at="2026-07-20T02:00:00+00:00")]  # NOW-11일

    assert collect_daily.filter_videos(items, NOW) == []


def test_filter_videos_keeps_video_published_within_the_weekly_window() -> None:
    items = [make_video(id="late-summary", published_at="2026-07-26T12:00:00+00:00")]  # NOW-4.6일

    assert [v["id"] for v in collect_daily.filter_videos(items, NOW)] == ["late-summary"]


def test_filter_videos_excludes_backfilled_old_video() -> None:
    """큐에 방금 들어왔어도 게시가 몇 주 전이면 버린다 (2026-08-04 회귀)."""
    items = [
        make_video(
            id="backfilled",
            published_at="2026-06-27T03:15:06+00:00",
            added_at="2026-07-31T02:55:00+00:00",  # 창 안이지만 게시는 한 달 전
            view_count=246_534,
        ),
        make_video(id="today", published_at="2026-07-31T01:00:00+00:00", view_count=900),
    ]

    assert [v["id"] for v in collect_daily.filter_videos(items, NOW)] == ["today"]


def test_filter_videos_excludes_missing_published_at() -> None:
    items = [make_video(published_at=None)]

    assert collect_daily.filter_videos(items, NOW) == []


def test_filter_videos_sorts_by_view_count_desc_capped_at_the_limit() -> None:
    count = collect_daily.VIDEO_LIMIT + 2
    items = [make_video(id=str(i), view_count=i) for i in range(count)]

    result = collect_daily.filter_videos(items, NOW)

    expected = [str(i) for i in range(count - 1, count - 1 - collect_daily.VIDEO_LIMIT, -1)]
    assert [v["id"] for v in result] == expected


def test_filter_videos_excludes_ids_in_exclude_ids() -> None:
    items = [make_video(id="keep", view_count=100), make_video(id="skip", view_count=200)]

    result = collect_daily.filter_videos(items, NOW, exclude_ids={"skip"})

    assert [v["id"] for v in result] == ["keep"]


def test_filter_videos_exclude_ids_defaults_to_empty_tuple() -> None:
    """기존 2-인자 호출(exclude_ids 생략)이 그대로 동작해야 한다."""
    items = [make_video(id="a", view_count=100), make_video(id="b", view_count=50)]

    result = collect_daily.filter_videos(items, NOW)

    assert [v["id"] for v in result] == ["a", "b"]


def test_filter_videos_backfills_from_lower_ranked_after_exclusion() -> None:
    """상위권이 exclude_ids 로 빠지면 그 아래가 VIDEO_LIMIT(5)까지 올라와야 한다."""
    items = [make_video(id=str(i), view_count=100 - i) for i in range(7)]  # id "0" 이 최고 조회수

    result = collect_daily.filter_videos(items, NOW, exclude_ids={"0", "1"})

    assert [v["id"] for v in result] == ["2", "3", "4", "5", "6"]


# ---- collect_daily.build_skeleton ----


def test_build_skeleton_generates_date_slug_title_and_sources() -> None:
    skeleton = collect_daily.build_skeleton(
        datetime.date(2026, 7, 31),
        theme={"bg": "#000"},
        brand="데일리 AI",
        cover_fixed={"eyebrow": "E", "mark": ["a"], "meta": ["x", "y", "old"], "hint": "h"},
        closing_fixed={
            "eyebrow": "E",
            "mark_lines": ["a"],
            "rows": [["k", "v"]],
            "stamp": "s",
            "restart": "r",
            "sources": [],
        },
        sources=["A", "B"],
    )

    assert skeleton["meta"] == {
        "title": "양자 하이라이트 · 7.31",
        "slug": "quantum-daily-0731",
        "date": "2026-07-31",
    }
    assert skeleton["cover"]["mark"] == ["7월 31일", "양자 카드뉴스"]
    assert skeleton["cover"]["meta"] == ["x", "y", "2026.07.31"]
    assert skeleton["closing"]["sources"] == ["A", "B"]


# ---- collect_daily 원문 URL 복원 · og:image 보강 ----

GNEWS_URL = collect_daily.GOOGLE_NEWS_ARTICLE + "CBMiTEST?oc=5"


def gnews_interstitial(signature: str = "Ae5Wzi_sig", timestamp: str = "1787746684") -> str:
    """구글 뉴스 인터스티셜 — 원문 대신 서명·타임스탬프만 심어서 준다."""
    return (
        '<!doctype html><html><body><c-wiz data-n-a-id="CBMiTEST" '
        f'data-n-a-sg="{signature}" data-n-a-ts="{timestamp}"></c-wiz></body></html>'
    )


def garturlres(url: str) -> str:
    """batchexecute 응답 — JSON 안에 JSON 문자열이 한 겹 더 들어 있는 구글 형식.

    구글은 안쪽 문자열에서 `=` 를 \\u003d 로 이스케이프한다. 실제 응답과 같게
    만들어 둬야 주소가 그 자리에서 잘리는 회귀를 잡는다.
    """
    inner = json.dumps(["garturlres", url, 1], ensure_ascii=False).replace("=", "\\u003d")
    envelope = json.dumps(
        [["wrb.fr", "Fbv4je", inner, None, None, None, ""], ["di", 15]], ensure_ascii=False
    )
    return ")]}'\n\n" + envelope


def gnews_client(publisher_url: str, **overrides: Any) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "news.google.com" and request.method == "POST":
            return httpx.Response(200, text=garturlres(publisher_url))
        return httpx.Response(200, text=gnews_interstitial(**overrides))

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_resolve_google_news_url_returns_the_publisher_url() -> None:
    with gnews_client("https://www.seoul.co.kr/news/international/2026/08/26/20260826010007") as c:
        resolved = collect_daily.resolve_google_news_url(c, GNEWS_URL)

    assert resolved == "https://www.seoul.co.kr/news/international/2026/08/26/20260826010007"


def test_resolve_google_news_url_keeps_the_query_string_intact() -> None:
    """정규식으로 한 번에 긁던 시절 `=` 이스케이프 자리에서 주소가 잘렸다.

    실측: aitimes.com 주소가 `?idxno` 까지만 남아 기사 대신 목록 페이지로 갔다.
    구글은 응답 안쪽 JSON 문자열에서 `=` 를 \u003d 로 이스케이프한다.
    """
    target = "https://www.aitimes.com/news/articleView.html?idxno=214335"
    with gnews_client(target) as c:
        assert collect_daily.resolve_google_news_url(c, GNEWS_URL) == target


def test_resolve_google_news_url_returns_none_without_a_signature() -> None:
    """구글이 인터스티셜 형식을 바꾸면 조용히 포기한다 — 원래 주소가 그대로 남는다."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(200, text="<html><body>no signature here</body></html>")

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        assert collect_daily.resolve_google_news_url(c, GNEWS_URL) is None

    assert calls == ["GET"]  # 서명이 없으면 RPC 는 두드리지 않는다


def test_resolve_google_news_url_returns_none_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        assert collect_daily.resolve_google_news_url(c, GNEWS_URL) is None


def og_client(html: str) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_og_image_url_reads_the_og_image_tag() -> None:
    html = '<html><head><meta property="og:image" content="https://img.example/a.jpg" /></head>'

    with og_client(html) as c:
        assert collect_daily.og_image_url(c, "https://news.example/1") == "https://img.example/a.jpg"


def test_og_image_url_absolutizes_a_relative_path() -> None:
    html = '<html><head><meta property="og:image" content="/photo/a.jpg" /></head>'

    with og_client(html) as c:
        image = collect_daily.og_image_url(c, "https://news.example/section/1")

    assert image == "https://news.example/photo/a.jpg"


def test_og_image_url_unescapes_html_entities_in_the_url() -> None:
    """속성값의 `&amp;` 를 그대로 쓰면 쿼리스트링이 깨진다(실측: itworld 이미지가 404)."""
    html_doc = (
        '<html><head><meta property="og:image" '
        'content="https://img.example/a.jpg?quality=50&amp;w=1024" /></head>'
    )

    with og_client(html_doc) as c:
        image = collect_daily.og_image_url(c, "https://news.example/1")

    assert image == "https://img.example/a.jpg?quality=50&w=1024"


def test_og_image_url_falls_back_to_twitter_image() -> None:
    html = '<html><head><meta name="twitter:image" content="https://img.example/t.jpg"></head>'

    with og_client(html) as c:
        assert collect_daily.og_image_url(c, "https://news.example/1") == "https://img.example/t.jpg"


def test_og_image_url_returns_none_when_the_page_has_no_image_meta() -> None:
    with og_client("<html><head><title>기사</title></head>") as c:
        assert collect_daily.og_image_url(c, "https://news.example/1") is None


def test_enrich_candidates_swaps_the_redirect_and_keeps_the_original() -> None:
    items = [make_news(source_ref="서울신문", url=GNEWS_URL)]

    result = collect_daily.enrich_candidates(
        items,
        lambda url: "https://www.seoul.co.kr/news/1",
        lambda url: "https://img.seoul.co.kr/1.jpg",
    )

    assert result[0]["url"] == "https://www.seoul.co.kr/news/1"
    assert result[0]["google_url"] == GNEWS_URL
    # 이미지는 되돌린 주소에서 가져온다 — 리디렉션 페이지에는 기사 사진이 없다.
    assert result[0]["image_url"] == "https://img.seoul.co.kr/1.jpg"


def test_enrich_candidates_fills_only_the_items_without_an_image() -> None:
    items = [
        make_news(source_ref="있음", url="https://news.example/1", image_url="https://img/a.jpg"),
        make_news(source_ref="없음", url="https://news.example/2"),
    ]
    asked: list[str] = []

    def fetch_image(url: str) -> str:
        asked.append(url)
        return "https://img/b.jpg"

    result = collect_daily.enrich_candidates(items, lambda url: None, fetch_image)

    assert asked == ["https://news.example/2"]
    assert result[0]["image_url"] == "https://img/a.jpg"
    assert result[1]["image_url"] == "https://img/b.jpg"


def test_enrich_candidates_leaves_the_item_alone_when_both_lookups_fail() -> None:
    """보강은 있으면 좋은 것이다 — 실패해도 후보를 버리거나 바꾸지 않는다."""
    items = [make_news(source_ref="A", url=GNEWS_URL)]

    result = collect_daily.enrich_candidates(items, lambda url: None, lambda url: None)

    assert result[0]["url"] == GNEWS_URL
    assert "google_url" not in result[0]
    assert result[0].get("image_url") is None


def test_enrich_candidates_does_not_mutate_input_items() -> None:
    items = [make_news(source_ref="A", url=GNEWS_URL)]
    before = [dict(n) for n in items]

    collect_daily.enrich_candidates(
        items, lambda url: "https://news.example/1", lambda url: "https://img/a.jpg"
    )

    assert items == before


def test_filter_news_enriches_before_image_dedupe() -> None:
    """보강으로 채운 이미지도 최근 발행분과 대조돼야 한다.

    순서가 뒤집히면(중복배제 -> 보강) 어제 쓴 사진이 오늘 카드에 그대로 다시 실린다.
    """
    recent = 1234
    items = [make_news(source_ref="A", url="https://news.example/1")]

    result = collect_daily.filter_news(
        items,
        NOW,
        exclude_image_hashes=[recent],
        hash_image=lambda url: recent,
        enrich=lambda picked: [{**n, "image_url": "https://img/dup.jpg"} for n in picked],
    )

    assert result[0]["image_url"] is None


# ---- collect_daily.main (httpx.MockTransport) ----


def _mock_handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "news" in url:
        return httpx.Response(200, json=[make_news(source_ref="X")])
    if "queue" in url:
        return httpx.Response(200, json={"items": [make_video(id="v1")]})
    return httpx.Response(404)


def test_collect_daily_main_writes_draft(tmp_path: Path) -> None:
    out_path = tmp_path / "draft.json"

    with httpx.Client(transport=httpx.MockTransport(_mock_handler)) as client:
        result_path = collect_daily.main(
            [
                "--date",
                "2026-07-31",
                "--out",
                str(out_path),
                "--news-url",
                "http://x/news",
                "--youtube-url",
                "http://x/queue",
            ],
            client=client,
        )

    assert result_path == out_path
    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert data["skeleton"]["meta"]["slug"] == "quantum-daily-0731"
    assert data["candidates"]["news"][0]["source_ref"] == "X"
    assert data["candidates"]["videos"][0]["thumbnail_url"] == (
        "https://i.ytimg.com/vi/v1/hqdefault.jpg"
    )


def test_collect_daily_main_writes_trending_corpus_note(tmp_path: Path) -> None:
    """무인 실행이 트렌딩 카드의 "N건 집계"를 지어내지 않도록 draft 가 note 를 준다."""
    out_path = tmp_path / "draft.json"

    with httpx.Client(transport=httpx.MockTransport(_mock_handler)) as client:
        collect_daily.main(
            [
                "--date",
                "2026-07-31",
                "--out",
                str(out_path),
                "--news-url",
                "http://x/news",
                "--youtube-url",
                "http://x/queue",
            ],
            client=client,
        )

    corpus = json.loads(out_path.read_text(encoding="utf-8"))["trending_corpus"]
    assert corpus["news"] == 1
    assert corpus["outlets"] == 1
    assert corpus["outlet_names"] == ["X"]
    assert corpus["note"].startswith("뉴스 1건 1매체 · 유튜브 ")


def test_corpus_summary_counts_distinct_outlets_not_articles() -> None:
    """한 매체가 여러 건을 써도 매체 수는 1이다 — 토큰포스트가 코퍼스 절반을 차지한다."""
    news = [make_news(source_ref="토큰포스트") for _ in range(5)] + [make_news(source_ref="B")]
    videos = [make_video(id="a"), make_video(id="b")]
    videos[0]["channel_title"] = "채널A"
    videos[1]["channel_title"] = "채널A"

    summary = collect_daily.corpus_summary(news, videos)

    assert summary["news"] == 6
    assert summary["outlets"] == 2
    assert summary["videos"] == 2
    assert summary["channels"] == 1
    assert summary["note"] == "뉴스 6건 2매체 · 유튜브 2건 1채널 집계"


def test_corpus_summary_ignores_items_missing_source_name() -> None:
    """source_ref/channel_title 이 빠진 항목이 매체 수를 부풀리면 안 된다."""
    news = [make_news(source_ref="A"), {"title": "출처 없음"}]

    summary = collect_daily.corpus_summary(news, [])

    assert summary["news"] == 2
    assert summary["outlets"] == 1


def test_collect_daily_main_fails_when_no_candidates(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "news" in str(request.url):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"items": []})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(SystemExit):
            collect_daily.main(
                [
                    "--date",
                    "2026-07-31",
                    "--out",
                    str(tmp_path / "draft.json"),
                    "--news-url",
                    "http://x/news",
                    "--youtube-url",
                    "http://x/queue",
                ],
                client=client,
            )


def test_collect_daily_main_fails_loudly_when_source_down(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(SystemExit, match="my-news"):
            collect_daily.main(
                [
                    "--date",
                    "2026-07-31",
                    "--out",
                    str(tmp_path / "draft.json"),
                    "--news-url",
                    "http://x/news",
                    "--youtube-url",
                    "http://x/queue",
                ],
                client=client,
            )


# ---- push_edition ----


def test_edition_scripts_default_to_this_projects_backend() -> None:
    """포크가 남긴 btc-daily-web 기본값의 회귀 테스트.

    `--api` 를 빠뜨렸을 때 오늘자 AI 에디션이 btc-daily-web 으로 새는 걸 막는다.
    축이 둘이라 기본값도 둘로 갈린다:

    - 발행처(push/recent) = 로컬 :8003. 손으로 돌릴 때 사고를 덜 내는 쪽이
      기본이어야 한다. 프로덕션 발행은 daily-cron.sh 가 `--api` 로 명시한다.
    - 이력 조회(collect) = 프로덕션. 무인 발행이 거기로 나가므로 "어제 뭐가
      나갔나"도 거기서 읽어야 중복 점검이 성립한다.

    어느 쪽이든 onebitebitcoin 이 섞이면 안 된다.
    """
    assert push_edition.DEFAULT_API == "http://localhost:8003"
    assert recent_editions.DEFAULT_API == "http://localhost:8003"
    assert push_edition.parse_args(["edition.json"]).api == "http://localhost:8003"
    assert recent_editions.parse_args([]).api == "http://localhost:8003"

    assert collect_daily.DEFAULT_EDITION_API == "https://daily.onebitecoder.com"
    assert "onebitebitcoin" not in collect_daily.DEFAULT_EDITION_API



def test_push_edition_local_validation_fails_before_post(tmp_path: Path) -> None:
    payload = reference_payload()
    del payload["meta"]["date"]
    edition_path = tmp_path / "edition.json"
    edition_path.write_text(json.dumps(payload), encoding="utf-8")

    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(SystemExit, match="스키마 검증 실패"):
            push_edition.main([str(edition_path)], client=client)

    assert called is False


def test_apply_date_to_cover_derives_mark_and_meta() -> None:
    cover = collect_daily.apply_date_to_cover(
        {"eyebrow": "E", "mark": ["old", "old"], "meta": ["x", "y", "old"], "hint": "h"},
        datetime.date(2026, 7, 31),
    )

    assert cover["mark"] == ["7월 31일", "양자 카드뉴스"]
    assert cover["meta"] == ["x", "y", "2026.07.31"]


def test_push_edition_rejects_cover_mark_contradicting_meta_date(tmp_path: Path) -> None:
    payload = reference_payload()
    payload["cover"]["mark"] = ["AI", "하이라이트"]  # stale/pre-fix cover (날짜 적용 전 픽스처 값)
    edition_path = tmp_path / "edition.json"
    edition_path.write_text(json.dumps(payload), encoding="utf-8")

    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(SystemExit, match="cover가 meta.date와 불일치"):
            push_edition.main([str(edition_path)], client=client)

    assert called is False


# ---- 삽화 출처 게이트 (push_edition.check_illustration_credit) ----
#
# 기사에서 이미지가 안 나오는 카드가 매 발행 나온다 — 2026-09-21 btc 발행은 10장 중
# 5장이 그랬다. 그 자리를 키워드 삽화로 채우기로 하면서 생긴 게이트다.


def _payload_with_media(image: str, credit: str | None) -> dict:
    payload = reference_payload()
    media = {"image": image, "href": None, "cta": None}
    if credit is not None:
        media["credit"] = credit
    payload["cards"][0]["media"] = media
    return payload


def test_illustration_from_a_known_host_requires_credit() -> None:
    """CC BY·BY-SA 는 저작자 표시가 라이선스 조건이다. 빠지면 발행을 막는다."""
    content = EditionContent(
        **_payload_with_media("https://upload.wikimedia.org/x/Bitcoin_farm.jpg", None)
    )

    with pytest.raises(SystemExit) as exc:
        push_edition.check_illustration_credit(content)

    assert "카드 1" in str(exc.value) and "credit" in str(exc.value)


def test_illustration_with_credit_passes() -> None:
    content = EditionContent(
        **_payload_with_media(
            "https://live.staticflickr.com/1/2_b.jpg",
            "Data Center · Cory M. Grenier (CC BY-SA 2.0)",
        )
    )

    push_edition.check_illustration_credit(content)  # 예외가 없으면 통과다


def test_article_photo_does_not_need_credit() -> None:
    """매체 CDN 에서 온 기사 사진은 그대로 둔다 — 도메인이 기사와 달라도 삽화가 아니다.

    tokenpost.kr 기사가 f1.tokenpost.kr 에 그림을 두는 식이 흔해서, 도메인 비교로
    판정하면 멀쩡한 기사 사진이 전부 걸린다. 그래서 삽화 호스트만 보고 가른다.
    """
    content = EditionContent(
        **_payload_with_media("https://f1.tokenpost.kr/2026/09/abc.jpg", None)
    )

    push_edition.check_illustration_credit(content)


def test_push_edition_accepts_cover_matching_meta_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    edition_path = tmp_path / "edition.json"
    edition_path.write_text(json.dumps(reference_payload()), encoding="utf-8")
    env_file = tmp_path / ".env"
    env_file.write_text("ADMIN_API_KEY=secret\n", encoding="utf-8")
    monkeypatch.setattr(push_edition, "ENV_FILE", env_file)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=request.content)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        # 링크·이미지 검증은 바깥 네트워크를 두드린다 — 여기 관심사가 아니라 끈다.
        result = push_edition.main([str(edition_path), "--skip-link-check"], client=client)

    assert result["cover"]["mark"] == ["7월 30일", "양자 카드뉴스"]


def test_push_edition_missing_api_key_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    edition_path = tmp_path / "edition.json"
    edition_path.write_text(json.dumps(reference_payload()), encoding="utf-8")
    monkeypatch.setattr(push_edition, "ENV_FILE", tmp_path / "nonexistent.env")

    with pytest.raises(SystemExit, match="ADMIN_API_KEY"):
        push_edition.main([str(edition_path)])


def test_push_edition_success_posts_validated_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    edition_path = tmp_path / "edition.json"
    edition_path.write_text(json.dumps(reference_payload()), encoding="utf-8")
    env_file = tmp_path / ".env"
    env_file.write_text("ADMIN_API_KEY=secret\n", encoding="utf-8")
    monkeypatch.setattr(push_edition, "ENV_FILE", env_file)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer secret"
        return httpx.Response(200, content=request.content)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = push_edition.main([str(edition_path), "--skip-link-check"], client=client)

    assert result["meta"]["date"] == "2026-07-30"


# ---- verify_edition (발행 직전 링크·이미지 검증) ----


def patterned_png(seed: int) -> bytes:
    """서로 다른 average hash 가 나오는 8x8 PNG. 단색은 전부 해시 0 이라 못 쓴다."""
    import io

    from PIL import Image

    image = Image.new("L", (8, 8))
    image.putdata([(seed * 37 + i * 11) % 256 for i in range(64)])
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def verify_card(
    num: int,
    *,
    href: str = "https://news.example/article/1",
    image: str | None = "https://img.example/1.png",
    title: str = "오픈AI 추론칩 공개",
    chips: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "num": num,
        "title": title,
        "chips": chips or [],
        "link": {"label": "매체 원문", "href": href},
        "media": {"image": image, "href": None, "cta": None} if image else None,
    }


def article_page(title: str = "오픈AI 추론칩 공개…성능 전격 공개") -> httpx.Response:
    return httpx.Response(
        200, text=f'<html><head><meta property="og:title" content="{title}" /></head></html>'
    )


def verify_client(routes: dict[str, httpx.Response]) -> httpx.Client:
    """URL 접두사로 응답을 고르는 가짜 웹. 목록에 없으면 404 다."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        for prefix, response in routes.items():
            if url.startswith(prefix):
                return response
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def png_response(seed: int = 1) -> httpx.Response:
    return httpx.Response(
        200, content=patterned_png(seed), headers={"content-type": "image/png"}
    )


def test_verify_passes_a_healthy_edition() -> None:
    content = {
        "cards": [
            verify_card(1),
            verify_card(
                2,
                href="https://news.example/article/2",
                image="https://other.example/2.png",
            ),
        ]
    }
    routes = {
        "https://news.example/": article_page(),
        "https://img.example/": png_response(1),
        "https://other.example/": png_response(200),
    }

    with verify_client(routes) as client:
        report = verify_edition.check_edition(content, client)

    assert report.fails == []


def test_verify_flags_a_link_that_is_just_the_outlet_homepage() -> None:
    """2026-08-26 실측: googlenews 후보의 리디렉션을 못 써서 카드 4장이 홈페이지로 나갔다."""
    content = {"cards": [verify_card(1, href="https://www.donga.com/")]}

    with verify_client({"https://img.example/": png_response()}) as client:
        report = verify_edition.check_edition(content, client)

    assert any("매체 홈페이지" in f for f in report.fails)


def test_verify_flags_a_google_news_redirect_link() -> None:
    href = collect_daily.GOOGLE_NEWS_ARTICLE + "CBMiTEST?oc=5"
    content = {"cards": [verify_card(1, href=href)]}

    with verify_client({"https://img.example/": png_response()}) as client:
        report = verify_edition.check_edition(content, client)

    assert any("구글 뉴스 리디렉션" in f for f in report.fails)


def test_verify_flags_a_dead_link() -> None:
    """2026-08-26 실측: 코인텔레그래프 주소가 404 인 채로 발행됐다."""
    content = {"cards": [verify_card(1, href="https://cointelegraph.com/features/gone")]}

    with verify_client({"https://img.example/": png_response()}) as client:
        report = verify_edition.check_edition(content, client)

    assert any("링크가 죽었다(404)" in f for f in report.fails)


def test_verify_treats_a_blocked_link_as_a_warning_not_a_failure() -> None:
    """매체가 봇을 막은 것뿐인데 발행을 못 하게 되면 손해가 더 크다."""
    content = {"cards": [verify_card(1)]}
    routes = {
        "https://news.example/": httpx.Response(403),
        "https://img.example/": png_response(),
    }

    with verify_client(routes) as client:
        report = verify_edition.check_edition(content, client)

    assert report.fails == []
    assert any("확인을 막았다(403)" in w for w in report.warns)


def test_verify_flags_two_cards_sharing_the_same_image_url() -> None:
    """표지가 1번 카드 이미지를 쓰므로 1번과 겹치면 한 화면에 세 번 나온다."""
    content = {
        "cards": [
            verify_card(1, image="https://img.example/same.png"),
            verify_card(2, href="https://news.example/article/2", image="https://img.example/same.png"),
        ]
    }
    routes = {"https://news.example/": article_page(), "https://img.example/": png_response()}

    with verify_client(routes) as client:
        report = verify_edition.check_edition(content, client)

    assert any("같은 이미지를 쓴다" in f for f in report.fails)


def test_verify_flags_the_same_image_served_under_two_urls() -> None:
    """2026-08-26 실측: 토큰포스트가 같은 브랜드 렌더를 다른 파일명으로 두 기사에 걸었다."""
    content = {
        "cards": [
            verify_card(1, image="https://img.example/a.png"),
            verify_card(2, href="https://news.example/article/2", image="https://img.example/b.png"),
        ]
    }
    routes = {"https://news.example/": article_page(), "https://img.example/": png_response(3)}

    with verify_client(routes) as client:
        report = verify_edition.check_edition(content, client)

    assert any("같은 이미지를 쓴다" in f for f in report.fails)


def test_verify_does_not_flag_two_different_images() -> None:
    content = {
        "cards": [
            verify_card(1, image="https://img.example/a.png"),
            verify_card(2, href="https://news.example/article/2", image="https://other.example/b.png"),
        ]
    }
    routes = {
        "https://news.example/": article_page(),
        "https://img.example/": png_response(1),
        "https://other.example/": png_response(200),
    }

    with verify_client(routes) as client:
        report = verify_edition.check_edition(content, client)

    assert report.fails == []


def test_verify_fails_when_the_image_url_does_not_serve_an_image() -> None:
    content = {"cards": [verify_card(1)]}
    routes = {
        "https://news.example/": article_page(),
        "https://img.example/": httpx.Response(200, text="<html>페이지</html>"),
    }

    with verify_client(routes) as client:
        report = verify_edition.check_edition(content, client)

    assert any("이미지를 안 준다" in f for f in report.fails)


def test_verify_warns_when_a_card_has_no_image() -> None:
    content = {"cards": [verify_card(1, image=None)]}

    with verify_client({"https://news.example/": article_page()}) as client:
        report = verify_edition.check_edition(content, client)

    assert report.fails == []
    assert any("기본 아트로 나간다" in w for w in report.warns)


def test_verify_notes_the_article_title_so_a_human_can_compare() -> None:
    """카드 10번이 알파경제 라벨로 TechCrunch 기사를 링크했던 사고를 사람이 잡게 한다."""
    content = {"cards": [verify_card(1)]}
    routes = {
        "https://news.example/": article_page("오픈AI 추론칩 공개…성능 전격 공개"),
        "https://img.example/": png_response(),
    }

    with verify_client(routes) as client:
        report = verify_edition.check_edition(content, client)

    assert any("오픈AI 추론칩 공개" in note for note in report.notes)


def test_verify_warns_when_card_and_article_titles_share_no_word() -> None:
    content = {"cards": [verify_card(1, title="우주로 올라가는 AI 데이터센터")]}
    routes = {
        "https://news.example/": article_page("앤트로픽 기업가치 30조 달러 제시"),
        "https://img.example/": png_response(),
    }

    with verify_client(routes) as client:
        report = verify_edition.check_edition(content, client)

    assert any("겹치는 낱말이 없다" in w for w in report.warns)


def test_verify_does_not_warn_when_the_article_is_in_another_language() -> None:
    """한국어 카드와 영문 원문은 낱말이 겹칠 리가 없다 — 매번 경고하면 경고를 안 읽게 된다."""
    content = {"cards": [verify_card(1, title="IPO 앞둔 오픈AI에서 떠난 데이터센터 총괄")]}
    routes = {
        "https://news.example/": article_page("OpenAI loses a top data center exec"),
        "https://img.example/": png_response(),
    }

    with verify_client(routes) as client:
        report = verify_edition.check_edition(content, client)

    assert report.fails == []
    assert not any("겹치는 낱말이 없다" in w for w in report.warns)


def test_verify_flags_a_source_no_card_links() -> None:
    """실측: 카드 10번을 알파경제에서 TechCrunch 로 바꿨는데 출처 목록은 그대로였다."""
    content = {
        "cards": [verify_card(1, image=None)],
        "closing": {"sources": ["알파경제"]},
    }
    content["cards"][0]["link"]["label"] = "TechCrunch 원문"

    with verify_client({"https://news.example/": article_page()}) as client:
        report = verify_edition.check_edition(content, client)

    assert any("링크한 카드가 없다" in f for f in report.fails)
    assert any("closing.sources 에 없다" in f for f in report.fails)


def test_verify_accepts_sources_that_match_the_card_labels() -> None:
    content = {
        "cards": [verify_card(1, image=None)],
        "closing": {"sources": ["매체"]},
    }

    with verify_client({"https://news.example/": article_page()}) as client:
        report = verify_edition.check_edition(content, client)

    assert report.fails == []


def test_push_edition_refuses_when_the_link_check_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """검증은 기본으로 켜져 있다 — 죽은 링크가 있으면 POST 하지 않는다."""
    payload = reference_payload()
    payload["cards"][0]["link"]["href"] = "https://news.example/gone"
    # 레퍼런스 fixture 의 media.image 는 번들 asset stem 이라 네트워크를 안 탄다.
    edition_path = tmp_path / "edition.json"
    edition_path.write_text(json.dumps(payload), encoding="utf-8")
    env_file = tmp_path / ".env"
    env_file.write_text("ADMIN_API_KEY=secret\n", encoding="utf-8")
    monkeypatch.setattr(push_edition, "ENV_FILE", env_file)
    posted: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posted.append(str(request.url))
            return httpx.Response(200, content=request.content)
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(SystemExit) as exc:
            push_edition.main([str(edition_path)], client=client)

    assert "링크·이미지 검증 실패" in str(exc.value)
    assert posted == []


# ---- generate_qa ----


def _write_edition(tmp_path: Path, num_cards: int = 2, qa: Any = None) -> Path:
    payload = reference_payload()
    payload["cards"] = payload["cards"][:num_cards]
    if qa is not None:
        payload["cards"][0]["qa"] = qa
    edition_path = tmp_path / "edition.json"
    edition_path.write_text(json.dumps(payload), encoding="utf-8")
    return edition_path


def _write_gemini_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("GEMINI_API_KEY=secret\n", encoding="utf-8")
    monkeypatch.setattr(generate_qa, "ENV_FILE", env_file)


def test_generate_qa_main_fills_cards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    edition_path = _write_edition(tmp_path, num_cards=2)
    _write_gemini_env(tmp_path, monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-goog-api-key"] == "secret"
        return httpx.Response(200, json=make_qa_response())

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result_path = generate_qa.main(
            [str(edition_path)], client=client, sleep=lambda seconds: None
        )

    assert result_path == edition_path
    data = json.loads(edition_path.read_text(encoding="utf-8"))
    for card in data["cards"]:
        assert len(card["qa"]) == generate_qa.QUESTIONS_PER_CARD
        assert card["qa"][0] == {
            "question": "Q1",
            "answer": "A1",
            "sources": ["https://example.com/1"],
        }


def test_generate_qa_main_continues_after_per_card_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    edition_path = _write_edition(tmp_path, num_cards=2)
    _write_gemini_env(tmp_path, monkeypatch)
    failing_card_title = json.loads(edition_path.read_text(encoding="utf-8"))["cards"][0]["title"]

    def handler(request: httpx.Request) -> httpx.Response:
        # 500은 재시도 가능(retryable) 오류라 여러 번(백오프 포함) 재시도한다 — 실패 카드는
        # 매 시도 500을 받아야 재시도로도 살아나지 않는다는 걸 검증한다 (호출 횟수 기반이면
        # 재시도에 우연히 성공해버려 이 테스트가 뭘 확인하는지 흐려진다). sleep은 no-op을
        # 주입해 백오프로 인한 실제 대기를 없앤다.
        if failing_card_title in request.content.decode("utf-8"):
            return httpx.Response(500)
        return httpx.Response(200, json=make_qa_response())

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        generate_qa.main([str(edition_path)], client=client, sleep=lambda seconds: None)

    data = json.loads(edition_path.read_text(encoding="utf-8"))
    assert data["cards"][0]["qa"] is None
    assert len(data["cards"][1]["qa"]) == generate_qa.QUESTIONS_PER_CARD

    captured = capsys.readouterr()
    assert "성공 1" in captured.out
    assert "실패 1" in captured.out


def test_generate_qa_recovers_on_retry_after_transient_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    edition_path = _write_edition(tmp_path, num_cards=1)
    _write_gemini_env(tmp_path, monkeypatch)

    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(500)
        return httpx.Response(200, json=make_qa_response())

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        generate_qa.main([str(edition_path)], client=client, sleep=lambda seconds: None)

    data = json.loads(edition_path.read_text(encoding="utf-8"))
    assert len(data["cards"][0]["qa"]) == generate_qa.QUESTIONS_PER_CARD
    assert calls == 2

    captured = capsys.readouterr()
    assert "성공 1" in captured.out
    assert "실패 0" in captured.out


def test_generate_qa_strips_markdown_code_fence_around_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    edition_path = _write_edition(tmp_path, num_cards=1)
    _write_gemini_env(tmp_path, monkeypatch)

    fenced = make_qa_response()
    raw_text = fenced["steps"][0]["content"][0]["text"]
    fenced["steps"][0]["content"][0]["text"] = f"```json\n{raw_text}\n```"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=fenced)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        generate_qa.main([str(edition_path)], client=client)

    data = json.loads(edition_path.read_text(encoding="utf-8"))
    assert len(data["cards"][0]["qa"]) == generate_qa.QUESTIONS_PER_CARD


def test_generate_qa_main_skips_existing_qa_without_force(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    existing_qa = [
        {"question": "old", "answer": "old-a", "sources": ["https://old.example.com"]}
    ] * generate_qa.QUESTIONS_PER_CARD
    edition_path = _write_edition(tmp_path, num_cards=1, qa=existing_qa)
    _write_gemini_env(tmp_path, monkeypatch)

    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=make_qa_response())

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        generate_qa.main([str(edition_path)], client=client)

    assert calls == 0
    data = json.loads(edition_path.read_text(encoding="utf-8"))
    assert data["cards"][0]["qa"] == existing_qa


def test_generate_qa_main_force_regenerates_existing_qa(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    existing_qa = [
        {"question": "old", "answer": "old-a", "sources": ["https://old.example.com"]}
    ] * generate_qa.QUESTIONS_PER_CARD
    edition_path = _write_edition(tmp_path, num_cards=1, qa=existing_qa)
    _write_gemini_env(tmp_path, monkeypatch)

    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=make_qa_response())

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        generate_qa.main([str(edition_path), "--force"], client=client)

    assert calls == 1
    data = json.loads(edition_path.read_text(encoding="utf-8"))
    assert data["cards"][0]["qa"] != existing_qa
    assert data["cards"][0]["qa"][0]["question"] == "Q1"


def test_generate_qa_missing_api_key_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    edition_path = _write_edition(tmp_path, num_cards=1)
    monkeypatch.setattr(generate_qa, "ENV_FILE", tmp_path / "nonexistent.env")

    with pytest.raises(SystemExit, match="GEMINI_API_KEY"):
        generate_qa.main([str(edition_path)])


def test_generate_qa_retries_after_429_then_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    edition_path = _write_edition(tmp_path, num_cards=1)
    _write_gemini_env(tmp_path, monkeypatch)

    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429)
        return httpx.Response(200, json=make_qa_response())

    sleeps: list[float] = []

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        generate_qa.main([str(edition_path)], client=client, sleep=sleeps.append)

    data = json.loads(edition_path.read_text(encoding="utf-8"))
    assert len(data["cards"][0]["qa"]) == generate_qa.QUESTIONS_PER_CARD
    assert calls == 2
    # 백오프로 실제 대기를 걸었는지(핫루프가 아닌지) 확인한다.
    assert sleeps == [generate_qa.RETRYABLE_BACKOFF_SECONDS[0]]


def test_generate_qa_honors_retry_after_header(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    edition_path = _write_edition(tmp_path, num_cards=1)
    _write_gemini_env(tmp_path, monkeypatch)

    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return httpx.Response(200, json=make_qa_response())

    sleeps: list[float] = []

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        generate_qa.main([str(edition_path)], client=client, sleep=sleeps.append)

    data = json.loads(edition_path.read_text(encoding="utf-8"))
    assert len(data["cards"][0]["qa"]) == generate_qa.QUESTIONS_PER_CARD
    # Retry-After 헤더 값(2초)을 써야지 기본 백오프(5초)를 쓰면 안 된다.
    assert sleeps == [2.0]


def test_generate_qa_persistent_429_reports_rate_limit_in_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    edition_path = _write_edition(tmp_path, num_cards=1)
    _write_gemini_env(tmp_path, monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    sleeps: list[float] = []

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        generate_qa.main([str(edition_path)], client=client, sleep=sleeps.append)

    data = json.loads(edition_path.read_text(encoding="utf-8"))
    assert data["cards"][0]["qa"] is None
    assert sleeps == list(generate_qa.RETRYABLE_BACKOFF_SECONDS)

    captured = capsys.readouterr()
    assert "실패 1" in captured.out
    assert "레이트 리밋" in captured.out


def test_generate_qa_non_retryable_error_skips_backoff_sleep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    edition_path = _write_edition(tmp_path, num_cards=1)
    _write_gemini_env(tmp_path, monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400)

    sleeps: list[float] = []

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        generate_qa.main([str(edition_path)], client=client, sleep=sleeps.append)

    data = json.loads(edition_path.read_text(encoding="utf-8"))
    assert data["cards"][0]["qa"] is None
    assert sleeps == []

    captured = capsys.readouterr()
    assert "레이트 리밋" not in captured.out


def test_generate_qa_delay_applied_between_cards_not_before_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    edition_path = _write_edition(tmp_path, num_cards=2)
    _write_gemini_env(tmp_path, monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=make_qa_response())

    sleeps: list[float] = []

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        generate_qa.main([str(edition_path), "--delay", "7"], client=client, sleep=sleeps.append)

    # 카드 2장, 둘 다 첫 시도에 성공 — 카드 사이 대기 1번만 걸리고, 첫 카드 전에는 없다.
    assert sleeps == [7.0]


def _editions_transport(
    listing: list[dict[str, str]], bodies: dict[str, dict[str, Any]]
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/editions":
            return httpx.Response(200, json=listing)
        date = request.url.path.rsplit("/", 1)[-1]
        if date in bodies:
            return httpx.Response(200, json=bodies[date])
        return httpx.Response(404, json={"detail": "not found"})

    return httpx.MockTransport(handler)


def _edition_body(*cards: tuple[str, str, str]) -> dict[str, Any]:
    return {
        "cards": [
            {
                "num": i,
                "chip": {"text": chip, "emphasis": None},
                "title": title,
                "link": {"label": f"{outlet} 원문", "href": "https://example.com"},
            }
            for i, (chip, title, outlet) in enumerate(cards, 1)
        ]
    }


def test_recent_editions_takes_latest_days_strictly_before_the_cutoff() -> None:
    listing = [{"date": d} for d in ["2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06"]]
    bodies = {d["date"]: _edition_body(("시황", d["date"], "토큰포스트")) for d in listing}

    with httpx.Client(transport=_editions_transport(listing, bodies)) as client:
        dates = recent_editions.fetch_dates(client, "http://api", datetime.date(2026, 8, 6), days=2)

    # 커트오프 당일(08-06)은 아직 발행 전이라 제외하고, 그 이전 2일을 최신순으로.
    assert dates == ["2026-08-05", "2026-08-04"]


def test_recent_editions_output_lists_cards_and_counts_categories(
    capsys: pytest.CaptureFixture[str],
) -> None:
    listing = [{"date": "2026-08-05"}, {"date": "2026-08-06"}]
    bodies = {
        "2026-08-05": _edition_body(
            ("보안", "콜드카드는 왜 뚫렸나", "Decrypt"),
            ("시황", "6만4000달러는 되찾았다", "블록미디어"),
        ),
        "2026-08-06": _edition_body(("보안", "1500 비트코인이 남아 있었다", "TFTC")),
    }

    with httpx.Client(transport=_editions_transport(listing, bodies)) as client:
        output = recent_editions.main(
            ["--api", "http://api", "--before", "2026-08-07", "--days", "7"], client=client
        )

    assert "--- 2026-08-06 ---" in output
    assert "[보안] 콜드카드는 왜 뚫렸나  (Decrypt)" in output
    # 같은 카테고리가 이틀에 걸쳐 2장 — 이 빈도가 3.1단계 판단의 출발점이다.
    assert "보안: 2장 / 2일" in output
    assert "시황: 1장 / 1일" in output
    assert output in capsys.readouterr().out


def test_recent_editions_skips_dates_whose_body_is_missing() -> None:
    listing = [{"date": "2026-08-05"}, {"date": "2026-08-06"}]
    bodies = {"2026-08-06": _edition_body(("시황", "오늘의 시황", "토큰포스트"))}

    with httpx.Client(transport=_editions_transport(listing, bodies)) as client:
        output = recent_editions.main(
            ["--api", "http://api", "--before", "2026-08-07"], client=client
        )

    assert "2026-08-05" not in output
    assert "--- 2026-08-06 ---" in output


def test_recent_editions_fails_loudly_when_nothing_published_yet() -> None:
    with httpx.Client(transport=_editions_transport([], {})) as client:
        with pytest.raises(SystemExit, match="발행분이 없다"):
            recent_editions.main(["--api", "http://api", "--before", "2026-08-07"], client=client)


def test_build_skeleton_puts_the_cover_quote_beside_the_date_fields() -> None:
    cover_fixed = {"eyebrow": "E", "mark": ["old", "old"], "meta": ["x", "y", "old"], "hint": "h"}
    quote = {"id": "mises-boom", "text": "…", "author": "미제스", "portrait": None}

    skeleton = collect_daily.build_skeleton(
        datetime.date(2026, 8, 8), {}, "B", cover_fixed, {"sources": []}, [], quote
    )

    assert skeleton["cover"]["quote"] == quote
    # 날짜 파생 필드는 그대로여야 한다 — push_edition 의 cover 가드가 이걸 본다.
    assert skeleton["cover"]["mark"] == ["8월 8일", "양자 카드뉴스"]


def test_build_skeleton_omits_the_quote_key_when_there_is_none() -> None:
    cover_fixed = {"eyebrow": "E", "mark": ["a", "b"], "meta": ["x", "y", "z"], "hint": "h"}

    skeleton = collect_daily.build_skeleton(
        datetime.date(2026, 8, 8), {}, "B", cover_fixed, {"sources": []}, []
    )

    assert "quote" not in skeleton["cover"]


def _editions_handler(bodies: dict[str, Any]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/editions":
            return httpx.Response(200, json=[{"date": d} for d in sorted(bodies)])
        date = request.url.path.rsplit("/", 1)[-1]
        if date in bodies:
            return httpx.Response(200, json=bodies[date])
        return httpx.Response(404, json={})

    return httpx.MockTransport(handler)


def _with_quote(quote_id: str | None) -> dict[str, Any]:
    quote = {"id": quote_id, "text": "…", "author": "A"} if quote_id else None
    return {"cover": {"eyebrow": "E", "mark": [], "meta": [], "hint": "h", "quote": quote}}


def test_recent_quote_ids_collects_newest_first_and_skips_editions_without_one() -> None:
    bodies = {
        "2026-08-05": _with_quote("hayek-curious-task"),
        "2026-08-06": _with_quote(None),  # 인용구 도입 전 발행분
        "2026-08-07": _with_quote("mises-boom-collapse"),
    }

    with httpx.Client(transport=_editions_handler(bodies)) as client:
        ids = collect_daily.recent_quote_ids(
            client, "http://api", datetime.date(2026, 8, 8), limit=10
        )

    assert ids == ["mises-boom-collapse", "hayek-curious-task"]


def test_recent_quote_ids_survives_a_dead_api(capsys: pytest.CaptureFixture[str]) -> None:
    # 인용구는 표지 장식이다 — 이력 조회 실패로 06:00 배치가 죽으면 안 된다.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        ids = collect_daily.recent_quote_ids(
            client, "http://api", datetime.date(2026, 8, 8), limit=10
        )

    assert ids == []
    assert "중복 회피를 건너뛴다" in capsys.readouterr().err


def test_collect_daily_main_fills_the_cover_quote(tmp_path: Path) -> None:
    out_path = tmp_path / "draft.json"

    with httpx.Client(transport=httpx.MockTransport(_mock_handler)) as client:
        collect_daily.main(
            [
                "--date",
                "2026-07-31",
                "--out",
                str(out_path),
                "--news-url",
                "http://x/news",
                "--youtube-url",
                "http://x/queue",
                "--edition-api",
                "http://x",
            ],
            client=client,
        )

    quote = json.loads(out_path.read_text(encoding="utf-8"))["skeleton"]["cover"]["quote"]
    assert set(quote) == {"id", "text", "author", "portrait"}
    assert quote["id"] and quote["text"] and quote["author"]


def test_recent_quote_ids_survives_an_unexpected_response_shape(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # 발행 목록이 리스트가 아니라 딕셔너리로 오면 예전 코드는 TypeError 로 죽었다.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": []})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        ids = collect_daily.recent_quote_ids(
            client, "http://api", datetime.date(2026, 8, 8), limit=10
        )

    assert ids == []
    assert "중복 회피를 건너뛴다" in capsys.readouterr().err


def test_recent_quote_ids_ignores_editions_whose_body_is_not_a_dict() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/editions":
            return httpx.Response(200, json=[{"date": "2026-08-06"}, {"date": "2026-08-07"}])
        if request.url.path.endswith("2026-08-07"):
            return httpx.Response(200, json=["unexpected"])
        return httpx.Response(200, json=_with_quote("hayek-curious-task"))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        ids = collect_daily.recent_quote_ids(
            client, "http://api", datetime.date(2026, 8, 8), limit=10
        )

    assert ids == ["hayek-curious-task"]


# ---- collect_daily.recent_video_ids ----


def _video_card(link_href: str | None = None, media_image: str | None = None) -> dict[str, Any]:
    """recent_video_ids 가 읽는 카드 모양 — link.href 와 media.image 가 각각 id 출처다."""
    return {
        "link": {"href": link_href} if link_href else None,
        "media": {"image": media_image} if media_image else None,
    }


def _video_edition_body(cards: list[dict[str, Any]]) -> dict[str, Any]:
    return {"cards": cards}


def test_recent_video_ids_extracts_id_from_link_href_short_url() -> None:
    bodies = {"2026-08-07": _video_edition_body([_video_card(link_href="https://youtu.be/abc123")])}

    with httpx.Client(transport=_editions_handler(bodies)) as client:
        ids = collect_daily.recent_video_ids(
            client, "http://api", datetime.date(2026, 8, 8), days=3
        )

    assert ids == ["abc123"]


def test_recent_video_ids_extracts_id_from_link_href_watch_url() -> None:
    bodies = {
        "2026-08-07": _video_edition_body(
            [_video_card(link_href="https://www.youtube.com/watch?v=def456")]
        )
    }

    with httpx.Client(transport=_editions_handler(bodies)) as client:
        ids = collect_daily.recent_video_ids(
            client, "http://api", datetime.date(2026, 8, 8), days=3
        )

    assert ids == ["def456"]


def test_recent_video_ids_extracts_id_from_media_image_thumbnail() -> None:
    bodies = {
        "2026-08-07": _video_edition_body(
            [_video_card(media_image="https://i.ytimg.com/vi/xyz789/hqdefault.jpg")]
        )
    }

    with httpx.Client(transport=_editions_handler(bodies)) as client:
        ids = collect_daily.recent_video_ids(
            client, "http://api", datetime.date(2026, 8, 8), days=3
        )

    assert ids == ["xyz789"]


def test_recent_video_ids_deduplicates_ids_appearing_in_multiple_cards() -> None:
    bodies = {
        "2026-08-06": _video_edition_body([_video_card(link_href="https://youtu.be/dup1")]),
        "2026-08-07": _video_edition_body(
            [
                _video_card(link_href="https://youtu.be/dup1"),
                _video_card(media_image="https://i.ytimg.com/vi/dup1/hqdefault.jpg"),
            ]
        ),
    }

    with httpx.Client(transport=_editions_handler(bodies)) as client:
        ids = collect_daily.recent_video_ids(
            client, "http://api", datetime.date(2026, 8, 8), days=3
        )

    assert ids == ["dup1"]


def test_recent_video_ids_survives_null_or_non_dict_link_and_media() -> None:
    """실데이터에 media: null 인 카드가 존재한다.

    link/media 가 None 이거나 dict가 아니어도 죽지 않는다.
    """
    bodies = {
        "2026-08-07": _video_edition_body(
            [
                {"link": None, "media": None},
                {"link": "not-a-dict", "media": "not-a-dict"},
                _video_card(link_href="https://youtu.be/ok1"),
            ]
        )
    }

    with httpx.Client(transport=_editions_handler(bodies)) as client:
        ids = collect_daily.recent_video_ids(
            client, "http://api", datetime.date(2026, 8, 8), days=3
        )

    assert ids == ["ok1"]


def test_recent_video_ids_survives_a_dead_api(capsys: pytest.CaptureFixture[str]) -> None:
    # recent_quote_ids 와 같은 방어 자세 — 발행 이력 조회가 안 된다고 수집이 죽으면 안 된다.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        ids = collect_daily.recent_video_ids(
            client, "http://api", datetime.date(2026, 8, 8), days=3
        )

    assert ids == []
    assert capsys.readouterr().err != ""


# ---- collect_daily.recent_image_hashes ----


def _image_card(media_image: str | None) -> dict[str, Any]:
    return {"media": {"image": media_image} if media_image else None}


def _image_editions_handler(
    bodies: dict[str, Any], images: dict[str, bytes]
) -> httpx.MockTransport:
    """발행 이력 API + 이미지 CDN 을 한 트랜스포트로 흉내낸다."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url in images:
            return httpx.Response(200, content=images[url])
        if request.url.path == "/api/editions":
            return httpx.Response(200, json=[{"date": d} for d in sorted(bodies)])
        date = request.url.path.rsplit("/", 1)[-1]
        if date in bodies:
            return httpx.Response(200, json=bodies[date])
        return httpx.Response(404, json={})

    return httpx.MockTransport(handler)


def test_recent_image_hashes_collects_hashes_from_card_media() -> None:
    url = "https://cdn/a.png"
    bodies = {"2026-08-07": {"cards": [_image_card(url)]}}

    with httpx.Client(transport=_image_editions_handler(bodies, {url: solid_png(200)})) as client:
        digests = collect_daily.recent_image_hashes(
            client, "http://api", datetime.date(2026, 8, 8), days=3, cache={}
        )

    assert digests == [collect_daily.average_hash(solid_png(200))]


def test_recent_image_hashes_skips_youtube_thumbnails() -> None:
    """영상은 id 로 이미 중복배제된다 — 썸네일까지 이미지 풀에 넣지 않는다."""
    url = "https://i.ytimg.com/vi/abc123/hqdefault.jpg"
    bodies = {"2026-08-07": {"cards": [_image_card(url)]}}

    with httpx.Client(transport=_image_editions_handler(bodies, {url: solid_png(200)})) as client:
        digests = collect_daily.recent_image_hashes(
            client, "http://api", datetime.date(2026, 8, 8), days=3, cache={}
        )

    assert digests == []


def test_recent_image_hashes_skips_cards_without_media() -> None:
    bodies = {"2026-08-07": {"cards": [_image_card(None)]}}

    with httpx.Client(transport=_image_editions_handler(bodies, {})) as client:
        digests = collect_daily.recent_image_hashes(
            client, "http://api", datetime.date(2026, 8, 8), days=3, cache={}
        )

    assert digests == []


def test_recent_image_hashes_populates_the_cache_for_reuse() -> None:
    url = "https://cdn/a.png"
    bodies = {"2026-08-07": {"cards": [_image_card(url)]}}
    cache: dict[str, int] = {}

    with httpx.Client(transport=_image_editions_handler(bodies, {url: solid_png(200)})) as client:
        collect_daily.recent_image_hashes(
            client, "http://api", datetime.date(2026, 8, 8), days=3, cache=cache
        )

    assert cache == {url: collect_daily.average_hash(solid_png(200))}


def test_recent_image_hashes_returns_empty_when_history_is_unreachable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """발행 이력을 못 읽어도 수집을 막지 않는다 — recent_video_ids 와 같은 원칙."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        digests = collect_daily.recent_image_hashes(
            client, "http://api", datetime.date(2026, 8, 8), days=3, cache={}
        )

    assert digests == []
    assert capsys.readouterr().err != ""
