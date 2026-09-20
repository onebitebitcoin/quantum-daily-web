"""Rank the last 24h's candidate topics by how "hot" they were — not by raw mention count.

Pure computation only, no network calls. `collect_daily.py` feeds this the same
filtered news/video candidate lists it already builds and writes the top 15 into
`trending_candidates` for a human (or Claude) to curate down to 10 with labels.

왜 "언급 수"가 아니라 "핫함"인가: 매체 하나가 같은 사건을 5번 우려먹은 것과, 매체
5곳이 각자 한 번씩 동시에 다룬 것은 언급 수로는 똑같이 5지만 화제성은 전혀 다르다.
그래서 점수 공식은 매체 다양성에 지수를 주고(diversity ** 1.5), 반복 언급의 효과는
log로 눌러 죽인다(volume). 자세한 배점 근거는 rank_topics 본문 주석 참고.
"""

import datetime
import math
import re
from typing import Any, TypedDict
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

# 거의 모든 기사/영상에 붙어 토픽으로서 변별력이 없는 태그. 모듈 상수라 필요하면
# 여기만 고치면 된다.
STOPWORDS: set[str] = {
    "ai",
    "인공지능",
    "artificialintelligence",
    "기술",
    "테크",
    "it",
    "산업",
    "시장",
    "가격",
    "투자",
    "기업",
    # 아래는 "그날의 사건"이 아니라 매일 붙는 배경 서술이다. 매체가 54곳이라
    # (2026-08-26 드라이런) 이런 태그는 다양성 점수를 그대로 얻어 실제 사건을
    # 전부 눌러버린다.
    "전망",
    "이슈",
    "시장분석",
    "미국",
    "한국",
}

# 표기만 다른 같은 토픽을 하나로 합친다. 키는 소문자 + 공백 제거로 정규화해서
# 조회하므로 "Fed"/"fed"/"FED"가 전부 같은 키로 들어온다.
SYNONYMS: dict[str, str] = {
    # 2026-08-26 드라이런에서 실제로 갈라진 것들이다. 표기만 다른 같은 회사가
    # 별도 토픽으로 잡히면(오픈AI 22 / OpenAI 7) 둘 다 순위에서 밀린다.
    "openai": "오픈AI",
    "오픈ai": "오픈AI",
    "챗gpt": "오픈AI",
    "chatgpt": "오픈AI",
    "anthropic": "앤트로픽",
    "앤스로픽": "앤트로픽",
    "앤트로픽": "앤트로픽",
    "claude": "앤트로픽",
    "클로드": "앤트로픽",
    "nvidia": "엔비디아",
    "엔비디아": "엔비디아",
    "google": "구글",
    "구글": "구글",
    "gemini": "구글",
    "제미나이": "구글",
    "deepmind": "구글",
    "딥마인드": "구글",
    "meta": "메타",
    "메타": "메타",
    "llama": "메타",
    "라마": "메타",
    "microsoft": "마이크로소프트",
    "마이크로소프트": "마이크로소프트",
    "ms": "마이크로소프트",
    "apple": "애플",
    "애플": "애플",
    "tsmc": "TSMC",
    "삼성전자": "삼성전자",
    "삼성": "삼성전자",
    "sk하이닉스": "SK하이닉스",
    "하이닉스": "SK하이닉스",
    # 같은 실물을 가리키는 갈래들. 드라이런에서 AI칩·AI반도체·반도체가 따로 세어졌다.
    "ai칩": "AI반도체",
    "ai반도체": "AI반도체",
    "반도체": "AI반도체",
    "gpu": "AI반도체",
    "hbm": "AI반도체",
    "ai인프라": "데이터센터",
    "ai데이터센터": "데이터센터",
    "데이터센터": "데이터센터",
    "ai에이전트": "AI에이전트",
    "에이전트": "AI에이전트",
    "agent": "AI에이전트",
    "llm": "LLM",
    "대규모언어모델": "LLM",
    "ai규제": "AI규제",
    "규제": "AI규제",
}

# 후보 15개를 넘겨 Claude가 겹치는 것끼리 묶어 10개로 정리할 여유를 준다.
TOP_N = 15

_HASHTAG_RE = re.compile(r"#(\S+)")


class ArticleRef(TypedDict):
    """트렌딩 항목을 펼쳤을 때 보여줄 기사/영상 하나."""

    title: str
    url: str
    source: str


class TopicSignal(TypedDict):
    """rank_topics의 출력 원소. topic 하나에 대한 집계 결과 + 사람이 라벨을 붙일 때 쓸 근거."""

    topic: str
    score: float
    heat: int
    mentions: int
    sources: int
    source_names: list[str]
    example_titles: list[str]
    articles: list[ArticleRef]


# 펼침 목록에 담을 기사 수. 시트 한 화면에 들어가고, 같은 사건을 다룬 매체가
# 몇 곳인지 눈으로 확인되는 정도면 충분하다.
MAX_ARTICLES = 6


def _normalize_tag(raw: str) -> str | None:
    """`#태그` → 정규화된 토픽명. 불용어면 None."""
    text = re.sub(r"\s+", " ", raw.lstrip("#").strip())
    if not text:
        return None
    key = text.lower().replace(" ", "")
    if key in STOPWORDS:
        return None
    return SYNONYMS.get(key, text)


def _extract_hashtags(text: str) -> list[str]:
    return _HASHTAG_RE.findall(text or "")


def _item_topics(raw_tags: list[str]) -> set[str]:
    topics: set[str] = set()
    for raw in raw_tags:
        normalized = _normalize_tag(raw)
        if normalized:
            topics.add(normalized)
    return topics


def _parse_kst(value: str | None) -> datetime.datetime | None:
    """news/video의 published_at을 파싱한다.

    tz 정보가 없는 문자열(뉴스 후보가 대개 이 형태)은 KST로 간주한다 — 수집기
    원본이 한국 매체라 이미 KST 로컬 시각이다. tz가 붙은 문자열(유튜브의
    `...Z` 등)은 그대로 존중한다.
    """
    if not value:
        return None
    dt = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    return dt


def _recency_multiplier(latest: datetime.datetime | None, now: datetime.datetime) -> float:
    if latest is None:
        return 1.0
    hours = (now - latest).total_seconds() / 3600
    if hours <= 6:
        return 1.3
    if hours <= 12:
        return 1.15
    return 1.0


class _Accumulator:
    """topic → 원시 집계치. rank_topics 안에서만 쓰는 내부 누산기."""

    def __init__(self) -> None:
        self.sources: dict[str, set[str]] = {}
        self.mentions: dict[str, int] = {}
        self.latest: dict[str, datetime.datetime] = {}
        self.view_sum: dict[str, int] = {}
        self.examples: dict[str, list[ArticleRef]] = {}

    def add(
        self,
        topic: str,
        source_name: str | None,
        published: datetime.datetime | None,
        title: str | None,
        views: int = 0,
        url: str | None = None,
    ) -> None:
        self.sources.setdefault(topic, set())
        if source_name:
            self.sources[topic].add(source_name)
        self.mentions[topic] = self.mentions.get(topic, 0) + 1
        if published is not None:
            current = self.latest.get(topic)
            if current is None or published > current:
                self.latest[topic] = published
        if views:
            self.view_sum[topic] = self.view_sum.get(topic, 0) + views
        if title:
            bucket = self.examples.setdefault(topic, [])
            # 같은 기사가 두 태그로 두 번 들어오면 목록에 중복으로 뜬다. 제목으로 막는다.
            if len(bucket) < MAX_ARTICLES and all(a["title"] != title for a in bucket):
                bucket.append({"title": title, "url": url or "", "source": source_name or ""})


def rank_topics(
    news: list[dict[str, Any]],
    videos: list[dict[str, Any]],
    now: datetime.datetime,
) -> list[TopicSignal]:
    """뉴스/영상 후보에서 토픽을 뽑아 "얼마나 핫했는지" 점수순으로 상위 15개를 낸다.

    점수 공식과 근거:
        diversity = (서로 다른 매체 수) ** 1.5
            매체 다양성에 지수를 준다 — 매체 5곳이 동시에 다뤘다는 건 한 매체가
            같은 사건을 5번 우려먹은 것보다 훨씬 강한 "진짜 화제" 신호다. 지수를
            줘서 매체 수가 늘수록 가중이 가속되게 한다(1곳→1, 2곳→2.8, 5곳→11.2).
        volume = log2(1 + 언급 수)
            반대로 언급 수 자체는 log로 눌러 죽인다. 그렇지 않으면 매체 하나가
            글을 열 번 쏟아내는 것만으로 diversity 부재를 물량으로 뒤집어버린다.
        recency = 최근성 가중 (6h 이내 1.3 / 12h 이내 1.15 / 그 외 1.0)
            오래전에 반짝했다 가라앉은 토픽보다 지금 막 터진 토픽이 더 핫하다.
        youtube = 1 + log10(1 + 조회수 합) / 10
            유튜브 반응도 신호로 더하되, 조회수는 자릿수 단위로 벌어지므로
            log10을 쓰고 나눗셈으로 완만하게 만든다 — 기사 위주 토픽이 조회수
            보정만으로 순위가 뒤집히지 않게 하는 정도로만 가중한다.
        score = diversity * volume * recency * youtube

    heat은 최고 점수를 100으로 정규화한 정수다.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=KST)

    acc = _Accumulator()

    for item in news:
        topics = _item_topics(item.get("tags") or [])
        published = _parse_kst(item.get("published_at"))
        for topic in topics:
            acc.add(
                topic,
                item.get("source_ref"),
                published,
                item.get("title"),
                url=item.get("url"),
            )

    for item in videos:
        raw_tags = [item.get("topic") or "", *_extract_hashtags(item.get("title") or "")]
        topics = _item_topics(raw_tags)
        published = _parse_kst(item.get("published_at"))
        # my-youtube 응답에 url이 없는 항목이 있어 id로 복원한다.
        video_url = item.get("url")
        if not video_url and item.get("id"):
            video_url = f"https://www.youtube.com/watch?v={item['id']}"
        for topic in topics:
            acc.add(
                topic,
                item.get("channel_title"),
                published,
                item.get("title"),
                views=item.get("view_count") or 0,
                url=video_url,
            )

    if not acc.mentions:
        return []

    scored: list[dict[str, Any]] = []
    for topic, mentions in acc.mentions.items():
        diversity = len(acc.sources[topic]) ** 1.5
        volume = math.log2(1 + mentions)
        recency = _recency_multiplier(acc.latest.get(topic), now)
        youtube = 1 + math.log10(1 + acc.view_sum.get(topic, 0)) / 10
        score = diversity * volume * recency * youtube
        scored.append(
            {
                "topic": topic,
                "score": score,
                "mentions": mentions,
                "sources": len(acc.sources[topic]),
                "source_names": sorted(acc.sources[topic]),
                "examples": acc.examples.get(topic, []),
            }
        )

    scored.sort(key=lambda s: s["score"], reverse=True)
    top = scored[:TOP_N]
    max_score = top[0]["score"] if top else 0.0

    result: list[TopicSignal] = []
    for entry in top:
        heat = round(100 * entry["score"] / max_score) if max_score > 0 else 0
        examples = entry["examples"]
        result.append(
            {
                "topic": entry["topic"],
                "score": round(entry["score"], 4),
                "heat": heat,
                "mentions": entry["mentions"],
                "sources": entry["sources"],
                "source_names": entry["source_names"],
                "example_titles": [a["title"] for a in examples[:3]],
                # url이 없는 항목은 뺀다 — 펼쳤을 때 눌리지 않는 줄이 남으면 고장으로 보인다.
                "articles": [a for a in examples if a["url"]],
            }
        )
    return result
