"""Collect AI news(36h)/youtube(48h) candidates and write a draft skeleton.

Deterministic only — no LLM calls. Fills every field of an edition that doesn't
require judgement (meta/theme/brand/cover/closing) and dumps ranked candidates
for a human (or a Claude Code session) to pick 10 cards from.

btc-daily-web 의 같은 파일에서 갈라져 나왔다. 필터·버킷·트렌딩 점수 공식은 그대로고
도메인 용어집 세 벌(QUANTUM_TERMS / EXCLUDE_TERMS / PHYSICS_TERMS)과 영상 topic,
수집 창(주간 168h)이 다르다.
AI 쪽에만 있는 로직은 매체 간 사건 클러스터링(cluster_events)이다 — 2026-08-26
드라이런에서 오픈AI 할라페뇨 칩 한 건이 후보 100칸 중 8칸을 먹었다.

--date 를 오늘이 아닌 과거로 주면 "지금부터 36h"가 아니라 그 날짜(KST) 자정까지의
창으로 자동 전환된다(백필). 단, 소스 API 는 최신순 정렬이라 며칠 전 날짜는
기본 --news-url 의 limit=500 으로 안 닿을 수 있다 — limit 을 넉넉히 올려서 넘겨라.

Usage: python scripts/collect_daily.py [--date YYYY-MM-DD] [--out PATH]
                                        [--news-url URL] [--youtube-url URL]
       python scripts/collect_daily.py --date 2026-08-20 \
           --news-url "http://localhost:8000/api/news?asset=ai&limit=1000"
"""

import argparse
import datetime
import html
import io
import json
import re
import sys
import urllib.parse
from collections.abc import Callable, Collection
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from PIL import Image

KST = ZoneInfo("Asia/Seoul")
REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
FIXTURE_CONTENT = REPO_ROOT / "frontend" / "src" / "fixtures" / "content.json"

# 직접 실행하면 sys.path[0]이 scripts/라 app을 못 찾는다 (push_edition.py와 동일 이유).
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.quotes import as_cover_quote, is_exhausted, load_pool, pick_quote  # noqa: E402
from app.trending import rank_topics  # noqa: E402  (sys.path 조정 후여야 함)
from scripts.recent_editions import fetch_dates, fetch_edition  # noqa: E402

DEFAULT_NEWS_URL = "http://localhost:8000/api/news?asset=quantum&limit=500"
# 트렌딩 집계는 카드 후보와 목적이 다르다 — 카드는 "쓸 만한 10건"을 고르지만
# 집계는 24시간에 무슨 일이 있었는지 전부 봐야 한다. 같은 소스를 따로, 넓게 받는다.
DEFAULT_TRENDING_NEWS_URL = "http://localhost:8000/api/news?asset=quantum&limit=500"
# 산업 보강 풀. my-news 의 asset=ai 는 반도체·전력 기사를 상당수 놓친다 — 디일렉·
# 올포칩·에너지경제처럼 "AI" 를 제목에 안 쓰고 HBM·파운드리·전력망만 말하는 매체가
# 그렇다. 그래서 asset 필터 없이 한 번 더 받되 **physics 등급만** 취한다.
# btc 등급 대신 ai 등급을 버리는 것도 같은 이유다: 비트코인 기사가 대부분인 이
# 피드에서 ai 등급까지 주우면 코인 시황이 후보 상단으로 샌다.
DEFAULT_BROAD_NEWS_URL = "http://localhost:8000/api/news?limit=500"
# full=1 없으면 my-youtube 가 summary/highlights/description 을 뺀 경량 응답을 준다.
# 그러면 filter_videos 의 `summary` 조건에 전부 걸려 후보가 조용히 0건이 된다(2026-08-05).
DEFAULT_YOUTUBE_URL = "http://localhost:23456/api/queue?full=1"
# 표지 인용구·영상·이미지 중복 회피가 읽는 발행 이력. **프로덕션을 본다** —
# 무인 발행이 프로덕션으로 나가므로 이력도 거기 쌓인다. 로컬 백엔드(8003)의
# 이력은 별개라, 로컬만 보면 어제 뭐가 나갔는지 모르고 같은 카드를 또 낸다.
# 로컬 드라이런은 `--edition-api http://localhost:8003` 으로 덮어쓴다.
# (발행처 자체는 push_edition.py 의 --api 다. 이 값과 다른 축이다.)
DEFAULT_EDITION_API = "https://daily.onebitecoder.com"
# 카드 후보 뉴스 창. 트렌딩 집계 창(24h)과 다르다 — 집계는 "그날 무슨 일이
# 있었나"라서 하루로 잘라야 맞지만, 카드 후보는 고를 게 많을수록 좋다.
# 영상 창(VIDEO_WINDOW_HOURS)이 이미 48h 인 것과 같은 취지다.
# 주간 발행이라 창도 7일이다. 하루 창으로 자르면 후보가 14~17건이라 10장을 고르는
# 선택비가 1.5:1 밖에 안 된다(2026-09-20 실측). 한 주를 모으면 24~129건이 된다.
NEWS_WINDOW_HOURS = 168
# 후보 수. **클러스터링 이후** 기준이다 — cluster_events 가 같은 사건을 접은 다음
# 세므로, 여기서 100 이면 서로 다른 사건 100건을 뜻한다. 2026-08-26 드라이런에서
# 클러스터링 없이 100 을 세었더니 실제 사건은 60건 남짓이었다.
NEWS_LIMIT = 100
# 창을 7등분해 **요일별로** 고르게 뽑는다. 창만 168시간으로 늘리고 4로 두면 한
# 버킷이 42시간이라 특정 요일 기사가 몰려 들어온다.
NEWS_BUCKETS = 7
# 트렌딩 집계 창. ai-daily-web 에서는 카드(36h)와 집계(24h)가 달랐다 — 집계는
# "그날 무슨 일이 있었나"라서 하루로 잘라야 맞았기 때문이다. 주간 발행에서는
# "이번 주 무슨 일이 있었나"가 되므로 카드 창과 같은 7일을 본다.
TRENDING_WINDOW_HOURS = 168
# 산업 등급에 떼어두는 자리. 등급 순서대로만 채우면 ai 가 NEWS_LIMIT 을 그대로 다
# 먹어(2026-08-26 드라이런: 36h ai 등급 251건) 산업이 한 건도 못 올라온다 — 카드가
# 반도체·전력 각도를 쓸 수 있으려면 후보에 보이기부터 해야 한다.
PHYSICS_RESERVE = 12

# 후보의 AI 관련도 등급. 앞에 올수록 먼저 후보 자리를 가져간다.
# 카드는 AI 온리가 1순위이고, 물량이 모자라면 크립토·일반 테크 소재 대신
# 산업(반도체·전력·데이터센터·CAPEX)으로 채운다.
RELEVANCE_TIERS = ("quantum", "physics", "other")

# 관련도 판정 키워드. title 은 가중치 3, summary+tags 는 1로 센다(_relevance_score).
# ASCII 항목은 단어 경계로, 한글 항목은 부분 문자열로 맞춘다.
#
# 1순위. 큐비트·양자기업·양자암호처럼 "양자 그 자체"인 소재다.
QUANTUM_TERMS = (
    "양자컴퓨터", "양자컴퓨팅", "양자기술", "양자정보", "양자우위",
    "큐비트", "qubit", "quantum", "qpu",
    "양자얽힘", "얽힘", "entangle", "중첩", "superposition",
    "결맞음", "coherence", "결어긋남", "decoherence",
    "양자오류", "오류정정", "error correction", "error-corrected",
    "양자내성", "양자암호", "pqc", "post-quantum", "포스트퀀텀",
    "qkd", "양자키분배", "양자통신", "양자센서", "양자센싱",
    "양자광학", "양자역학", "양자물리", "양자시뮬레이션",
    # 기업·기관 고유명사. 다른 뜻으로 읽힐 여지가 없는 것만 골랐다.
    "아이온큐", "ionq", "퀀티넘", "quantinuum", "디웨이브", "d-wave",
    "리게티", "rigetti", "파스칼", "pasqal", "quera", "psiquantum",
    "퀴스킷", "qiskit", "ibm quantum", "google quantum",
    # 큐비트 구현 방식
    "초전도큐비트", "중성원자", "neutral atom", "이온트랩", "trapped ion",
    "위상큐비트", "topological qubit", "마요라나", "majorana",
)
# "양자" 단독은 넣지 않는다 — quantization 의 번역어가 '양자화'라 LLM 경량화
# 기사가 통째로 딸려 온다. 같은 이유로 "입양자"·"부양자" 도 걸린다.
#
# 배제 축. ai-daily-web 에서 크립토가 하던 역할을 여기서는 아래 셋이 한다.
# 셋 다 2026-09-20 실측으로 후보에 실제로 섞여 들어온 것이다:
#   - 'Qwen3.8 27B 양자화 벤치마크'  (quantization = LLM 경량화)
#   - '삼성, 올해 QD TV 생산 80% 줄인다'  (퀀텀닷 = 디스플레이)
#   - 'K-방산, 자주포 넘어 퀀텀점프'  (퀀텀점프 = 비유)
# 양자점(quantum dot) 자체는 빼지 않는다 — 양자점 큐비트는 진짜 양자 소재다.
# 디스플레이 제품명만 골라 막는다.
EXCLUDE_TERMS = (
    "양자화", "quantization", "quantized",
    "퀀텀점프", "퀀텀 점프", "quantum leap",
    "qd tv", "qd-oled", "qled", "퀀텀닷 tv", "퀀텀닷 티비",
    "입양자", "부양자", "수양자",
)
# 2순위. 초전도·극저온·포토닉스처럼 양자를 굴리는 물리·산업 기반 쪽이다.
# **넓은 단어를 넣지 않는 게 이 튜플의 규칙이다.** ai-daily-web 이 관세·IPO·
# 밸류에이션을 넣었다가 2순위가 증시 시황으로 찼던 전례를 그대로 따른다.
PHYSICS_TERMS = (
    "초전도", "superconduct", "극저온", "cryogenic", "희석냉동",
    "dilution refrigerator", "밀리켈빈", "millikelvin",
    "포토닉스", "photonic", "광자", "photon", "레이저", "laser",
    "스핀트로닉스", "spintronic", "응집물질", "condensed matter",
    "원자시계", "atomic clock", "냉각원자", "cold atom", "보스-아인슈타인",
    "중성미자", "neutrino", "입자가속기", "accelerator", "cern", "lhc",
    "반도체", "semiconductor", "파운드리", "foundry", "웨이퍼",
    "표준연", "kriss", "국가 양자", "national quantum", "chips act",
)


# 매체 간 사건 클러스터링. 2026-08-26 드라이런에서 오픈AI 할라페뇨 칩 발표 한 건이
# 후보 100칸 중 8칸을 먹었다 — my-news 의 `is_duplicate` 는 매체 **내부** 중복만
# 잡고 매체 간 중복은 그대로 통과시킨다. btc-daily-web 은 매체가 11곳이라 없어도
# 굴러갔지만 이쪽은 54곳이라(googlenews 경유) 없으면 카드 절반이 같은 사건이 된다.
#
# 임계값은 그날 후보 100건을 손으로 훑어 정했다. 0.30 은 "애플 AI, 구글·엔비디아
# 인프라로 방향 틀었다"를 스페이스X 기가와트 확장 건과 묶었고(오탐), 0.40 은
# 할라페뇨 한국어 6건 중 4건만 묶었다. 0.32 에서 오탐 0 · 군 9개가 나왔다.
EVENT_SIM_THRESHOLD = 0.32
# 군의 **머리**(가장 앞선 기사)와도 최소 이만큼은 겹쳐야 합류시킨다. 단일 연결만
# 쓰면 A-B 가 닮고 B-C 가 닮았다는 이유로 A 와 C 가 한 군이 된다(사슬). 실측:
# 머리 관문 없이 돌렸더니 "인도 AI 데이터센터 80억달러 투자"가 "머스크 AI 위성
# 발사"와 "스페이스X 베라 CPU"까지 끌어와 10건짜리 군이 됐다. 반대로 머리하고만
# 비교(완전 연결에 가깝게)하면 할라페뇨 7건이 5+3 으로 쪼개졌다. 두 조건을 같이
# 걸어 0.20 에서 사슬이 끊기고 같은 사건은 붙는다.
EVENT_HEAD_THRESHOLD = 0.20
# 비율만으로는 부족하다. 겹친 것이 **온전한 단어·숫자로 몇 개인지**를 따로 센다.
# 바이그램 개수로 하한을 걸었더니(3) "엔비디아" 한 단어가 엔비/비디/디아 세 개라
# 그것만으로 하한이 채워졌다 — 실측: 엔비디아를 언급하기만 한 서로 다른 사건 5건이
# (신용노출 전망 · 인도 데이터센터 주문 · 머스크 위성 · 람다 자금조달 · 쿠다-X 확장)
# 후보 1위 자리에 10건짜리 한 군으로 뭉쳤다.
#
# 같은 사건을 다룬 기사는 회사명 말고도 사건을 특정하는 말을 같이 쓴다 — 할라페뇨
# 군은 오픈AI·할라페뇨·칩·공개를, 젯슨 군은 엔비디아·젯슨·나노·엣지를 공유한다.
# 그래서 하한을 **서로 다른 단어 3개**로 옮겼다. 회사명 하나로는 절대 못 넘는다.
EVENT_MIN_ANCHORS = 3
# 서명에서 뺄 영문 기능어. 한국어는 형태소 분석 없이 문자 바이그램으로 처리하므로
# (조사가 붙어도 바이그램 상당수가 겹친다) 불용어 목록이 따로 필요 없다.
EVENT_EN_STOPWORDS = frozenset(
    {
        "the", "and", "for", "with", "how", "its", "new", "from", "that", "this",
        "are", "was", "has", "have", "after", "into", "out", "not", "but", "you",
        "your", "who", "why", "all", "can", "may", "will", "more", "than", "about",
        "over", "under", "said", "says", "amid", "top", "now", "one", "two",
    }
)
_EVENT_BRACKET = re.compile(r"\[[^\]]*\]")
_EVENT_TAIL = re.compile(r"[-–—]\s*[가-힣A-Za-z ]{2,12}$")
_EVENT_HANGUL = re.compile(r"[가-힣]{2,}")
_EVENT_LATIN = re.compile(r"[a-z]{3,}")
_EVENT_NUMBER = re.compile(r"\d+(?:[.,]\d+)?[가-힣%a-z]*")

# my-youtube 가 큐 항목에 붙이는 주제 라벨. 이 프로젝트는 이 값 하나만 본다
# (2026-08-26 기준 큐 2,290건 중 AI 1,103 / 비트코인 1,059 / 기타 117).
VIDEO_TOPIC = "양자컴퓨팅"
# 영상 후보 수.
VIDEO_LIMIT = 15
# 영상 창은 게시 시각 기준 48h. 24h 로 좁히면 my-youtube 가 요약을 늦게 끝낸 영상이
# 통째로 빠진다 — 게시 25h 뒤에 요약이 붙는 경우가 흔하다.
VIDEO_WINDOW_HOURS = 168
# 영상 일간 중복배제용 발행 이력 조회 기간. 영상 후보 창(48h) + 여유 하루.
RECENT_VIDEO_DAYS = 3

# 이미지 일간 중복배제. 카드뉴스 이미지가 며칠 간격으로 재탕되는데, 토큰포스트
# (f1.tokenpost.kr) 등 일부 매체가 같은 그림을 기사마다 새 랜덤 파일명으로
# 재업로드해 URL 대조로는 새어나간다(2026-08-19~23 발행 48장 md5 대조: 바이트
# 동일 4쌍 중 3쌍이 URL 이 서로 달랐다) — 그래서 실제로 이미지를 내려받아
# average hash 로 비교한다.
RECENT_IMAGE_DAYS = 7  # 발행 이력 조회 기간(일)
IMAGE_HASH_SIZE = 8  # average hash 그레이스케일 리사이즈 크기(8x8 = 64비트)
# 이 이하 해밍거리면 "같은 이미지"로 본다. 2026-08-23 실측으로 정했다: 발행분·후보
# 이미지 84종(3,486쌍)을 재보니 서로 다른 이미지의 최소 거리가 6이었고, 진짜 중복은
# — 바이트 동일이든 리사이즈 변형(_th_860x0, -560x305)이든 랜덤 파일명 재업로드든 —
# 전부 거리 0으로 나왔다. 그래서 0 쪽에 붙여 4로 잡는다(그 표본에서 오탐 0쌍).
# 처음에 12로 뒀더니 베이지색 서류함 일러스트와 네온 실루엣이 거리 11로 묶였다.
IMAGE_HASH_MAX_DISTANCE = 4
# 이미지 다운로드 타임아웃(초). 느린 CDN 하나가 배치를 물고 늘어지지 않게 짧게 잡는다.
IMAGE_FETCH_TIMEOUT = 10.0
IMAGE_HASH_CACHE_PATH = BACKEND_ROOT / ".cache" / "imghash" / "cache.json"


def _parse_dt(value: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))


# ---- 이미지 중복배제: average hash ----
#
# md5 같은 바이트 단위 대조는 리사이즈·재인코딩·랜덤 파일명 재업로드를 못 잡는다.
# average hash 는 이미지를 8x8 그레이스케일로 뭉갠 뒤 픽셀이 평균보다 밝은지만
# 비트로 남겨 그런 변형에 강하다 — 2026-08-19~23 발행 48장에서 해밍거리 12 이하
# 유사쌍이 6쌍 나왔다(바이트 동일 4쌍 + 리사이즈로 추정되는 2쌍).


def average_hash(image_bytes: bytes) -> int | None:
    """8x8(IMAGE_HASH_SIZE) 그레이스케일 average hash.

    디코딩 실패하면 None — 이미지 하나 때문에 호출자(배치)를 죽이지 않는다.
    """
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            small = img.convert("L").resize(
                (IMAGE_HASH_SIZE, IMAGE_HASH_SIZE), Image.Resampling.LANCZOS
            )
            pixels = list(small.getdata())
    except Exception as exc:  # Pillow 예외 유형이 다양해 넓게 잡는다
        print(f"경고: 이미지 해시 계산 실패, 건너뜀 ({exc!r})", file=sys.stderr)
        return None

    average = sum(pixels) / len(pixels)
    digest = 0
    for index, value in enumerate(pixels):
        if value > average:
            digest |= 1 << index
    return digest


def hamming_distance(a: int, b: int) -> int:
    """두 average hash 가 다른 비트 수 — 작을수록 같은 이미지에 가깝다."""
    return bin(a ^ b).count("1")


def _fetch_image_bytes(client: httpx.Client, url: str) -> bytes | None:
    """이미지를 내려받는다.

    실패(타임아웃/4xx/5xx)해도 예외를 올리지 않고 None — 이미지 하나의 네트워크
    실패로 전체 수집을 막지 않는다.
    """
    try:
        response = client.get(url, timeout=IMAGE_FETCH_TIMEOUT)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        print(f"경고: 이미지 다운로드 실패, 건너뜀 ({url}): {exc!r}", file=sys.stderr)
        return None
    return response.content


def _load_image_hash_cache() -> dict[str, int]:
    """url -> average hash 캐시. 없거나 손상됐으면 빈 캐시로 시작한다(치명적이지 않다)."""
    if not IMAGE_HASH_CACHE_PATH.exists():
        return {}
    try:
        raw = json.loads(IMAGE_HASH_CACHE_PATH.read_text(encoding="utf-8"))
        return {str(url): int(digest) for url, digest in raw.items()}
    except (OSError, json.JSONDecodeError, ValueError, AttributeError):
        return {}


def _save_image_hash_cache(cache: dict[str, int]) -> None:
    IMAGE_HASH_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        IMAGE_HASH_CACHE_PATH.write_text(
            json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as exc:
        print(f"경고: 이미지 해시 캐시 저장 실패 ({exc!r})", file=sys.stderr)


def get_image_hash(client: httpx.Client, url: str, cache: dict[str, int]) -> int | None:
    """url 이미지의 average hash. cache 에 있으면 그대로 쓰고, 없으면 내려받아 계산해

    cache 에 채운다(호출자가 들고 있는 dict를 in-place 로 채운다 — 여러 URL 을
    순회하고 마지막에 한 번만 파일로 저장하기 위해서다, 저장은 호출자 책임).
    다운로드/디코딩 실패는 캐시에 남기지 않는다 — 다음 실행에서 다시 시도되게.
    """
    if url in cache:
        return cache[url]
    image_bytes = _fetch_image_bytes(client, url)
    if image_bytes is None:
        return None
    digest = average_hash(image_bytes)
    if digest is None:
        return None
    cache[url] = digest
    return digest


# ---- 원문 URL 복원 · 대표 이미지 보강 ----
#
# 후보의 절반 가까이가 googlenews 경유로 들어온다 — url 이 news.google.com
# 리디렉션이고 image_url 은 비어 있다(2026-08-26 실측: 후보 100건 중 45건이
# 그랬고, 이미지 없는 59건 중 45건이 이쪽이다). 그대로 두면 카드의 "원문" 링크가
# 리디렉션 주소가 되고 이미지는 채울 방법이 없다. 실제로 2026-08-26 발행분은
# 카드 4장이 매체 홈페이지 링크에 기본 아트로 나갔다.
#
# 그래서 최종 후보에 한해 둘을 채운다.
#   1. 구글 뉴스 리디렉션 -> 매체 원문 URL
#   2. image_url 이 빈 후보 -> 원문 <head> 의 og:image
# 둘 다 실패하면 원래 값을 그대로 남긴다. 있으면 좋은 보강이지 06:00 배치를
# 죽일 이유가 아니다. 최종 후보(NEWS_LIMIT)에만 거는 건 원본 500건을 전부
# 두드리면 느리고 낭비라서다 — 이미지 해시와 같은 이유다.

GOOGLE_NEWS_ARTICLE = "https://news.google.com/rss/articles/"
GOOGLE_NEWS_RPC = "https://news.google.com/_/DotsSplashUi/data/batchexecute"
# 구글은 브라우저 UA 가 아니면 인터스티셜에 서명을 심어주지 않는다.
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
SOURCE_FETCH_TIMEOUT = 12.0
# 동시 요청 수. 5 면 이미지 없는 후보 59건이 실측 15초 안쪽이고 매체 한 곳에
# 몰아치지도 않는다.
SOURCE_ENRICH_WORKERS = 5
# og:image 는 <head> 에 있다. 본문까지 읽을 이유가 없다.
OG_HEAD_BYTES = 200_000
# 수집 본체는 my-news/my-youtube 만 보므로 리디렉션을 안 따라가지만, 매체 원문은
# 거의 항상 리디렉션을 탄다. 클라이언트를 따로 만들지 않고 요청 단위로 얹는다 —
# 그래야 호출자가 넘긴 클라이언트를 그대로 쓴다(테스트가 MockTransport 로 가로챈다).
_SOURCE_REQUEST: dict[str, Any] = {
    "headers": {"User-Agent": BROWSER_UA},
    "follow_redirects": True,
    "timeout": SOURCE_FETCH_TIMEOUT,
}
SOURCE_URL_CACHE_PATH = BACKEND_ROOT / ".cache" / "source-url" / "cache.json"
OG_IMAGE_CACHE_PATH = BACKEND_ROOT / ".cache" / "og-image" / "cache.json"

_GNEWS_SIGNATURE = re.compile(r'data-n-a-sg="([^"]+)"')
_GNEWS_TIMESTAMP = re.compile(r'data-n-a-ts="(\d+)"')
_OG_IMAGE_TAG = re.compile(
    r"""<meta[^>]+(?:property|name)=["'](?:og:image|twitter:image)(?::src)?["'][^>]*>""",
    re.IGNORECASE,
)
_OG_CONTENT = re.compile(r"""content=["']([^"']+)["']""", re.IGNORECASE)


def _load_str_cache(path: Path) -> dict[str, str]:
    """url -> url 캐시. 없거나 손상됐으면 빈 캐시로 시작한다(치명적이지 않다)."""
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {str(key): str(value) for key, value in raw.items()}
    except (OSError, json.JSONDecodeError, ValueError, AttributeError):
        return {}


def _save_str_cache(path: Path, cache: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        print(f"경고: 캐시 저장 실패 ({path.name}): {exc!r}", file=sys.stderr)


def _cached(cache: dict[str, str], key: str, produce: Callable[[], str | None]) -> str | None:
    """성공한 결과만 캐시에 남긴다 — 실패는 다음 실행에서 다시 시도되게."""
    if key in cache:
        return cache[key]
    value = produce()
    if value:
        cache[key] = value
    return value


def resolve_google_news_url(client: httpx.Client, url: str) -> str | None:
    """구글 뉴스 리디렉션 주소를 매체 원문 URL 로 되돌린다.

    주소 안에 원문이 인코딩돼 있지 않다 — 예전 형식은 base64 였지만 지금은
    아니다. 인터스티셜 HTML 에 심긴 서명(`data-n-a-sg`)과 타임스탬프를 구글
    내부 RPC 에 되던져야 원문이 나온다. 구글이 이 흐름을 바꾸면 여기서 None 이
    나오고 호출자는 원래 주소를 그대로 쓴다 — 링크가 리디렉션으로 남을 뿐
    수집은 계속된다.
    """
    article_id = url.split("/articles/", 1)[-1].split("?", 1)[0]
    if not article_id or article_id == url:
        return None
    try:
        page = client.get(url, **_SOURCE_REQUEST)
        page.raise_for_status()
        signature = _GNEWS_SIGNATURE.search(page.text)
        timestamp = _GNEWS_TIMESTAMP.search(page.text)
        if not (signature and timestamp):
            return None
        request = [
            "Fbv4je",
            json.dumps(
                [
                    "garturlreq",
                    [
                        ["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1]
                        + [None, None, None, None, None, 0, 1],
                        "X",
                        "X",
                        1,
                        [1, 1, 1],
                        1,
                        1,
                        None,
                        0,
                        0,
                        None,
                        0,
                    ],
                    article_id,
                    int(timestamp.group(1)),
                    signature.group(1),
                ]
            ),
        ]
        response = client.post(
            GOOGLE_NEWS_RPC,
            data={"f.req": json.dumps([[request]])},
            headers={
                "User-Agent": BROWSER_UA,
                "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            },
            timeout=SOURCE_FETCH_TIMEOUT,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        print(f"경고: 구글 뉴스 원문 복원 실패, 건너뜀 ({exc!r})", file=sys.stderr)
        return None
    return _parse_garturlres(response.text)


def _parse_garturlres(body: str) -> str | None:
    """batchexecute 응답에서 원문 URL 을 꺼낸다.

    응답은 `)]}'` 로 시작하는 줄 뒤에 JSON 이 이어지는 구글 특유의 형식이고,
    원문 URL 은 그 안에 **문자열로 한 번 더 인코딩된** JSON 안에 들어 있다.
    정규식으로 한 번에 긁으면 `=` 가 `\u003d` 로 이스케이프된 자리에서 잘린다
    (실측: aitimes.com 주소가 `?idxno` 에서 끊겼다) — 그래서 두 겹 다 파싱한다.
    """
    for line in body.splitlines():
        if "garturlres" not in line:
            continue
        try:
            envelope = json.loads(line)
        except json.JSONDecodeError:
            continue
        for row in envelope:
            if isinstance(row, list) and len(row) > 2 and row[0] == "wrb.fr":
                try:
                    payload = json.loads(row[2])
                except (json.JSONDecodeError, TypeError):
                    continue
                if len(payload) > 1 and isinstance(payload[1], str):
                    return payload[1]
    return None


def og_image_url(client: httpx.Client, url: str) -> str | None:
    """기사 <head> 의 og:image(없으면 twitter:image)를 절대 URL 로 돌려준다.

    이게 "기사 본문 실사진"에 가장 가까운 자동 수단이다 — 매체가 그 기사의
    대표 이미지로 직접 지정한 것이라, 다른 기사 사진을 빌려 오는 사고가 없다.
    """
    try:
        response = client.get(url, **_SOURCE_REQUEST)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        print(f"경고: 원문 og:image 조회 실패, 건너뜀 ({url}): {exc!r}", file=sys.stderr)
        return None
    for tag in _OG_IMAGE_TAG.findall(response.text[:OG_HEAD_BYTES]):
        found = _OG_CONTENT.search(tag)
        if found and found.group(1).strip():
            # 속성값은 HTML 이스케이프된 채로 들어온다 — `&amp;` 를 그대로 두면
            # 쿼리스트링이 깨져 이미지 서버가 다른 것을 주거나 404 를 낸다.
            raw = html.unescape(found.group(1).strip())
            return urllib.parse.urljoin(str(response.url), raw)
    return None


def enrich_candidates(
    items: list[dict[str, Any]],
    resolve_url: Callable[[str], str | None],
    fetch_image: Callable[[str], str | None],
    workers: int = SOURCE_ENRICH_WORKERS,
) -> list[dict[str, Any]]:
    """후보의 url 을 매체 원문으로 되돌리고, 이미지가 빈 후보에 og:image 를 채운다.

    되돌린 주소는 `url` 에 넣고 원래 리디렉션 주소는 `google_url` 로 남긴다.
    items 와 그 안의 dict 를 변형하지 않는다.
    """
    redirects = sum(1 for n in items if str(n.get("url") or "").startswith(GOOGLE_NEWS_ARTICLE))
    missing = sum(1 for n in items if not n.get("image_url"))

    def enrich(news: dict[str, Any]) -> dict[str, Any]:
        url = str(news.get("url") or "")
        patch: dict[str, Any] = {}
        if url.startswith(GOOGLE_NEWS_ARTICLE):
            resolved = resolve_url(url)
            if resolved:
                patch["url"] = resolved
                patch["google_url"] = url
                url = resolved
        if url and not news.get("image_url"):
            image = fetch_image(url)
            if image:
                patch["image_url"] = image
        return {**news, **patch} if patch else news

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        enriched = list(pool.map(enrich, items))

    restored = sum(1 for n in enriched if n.get("google_url"))
    filled = sum(
        1
        for before, after in zip(items, enriched, strict=True)
        if not before.get("image_url") and after.get("image_url")
    )
    print(
        f"source enrich: 리디렉션 {redirects}건 중 {restored}건 복원 · "
        f"이미지 없던 {missing}건 중 {filled}건 보강"
    )
    return enriched


def enrich_with_network(
    items: list[dict[str, Any]],
    client: httpx.Client,
    url_cache: dict[str, str],
    image_cache: dict[str, str],
) -> list[dict[str, Any]]:
    """enrich_candidates 를 실제 네트워크와 디스크 캐시에 묶는다."""
    return enrich_candidates(
        items,
        lambda url: _cached(url_cache, url, lambda: resolve_google_news_url(client, url)),
        lambda url: _cached(image_cache, url, lambda: og_image_url(client, url)),
    )


# 화제성 우선권을 줄 트렌딩 토픽 수. rank_topics 는 상위 15개를 내는데, 그 꼬리는
# 매체 한 곳이 한 번 언급한 수준이라 "여러 매체가 동시에 다뤘다"는 신호가 약하다.
TRENDING_PRIORITY_TOPICS = 8


def trending_article_urls(topics: list[dict[str, Any]]) -> list[str]:
    """상위 TRENDING_PRIORITY_TOPICS 개 토픽에 걸린 기사 url — filter_news 의 우선권 목록."""
    urls: list[str] = []
    for topic in topics[:TRENDING_PRIORITY_TOPICS]:
        for article in topic.get("articles") or []:
            url = article.get("url")
            if isinstance(url, str):
                urls.append(url)
    return list(dict.fromkeys(urls))


def _term_hits(text: str, terms: tuple[str, ...]) -> int:
    """text 에 등장한 terms 의 종류 수. 같은 단어가 여러 번 나와도 1로 센다."""
    count = 0
    for term in terms:
        if term.isascii():
            # "eth" 가 "method" 에, "gold" 가 "goldman" 에 걸리지 않게 단어 경계로 맞춘다.
            if re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text):
                count += 1
        elif term in text:
            count += 1
    return count


def _relevance_score(news: dict[str, Any], terms: tuple[str, ...]) -> int:
    """제목 가중치 3, summary+tags 가중치 1로 매긴 키워드 점수."""
    title = str(news.get("title") or "").lower()
    rest = " ".join(str(news.get(key) or "") for key in ("summary", "tags")).lower()
    return 3 * _term_hits(title, terms) + _term_hits(rest, terms)


def classify_relevance(news: dict[str, Any]) -> str:
    """기사를 RELEVANCE_TIERS 중 하나로 분류한다 — "quantum" / "physics" / "other".

    양자 점수가 배제 점수 이상이면 quantum, 아니면 물리 점수가 배제 이상일 때
    physics, 나머지는 other. 동점을 quantum·physics 쪽에 주는 건 의도한 것이다 —
    이 등급은 후보를 자르는 게이트가 아니라 **후보 자리를 누가 먼저 가져가느냐**를
    정하는 우선순위라서, 카드 10장을 고르는 사람이 한 번 더 거른다.

    양자 코퍼스는 asset=quantum 피드가 이미 한 번 걸러져 들어오므로 other 가
    거의 없다. 이 파이프라인에서 후보 품질을 실제로 좌우하는 건 등급이 아니라
    배제 축(EXCLUDE_TERMS)이다 — 양자화·퀀텀닷 TV·퀀텀점프가 제목 점수만 보면
    양자 기사처럼 보이기 때문이다.
    """
    quantum = _relevance_score(news, QUANTUM_TERMS)
    excluded = _relevance_score(news, EXCLUDE_TERMS)
    if quantum and quantum >= excluded:
        return "quantum"
    if _relevance_score(news, PHYSICS_TERMS) >= max(excluded, 1):
        return "physics"
    return "other"


def _event_signature(news: dict[str, Any]) -> frozenset[str]:
    """제목 하나를 "같은 사건인가"를 재는 서명으로 바꾼다.

    두 종류를 섞어 담는다.

    **앵커(`W:`/`N:`)** — 온전한 단어와 숫자다. 사건을 특정하는 건 이쪽이라
    `_shared_anchors` 가 이것만 센다.

    **한글 바이그램(`k:`)** — 조사가 붙거나 띄어쓰기가 달라 단어가 어긋나도
    ("자율살상" / "자율 살상") 겹치게 해주는 완충재다. 형태소 분석 없이 한국어
    표기 흔들림을 흡수하는 값싼 방법이다.

    **영문은 바이그램으로 담지 않는다.** in·er·on 같은 흔한 글자쌍 때문에 관계없는
    영문 기사들이 전부 한 덩어리가 된다(실측: 무관한 영문 9건이 한 군으로 뭉쳤다).
    한글 바이그램은 단어 안에서만 만든다 — 문장 전체를 이어 붙여 자르면 "아신"
    같은 단어 경계를 넘는 쌍이 생겨 같은 노이즈가 한글 쪽에 생긴다.

    말머리(`[미국 특징주]`)와 매체 꼬리(`- 조선비즈`)는 사건과 무관한데 여러 기사에
    공통으로 붙어 유사도를 부풀리므로 먼저 떼어낸다.
    """
    text = str(news.get("title") or "").lower()
    text = _EVENT_BRACKET.sub(" ", text)
    text = _EVENT_TAIL.sub(" ", text)
    sig: set[str] = set()
    for word in _EVENT_HANGUL.findall(text):
        sig.add(f"W:{word}")
        sig.update(f"k:{word[i : i + 2]}" for i in range(len(word) - 1))
    sig.update(f"W:{w}" for w in _EVENT_LATIN.findall(text) if w not in EVENT_EN_STOPWORDS)
    sig.update(f"N:{w}" for w in _EVENT_NUMBER.findall(text))
    return frozenset(sig)


def _shared_anchors(a: frozenset[str], b: frozenset[str]) -> int:
    """두 서명이 공유하는 **온전한 단어·숫자**의 개수. 바이그램은 세지 않는다."""
    return sum(1 for item in a & b if item[0] in "WN")


def _overlap(a: frozenset[str], b: frozenset[str]) -> float:
    """겹침 계수 — 교집합을 **작은 쪽** 크기로 나눈다."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def _same_event(a: frozenset[str], b: frozenset[str]) -> bool:
    """겹침 계수(작은 쪽 기준)와 공유 앵커 수를 둘 다 넘겨야 같은 사건으로 본다.

    자카드가 아니라 겹침 계수를 쓰는 건 제목 길이가 매체마다 크게 다르기 때문이다 —
    통신사 한 줄 제목과 종합지 두 줄 제목이 같은 사건이어도 합집합이 커서 자카드가
    낮게 나온다.
    """
    return _shared_anchors(a, b) >= EVENT_MIN_ANCHORS and _overlap(a, b) >= EVENT_SIM_THRESHOLD


def cluster_events(items: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """같은 사건을 다룬 기사끼리 묶는다. 입력 순서를 보존한다(앞선 것이 각 군의 머리).

    합류 조건이 둘이다 — 군의 **아무 기사와** _same_event 이고, 동시에 군의 **머리와**
    EVENT_HEAD_THRESHOLD 이상 겹쳐야 한다. 앞의 조건만 쓰면 사슬이 생기고, 뒤의
    조건만 쓰면 같은 사건이 쪼개진다(각 상수 주석에 실측 사례를 적어 뒀다).

    남는 한계 둘은 알고도 둔 것이다.
    1. 한국어 기사와 영문 기사가 같은 사건이어도 잘 안 묶인다 — 할라페뇨 한국어
       7건은 한 군이 됐지만 GeekNews·TechCrunch 영문판은 따로 남았다. 표기 사전
       없이는 못 넘는 선이고, 잘못 묶는 쪽이 못 묶는 쪽보다 비싸다.
    2. 주제가 인접한 다른 사건은 여전히 가끔 붙는다. 2026-08-26 실측에서는 "인도
       데이터센터 베라 루빈 9000대 주문"과 "스페이스X 베라 CPU 도입"이 한 군이 됐다 —
       둘 다 엔비디아 베라 계열 도입 건이라 앵커가 실제로 셋 이상 겹친다.
    3. 반대로 표기가 갈리면 같은 사건이 쪼개진다. 우크라이나 자율살상 드론 6건은
       "자율살상" / "자율 살상" / "살상 드론" 으로 갈려 2건씩 세 군으로 남았다.

    둘 다 대표에 cluster_titles 를 붙여 두는 이유다 — 카드를 고르는 쪽이 눈으로
    가른다. 잘못 묶는 쪽이 못 묶는 쪽보다 비싸므로 지금 균형은 미탐 쪽에 두었다.
    """
    signatures = [_event_signature(n) for n in items]
    groups: list[list[int]] = []
    for i in range(len(items)):
        for group in groups:
            if _overlap(signatures[i], signatures[group[0]]) < EVENT_HEAD_THRESHOLD:
                continue
            if any(_same_event(signatures[i], signatures[j]) for j in group):
                group.append(i)
                break
        else:
            groups.append([i])
    return [[items[i] for i in group] for group in groups]


def collapse_events(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """사건 하나당 기사 하나만 남긴다. 나머지는 대표 기사에 메타로 접어 넣는다.

    대표는 **군에서 가장 앞선(=등급·화제성이 높은) 기사 중 이미지가 있는 것**이다.
    이미지가 있는 쪽을 올리는 게 이 함수의 두 번째 일이다 — 같은 사건이니 어느
    기사를 써도 내용은 같은데, 드라이런에서 후보 100건 중 이미지가 39건뿐이라
    (googlenews 경유 기사가 image_url 없이 들어온다) 카드 10장을 못 채웠다.
    **다른 매체의 이미지를 가져다 붙이지 않고 대표 자체를 바꾸는 것**이 중요하다.
    카드는 이미지와 원문 링크를 같이 싣기 때문에, 이미지만 남의 것을 쓰면 출처가
    어긋난다. 군 전체에 이미지가 없으면 맨 앞 기사를 그대로 대표로 둔다.

    대표에 붙는 키:
      cluster_size     — 이 사건을 다룬 후보 기사 수(= 매체 수의 하한, 화제성 신호)
      also_covered_by  — 대표를 뺀 나머지 매체 이름
      cluster_titles   — 나머지 기사의 제목/URL. 같은 사건의 다른 각도를 보여준다.

    items 와 그 안의 dict 를 변형하지 않는다.
    """
    collapsed: list[dict[str, Any]] = []
    for group in cluster_events(items):
        lead = next((n for n in group if n.get("image_url")), group[0])
        others = [n for n in group if n is not lead]
        collapsed.append(
            {
                **lead,
                "cluster_size": len(group),
                "also_covered_by": list(
                    dict.fromkeys(
                        name
                        for n in others
                        if (name := n.get("source_ref") or n.get("source"))
                    )
                ),
                "cluster_titles": [
                    {"title": n.get("title"), "url": n.get("url")} for n in others
                ],
            }
        )
    return collapsed


def _round_robin_by_bucket(
    items: list[dict[str, Any]],
    now: datetime.datetime,
    limit: int,
    priority_urls: Collection[str] = (),
) -> list[dict[str, Any]]:
    """창을 NEWS_BUCKETS 구간으로 나눠 구간별 라운드로빈으로 limit 건 뽑는다.

    구간 안에서는 priority_urls 에 든 기사를 먼저, 그 다음 최신순으로 정렬한다.
    """
    if limit <= 0:
        return []
    priority = set(priority_urls)
    bucket_hours = NEWS_WINDOW_HOURS / NEWS_BUCKETS
    buckets: list[list[dict[str, Any]]] = [[] for _ in range(NEWS_BUCKETS)]
    for n in items:
        age_h = (now - _parse_dt(n["crawled_at"])).total_seconds() / 3600
        # 클록 스큐 등으로 age_h 가 음수/창 초과로 튀어도 유효 구간 안에 묶는다.
        index = min(max(int(age_h // bucket_hours), 0), NEWS_BUCKETS - 1)
        buckets[index].append(n)
    for bucket in buckets:
        bucket.sort(
            key=lambda n: (
                n.get("url") not in priority,
                # 같은 사건을 다룬 매체가 많을수록 앞. collapse_events 가 붙여 둔
                # 값이고, 안 붙어 있으면(단독 호출 등) 전부 1이라 예전과 같다.
                -n.get("cluster_size", 1),
                -_parse_dt(n["crawled_at"]).timestamp(),
            )
        )

    picked: list[dict[str, Any]] = []
    cursors = [0] * NEWS_BUCKETS
    while len(picked) < limit and any(cursors[i] < len(buckets[i]) for i in range(NEWS_BUCKETS)):
        for i in range(NEWS_BUCKETS):
            if len(picked) >= limit:
                break
            if cursors[i] < len(buckets[i]):
                picked.append(buckets[i][cursors[i]])
                cursors[i] += 1
    return picked


def physics_topups(
    items: list[dict[str, Any]],
    now: datetime.datetime,
    exclude_urls: Collection[str] = (),
) -> list[dict[str, Any]]:
    """asset 필터 없는 피드에서 **physics 등급만** 골라낸다 — 물리 보강 풀.

    ai 등급은 일부러 버린다. 이 피드는 비트코인·일반 뉴스가 대부분이라 그대로
    받으면 후보 상단이 코인 시황으로 오염된다. AI 기사는 my-news 가 이미
    asset=ai 로 걸러 주므로 여기서 또 주울 이유도 없다.

    exclude_urls 는 기본 피드에서 이미 받은 url 이다 — 두 피드가 겹치는 만큼
    중복으로 들어오는 걸 막는다.
    """
    seen = set(exclude_urls)
    cutoff = now - datetime.timedelta(hours=NEWS_WINDOW_HOURS)
    picked: list[dict[str, Any]] = []
    for news in items:
        url = news.get("url")
        if not url or url in seen or news.get("is_duplicate"):
            continue
        crawled = news.get("crawled_at")
        if not crawled or _parse_dt(crawled) < cutoff:
            continue
        if classify_relevance(news) != "physics":
            continue
        seen.add(url)
        picked.append(news)
    return picked


def filter_news(
    items: list[dict[str, Any]],
    now: datetime.datetime,
    exclude_image_hashes: Collection[int] = (),
    hash_image: Callable[[str], int | None] | None = None,
    priority_urls: Collection[str] = (),
    enrich: Callable[[list[dict[str, Any]]], list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """NEWS_WINDOW_HOURS 창을 통과한 기사를 관련도 순으로, 시간대별로 고르게 뽑는다.

    두 축이 겹쳐 있다.

    **관련도(바깥 축).** RELEVANCE_TIERS 순서대로 ai 를 먼저 채우고, 남으면
    physics, 그래도 남으면 other 로 채운다. 카드가 양자 온리를 1순위로 두고
    물량이 모자랄 때 크립토 대신 산업(반도체·전력)을 쓰기 때문이다. 다만 ai 만으로
    상한이 차버리면 물리가 후보에 아예 안 보이므로, physics 후보가 있는 만큼
    PHYSICS_RESERVE 자리까지는 quantum 몫에서 떼어 남겨둔다.

    **시간대(안쪽 축).** 각 등급 안에서는 창을 NEWS_BUCKETS 개 구간으로 나눠
    구간별 라운드로빈으로 뽑는다. 크론이 06:00 KST 에 도는 탓에 그 직전 몇 시간
    (=미국 장중)에 기사가 몰리면 그 시간대가 상위를 독차지해, 카드 후보가 하루
    24시간 중 평균 27%(최악 9%)밖에 못 덮었다(2026-08-18 진단).

    **화제성(구간 안 정렬).** priority_urls 는 보통 rank_topics 상위 토픽에 걸린
    기사들의 url 이다. 구간 안에서 이들을 최신순보다 앞에 둔다 — 등급이 같아도
    여러 매체가 동시에 다룬 사건이 먼저 후보 자리를 가져가야 지엽적인 단발 기사에
    밀리지 않는다. 안 넘기면 예전처럼 최신순이다.

    **사건(접기).** 창을 통과한 직후 collapse_events 로 같은 사건을 한 건으로
    접는다. 상한을 세기 전에 접어야 "서로 다른 사건 100건"이 된다.

    **보강(enrich).** 최종 후보가 정해진 뒤 한 번 부른다(보통 enrich_with_network).
    구글 뉴스 리디렉션을 매체 원문 URL 로 되돌리고 이미지가 빈 후보에 og:image 를
    채우는 자리다 — 이미지 중복배제보다 **먼저** 걸어야 새로 채운 이미지도 최근
    발행분과 대조된다. 안 넘기면(기본값) 보강 없이 예전과 동일하게 동작한다.

    돌려주는 각 항목에는 `relevance` 와 `cluster_size`/`also_covered_by`/
    `cluster_titles` 가 붙는다(카드 10장을 고를 때 쓴다). 보강으로 주소를 되돌린
    항목에는 `google_url` 도 붙는다.

    exclude_image_hashes(recent_image_hashes)와 hash_image(url -> average hash,
    보통 get_image_hash 를 클라이언트/캐시에 바인딩한 클로저)가 둘 다 주어지면,
    최종 선정된 후보 중 이미지가 최근 발행분과 해밍거리 IMAGE_HASH_MAX_DISTANCE
    이하로 겹치는 것의 image_url 을 None 으로 뗀다(기사 자체는 남긴다) — 같은
    draft 안에서 후보끼리 겹쳐도 마찬가지다. 최종 선정된 NEWS_LIMIT 건에만
    적용한다 — 원본 최대 500건을 전부 내려받으면 느리고 낭비다. hash_image 를
    안 넘기면(기본값) 이미지 중복배제를 건너뛴다 — 예전과 동일하게 동작한다.

    items 와 그 안의 dict 를 변형하지 않는다(relevance 가 붙은 항목도, 이미지가
    떨어져 나간 항목도 새 dict 로 돌려준다).
    """
    cutoff = now - datetime.timedelta(hours=NEWS_WINDOW_HOURS)
    fresh = [
        {**n, "relevance": classify_relevance(n)}
        for n in items
        if not n.get("is_duplicate") and _parse_dt(n["crawled_at"]) >= cutoff
    ]
    # 상한(NEWS_LIMIT)을 세기 **전에** 접는다 — 접고 세야 "서로 다른 사건 100건"이
    # 된다. 접기 전에 자르면 상위 칸을 같은 사건이 나눠 먹은 채로 잘린다.
    fresh = collapse_events(fresh)

    by_tier = {tier: [n for n in fresh if n["relevance"] == tier] for tier in RELEVANCE_TIERS}
    # quantum 이 상한을 다 먹지 않도록, 실제로 있는 만큼만 물리 자리를 떼어둔다.
    reserved = min(PHYSICS_RESERVE, len(by_tier["physics"]))

    picked: list[dict[str, Any]] = []
    for tier in RELEVANCE_TIERS:
        room = NEWS_LIMIT - len(picked)
        if tier == "quantum":
            room -= reserved
        if room <= 0:
            continue
        picked.extend(_round_robin_by_bucket(by_tier[tier], now, room, priority_urls))

    # 읽는 순서는 등급 → 매체 수 → 트렌딩 → 최신순이다. **구간 정렬과 축 순서가
    # 다르다.** 구간 정렬은 무엇이 후보에 들어올지를 정하므로 트렌딩 우선권을 앞에
    # 둬야 화제 기사가 컷에서 잘리지 않는다. 여기는 이미 뽑힌 것을 늘어놓는 자리라
    # "몇 개 매체가 같이 썼나"가 먼저 와야 한다 — 축 순서를 바꾸기 전에는 7개 매체가
    # 쓴 오픈AI 할라페뇨 발표가 단독 기사들 아래로 내려가 있었다(2026-08-26 실측).
    priority = set(priority_urls)
    picked.sort(
        key=lambda n: (
            RELEVANCE_TIERS.index(n["relevance"]),
            -n.get("cluster_size", 1),
            n.get("url") not in priority,
            -_parse_dt(n["crawled_at"]).timestamp(),
        )
    )

    if enrich is not None:
        picked = enrich(picked)

    if hash_image is not None:
        picked = _dedupe_image_urls(picked, exclude_image_hashes, hash_image)

    return picked


def _dedupe_image_urls(
    picked: list[dict[str, Any]],
    exclude_image_hashes: Collection[int],
    hash_image: Callable[[str], int | None],
) -> list[dict[str, Any]]:
    """최근 발행 이미지 또는 이 draft 안 앞선 후보와 겹치는 image_url 을 뗀다.

    같은 draft 안에서 겹치면 먼저 나온 쪽(picked 순서 기준)을 살리고 뒤에 오는
    쪽을 뗀다. hash_image 가 None 을 돌려주면(다운로드/디코딩 실패, 또는
    image_url 자체가 없음) 판정할 수 없으니 그대로 둔다.
    """
    seen_hashes: list[int] = []
    result: list[dict[str, Any]] = []
    dropped = 0
    for news in picked:
        url = news.get("image_url")
        digest = hash_image(url) if url else None
        if digest is None:
            result.append(news)
            continue
        match = _closest_image_match(digest, exclude_image_hashes, seen_hashes)
        if match is None:
            seen_hashes.append(digest)
            result.append(news)
            continue
        distance, source = match
        print(
            f"이미지 중복배제: {url} 의 image_url 을 뗀다 "
            f"({source}와 해밍거리 {distance} <= {IMAGE_HASH_MAX_DISTANCE})",
            file=sys.stderr,
        )
        result.append({**news, "image_url": None})
        dropped += 1
    if dropped:
        print(f"이미지 중복배제: {dropped}건의 image_url 을 뗐다", file=sys.stderr)
    return result


def _closest_image_match(
    digest: int, exclude_image_hashes: Collection[int], seen_hashes: list[int]
) -> tuple[int, str] | None:
    """digest 와 IMAGE_HASH_MAX_DISTANCE 이하로 가장 가까운 (해밍거리, 출처)를 찾는다."""
    best: tuple[int, str] | None = None
    for source, pool in (
        ("최근 발행 이미지", exclude_image_hashes),
        ("같은 draft 내 다른 후보", seen_hashes),
    ):
        for other in pool:
            distance = hamming_distance(digest, other)
            if distance <= IMAGE_HASH_MAX_DISTANCE and (best is None or distance < best[0]):
                best = (distance, source)
    return best


def filter_videos(
    items: list[dict[str, Any]],
    now: datetime.datetime,
    exclude_ids: Collection[str] = (),
) -> list[dict[str, Any]]:
    """Keep VIDEO_TOPIC videos, summarized, published within the window, by view_count desc.

    창을 published_at 으로 잡는 게 핵심이다. 큐 등록 시각(added_at)으로 잡으면
    my-youtube 가 과거 영상을 한꺼번에 백필한 날 몇 주 지난 영상이 "최근 24시간"으로
    딸려 들어오고, 조회수가 그만큼 누적돼 있어 상위 칸을 독차지한다.
    (2026-08-04: 6~7월 영상 5건이 8/3 게시분을 전부 밀어냄)

    published_at 이 없는 항목은 신선도를 판정할 수 없으므로 버린다.

    exclude_ids 는 최근 발행분에 이미 쓴 영상 id다(recent_video_ids). 48h 창 안에서
    조회수가 며칠째 쌓이는 인기 영상은 매일 상위권을 독차지하기 쉬운데, 그대로 두면
    같은 영상이 날짜를 넘겨 반복 게재된다 — 17개 날짜 전환 중 10회(59%)에서 전날
    영상이 재등장한 사례가 있었다(2026-08-18 진단). 기본값은 빈 컬렉션이라 호출부가
    안 넘기면 예전과 동일하게 동작한다.
    """
    excluded = set(exclude_ids)
    cutoff = now - datetime.timedelta(hours=VIDEO_WINDOW_HOURS)
    fresh = [
        v
        for v in items
        if v.get("topic") == VIDEO_TOPIC
        and v.get("summary")
        and v.get("published_at")
        and _parse_dt(v["published_at"]) >= cutoff
        and v.get("id") not in excluded
    ]
    fresh.sort(key=lambda v: v.get("view_count", 0), reverse=True)
    return fresh[:VIDEO_LIMIT]


def trending_pool_news(items: list[dict[str, Any]], now: datetime.datetime) -> list[dict[str, Any]]:
    """트렌딩 집계용 24시간 뉴스 코퍼스 — 카드 후보 필터를 쓰지 않는다.

    filter_news 를 재사용하면 안 되는 이유가 둘이다.
    1. `is_duplicate` 를 버린다. 카드에는 같은 사건을 두 번 싣지 않으려는 올바른
       필터지만, 집계에서는 **여러 매체가 같은 사건을 다뤘다는 사실 자체가 신호다.**
    2. 상위 20건으로 자른다. "가장 핫한 토픽"은 그날 전체를 봐야 나온다.
    """
    cutoff = now - datetime.timedelta(hours=TRENDING_WINDOW_HOURS)
    return [n for n in items if n.get("crawled_at") and _parse_dt(n["crawled_at"]) >= cutoff]


def trending_pool_videos(
    items: list[dict[str, Any]], now: datetime.datetime
) -> list[dict[str, Any]]:
    """트렌딩 집계용 7일 영상 코퍼스.

    filter_videos 와 달리 `summary` 를 요구하지 않는다 — 요약은 카드 문구를 쓸 때나
    필요하고, 집계에는 제목·태그·조회수면 충분하다. 요약이 아직 안 붙었다는 이유로
    그날 화제작이 통계에서 빠지면 순위가 왜곡된다. 창은 카드와 같은 7일이다 — 주간 발행이라
    "이번 주 무슨 일이 있었나"를 묻는 자리이기 때문이다.
    """
    cutoff = now - datetime.timedelta(hours=TRENDING_WINDOW_HOURS)
    return [
        v
        for v in items
        if v.get("topic") == VIDEO_TOPIC
        and v.get("published_at")
        and _parse_dt(v["published_at"]) >= cutoff
    ]


def corpus_summary(news: list[dict[str, Any]], videos: list[dict[str, Any]]) -> dict[str, Any]:
    """트렌딩 집계가 실제로 무엇을 봤는지 — 건수와 매체/채널 수.

    `note` 문자열까지 여기서 완성해 draft 에 굽는다. 발행 문구를 쓰는 주체는
    Claude 지만 **이 숫자만은 세는 것이지 쓰는 게 아니다.** draft 에 없으면 무인
    실행이 트렌딩 카드의 "N건 집계"를 지어내는 수밖에 없다(2026-08-05: 사람이
    수동으로 세어 넣었다). 세어서 넘겨주면 그대로 베끼면 된다.
    """
    outlets = {n.get("source_ref") for n in news if n.get("source_ref")}
    channels = {v.get("channel_title") for v in videos if v.get("channel_title")}
    return {
        "news": len(news),
        "outlets": len(outlets),
        "videos": len(videos),
        "channels": len(channels),
        "outlet_names": sorted(outlets),
        "channel_names": sorted(channels),
        "note": (
            f"뉴스 {len(news)}건 {len(outlets)}매체 · "
            f"유튜브 {len(videos)}건 {len(channels)}채널 집계"
        ),
    }


def warn_video_drought(items: list[dict[str, Any]]) -> None:
    """영상 후보가 0건일 때 원인을 stderr 로 구분해 알린다.

    소스 스키마가 바뀌어 summary 가 통째로 빠지면 filter_videos 가 전부 걸러내는데,
    그대로 두면 '오늘은 영상이 없었나 보다'로 읽혀 넘어간다(2026-08-05 실제 사례).
    """
    topical = [v for v in items if v.get("topic") == VIDEO_TOPIC]
    if topical and not any(v.get("summary") for v in topical):
        print(
            f"WARNING: {VIDEO_TOPIC} 영상 {len(topical)}건이 있는데 summary 가 하나도 없다 — "
            "my-youtube 응답에서 요약이 빠졌는지 확인하라(--youtube-url 에 full=1 필요).",
            file=sys.stderr,
        )
    else:
        print(
            f"WARNING: 창 안에 {VIDEO_TOPIC} 영상 후보가 없다 — 뉴스만으로 구성된다.",
            file=sys.stderr,
        )


def apply_date_to_cover(cover_fixed: dict[str, Any], date: datetime.date) -> dict[str, Any]:
    """cover.mark/meta[2]는 날짜에서 파생된다 — 이 함수가 유일한 계산처(단일 진실 공급원).

    push_edition.py 도 발행 전 이 함수의 출력과 draft 의 cover 를 비교해 드리프트를
    막는다.
    """
    cover = dict(cover_fixed)
    cover["mark"] = [f"{date.month}월 {date.day}일", "양자 카드뉴스"]
    cover["meta"] = [*cover_fixed["meta"][:2], f"{date:%Y.%m.%d}"]
    return cover


def build_skeleton(
    date: datetime.date,
    theme: dict[str, Any],
    brand: str,
    cover_fixed: dict[str, Any],
    closing_fixed: dict[str, Any],
    sources: list[str],
    cover_quote: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cover = apply_date_to_cover(cover_fixed, date)
    if cover_quote is not None:
        cover = {**cover, "quote": cover_quote}
    closing = dict(closing_fixed)
    closing["sources"] = sources
    return {
        "meta": {
            "title": f"양자 하이라이트 · {date.month}.{date.day}",
            "slug": f"quantum-daily-{date:%m%d}",
            "date": date.isoformat(),
        },
        "theme": theme,
        "brand": brand,
        "cover": cover,
        "closing": closing,
    }


def window_end(date: datetime.date, today_kst: datetime.date) -> datetime.datetime:
    """오늘이면 지금 이 순간(실시간 최근 24h), 과거 날짜면 그날 자정(KST) 기준 24h 창.

    filter_news/filter_videos 는 항상 "이 시각으로부터 각자의 창 길이만큼 전까지"만 본다 —
    과거 날짜를 백필할 때는 그 날짜가 끝나는 자정을 기준점으로 삼아야 그날 하루가
    창에 들어온다.
    """
    if date == today_kst:
        return datetime.datetime.now(datetime.UTC)
    next_midnight_kst = datetime.datetime.combine(
        date + datetime.timedelta(days=1), datetime.time(0, 0), tzinfo=KST
    )
    return next_midnight_kst.astimezone(datetime.UTC)


def fetch_json(client: httpx.Client, url: str, label: str) -> Any:
    try:
        response = client.get(url)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise SystemExit(f"{label} 소스 조회 실패 ({url}): {exc}") from exc
    return response.json()


def recent_quote_ids(client: httpx.Client, api: str, date: datetime.date, limit: int) -> list[str]:
    """이미 쓴 표지 인용구 id를 최신 발행분부터 모은다.

    **실패해도 수집을 막지 않는다.** 인용구는 표지 장식이지 그날 뉴스가 아니다 —
    발행 이력 조회가 안 된다고 06:00 배치가 통째로 죽으면 손해가 훨씬 크다. 빈
    목록을 돌려주면 `pick_quote`가 날짜 기반 로테이션으로 폴백한다(그날은 중복
    회피가 약해질 뿐 발행은 나간다).
    """
    try:
        # fetch_dates 는 응답이 에러면 SystemExit 을 낸다 — 여기서는 치명적이지 않다.
        # TypeError/KeyError 는 응답 모양이 예상과 다를 때다(프록시가 끼어들거나 API가
        # 바뀐 경우). 어느 쪽이든 인용구 하나 때문에 수집을 죽일 이유가 없다.
        dates = fetch_dates(client, api, date, limit)
    except (httpx.HTTPError, SystemExit, TypeError, KeyError) as exc:
        print(f"경고: 발행 이력을 못 읽어 인용구 중복 회피를 건너뛴다 ({exc!r})", file=sys.stderr)
        return []

    ids: list[str] = []
    for published in dates:
        try:
            edition = fetch_edition(client, api, published)
        except httpx.HTTPError:
            continue
        # 발행분마다 모양을 확인한다 — 인용구 도입 전 12편에는 cover.quote 가 없고,
        # 응답이 통째로 다른 모양일 수도 있다.
        if not isinstance(edition, dict):
            continue
        cover = edition.get("cover")
        quote = cover.get("quote") if isinstance(cover, dict) else None
        if isinstance(quote, dict) and isinstance(quote.get("id"), str):
            ids.append(quote["id"])
    return ids


def _youtube_id(url: str) -> str | None:
    """카드에 박히는 세 가지 유튜브 URL 형태에서 video id 를 뽑는다.

    발행 파이프라인이 만드는 형태는 이 셋뿐이다:
    - card.link.href  : `https://youtu.be/<id>` 또는 `https://www.youtube.com/watch?v=<id>`
    - card.media.image: `https://i.ytimg.com/vi/<id>/hqdefault.jpg`
    매치되지 않으면(형태가 바뀌었거나 유튜브 링크가 아니면) None — 조용히 건너뛴다.
    """
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return None
    host = parsed.netloc.removeprefix("www.")
    if host == "youtu.be":
        video_id = parsed.path.strip("/")
        return video_id or None
    if host in {"youtube.com", "m.youtube.com"}:
        video_id = urllib.parse.parse_qs(parsed.query).get("v", [None])[0]
        return video_id or None
    if host == "i.ytimg.com":
        parts = parsed.path.strip("/").split("/")
        if len(parts) >= 2 and parts[0] == "vi":
            return parts[1] or None
        return None
    return None


def recent_video_ids(client: httpx.Client, api: str, date: datetime.date, days: int) -> list[str]:
    """최근 `days`일 발행분에 이미 쓴 유튜브 영상 id 를 모은다 — 영상 일간 중복배제용.

    **실패해도 수집을 막지 않는다.** recent_quote_ids 와 같은 이유다 — 영상
    중복배제는 있으면 좋은 것이지, 발행 이력 조회 하나 때문에 06:00 배치가 죽으면
    손해가 훨씬 크다. 실패하면 빈 목록을 돌려주고, filter_videos 는 exclude_ids=()
    와 동일하게 동작한다(중복배제만 약해질 뿐 수집은 그대로 나간다).
    """
    try:
        # fetch_dates 는 응답이 에러면 SystemExit 을 낸다 — 여기서는 치명적이지 않다.
        dates = fetch_dates(client, api, date, days)
    except (httpx.HTTPError, SystemExit, TypeError, KeyError) as exc:
        print(f"경고: 발행 이력을 못 읽어 영상 중복 회피를 건너뛴다 ({exc!r})", file=sys.stderr)
        return []

    ids: list[str] = []
    for published in dates:
        try:
            edition = fetch_edition(client, api, published)
        except httpx.HTTPError:
            continue
        if not isinstance(edition, dict):
            continue
        for card in edition.get("cards") or []:
            if not isinstance(card, dict):
                continue
            # media: null 인 카드가 실제 데이터에 하루 0~3장 있다 — 방어적으로 접근.
            link = card.get("link")
            href = link.get("href") if isinstance(link, dict) else None
            media = card.get("media")
            image = media.get("image") if isinstance(media, dict) else None
            for url in (href, image):
                if isinstance(url, str):
                    video_id = _youtube_id(url)
                    if video_id is not None:
                        ids.append(video_id)
    return list(dict.fromkeys(ids))


def recent_image_hashes(
    client: httpx.Client, api: str, date: datetime.date, days: int, cache: dict[str, int]
) -> list[int]:
    """최근 `days`일 발행분 카드 이미지의 average hash 를 모은다 — 이미지 중복배제용.

    **실패해도 수집을 막지 않는다.** recent_video_ids 와 같은 이유다. 이미지가
    겹치는 건 아쉬운 일이지만, 발행 이력 조회나 CDN 하나가 느리다고 06:00 배치가
    죽으면 손해가 훨씬 크다. 실패하면 빈 목록을 돌려주고 filter_news 는
    exclude_image_hashes=() 와 동일하게 동작한다.

    유튜브 썸네일(i.ytimg.com)은 제외한다 — 영상 중복배제가 id 로 이미 막고 있고,
    썸네일은 그 영상의 고유 이미지라 여기서 또 걸 이유가 없다.
    """
    try:
        # fetch_dates 는 응답이 에러면 SystemExit 을 낸다 — 여기서는 치명적이지 않다.
        dates = fetch_dates(client, api, date, days)
    except (httpx.HTTPError, SystemExit, TypeError, KeyError) as exc:
        print(f"경고: 발행 이력을 못 읽어 이미지 중복 회피를 건너뛴다 ({exc!r})", file=sys.stderr)
        return []

    digests: list[int] = []
    for published in dates:
        try:
            edition = fetch_edition(client, api, published)
        except httpx.HTTPError:
            continue
        if not isinstance(edition, dict):
            continue
        for card in edition.get("cards") or []:
            if not isinstance(card, dict):
                continue
            # media: null 인 카드가 하루 0~3장 있다 — 방어적으로 접근.
            media = card.get("media")
            image = media.get("image") if isinstance(media, dict) else None
            if not isinstance(image, str) or _youtube_id(image) is not None:
                continue
            digest = get_image_hash(client, image, cache)
            if digest is not None:
                digests.append(digest)
    return digests


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="기본값: 오늘(Asia/Seoul)")
    parser.add_argument("--out", help="기본값: <repo>/drafts/draft-<date>.json")
    parser.add_argument("--news-url", default=DEFAULT_NEWS_URL)
    parser.add_argument("--trending-news-url", default=DEFAULT_TRENDING_NEWS_URL)
    parser.add_argument("--broad-news-url", default=DEFAULT_BROAD_NEWS_URL)
    parser.add_argument("--youtube-url", default=DEFAULT_YOUTUBE_URL)
    parser.add_argument(
        "--edition-api",
        default=DEFAULT_EDITION_API,
        help="표지 인용구 중복 회피용 발행 이력 조회처. 기본값: 프로덕션",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, client: httpx.Client | None = None) -> Path:
    args = parse_args(argv)
    today_kst = datetime.datetime.now(KST).date()
    date = datetime.date.fromisoformat(args.date) if args.date else today_kst
    window_end_utc = window_end(date, today_kst)
    fixture = json.loads(FIXTURE_CONTENT.read_text(encoding="utf-8"))

    owns_client = client is None
    if owns_client:
        client = httpx.Client(timeout=10.0)
    try:
        news_raw = fetch_json(client, args.news_url, "my-news")
        # 트렌딩은 같은 소스를 더 넓게 봐야 한다. 기본값은 이제 둘 다 limit=500 이라
        # 아래 분기가 자동으로 news_raw 를 재사용해 호출 1회를 절약한다 — 사용자가
        # --news-url 을 이보다 좁게 오버라이드했을 때만 따로 다시 받는다.
        trending_news_raw = (
            news_raw
            if args.trending_news_url == args.news_url
            else fetch_json(client, args.trending_news_url, "my-news(trending)")
        )
        yt_raw = fetch_json(client, args.youtube_url, "my-youtube")["items"]
        quote_pool = load_pool()
        used_quote_ids = recent_quote_ids(client, args.edition_api, date, len(quote_pool))
        used_video_ids = recent_video_ids(client, args.edition_api, date, RECENT_VIDEO_DAYS)
        image_hash_cache = _load_image_hash_cache()
        source_url_cache = _load_str_cache(SOURCE_URL_CACHE_PATH)
        og_image_cache = _load_str_cache(OG_IMAGE_CACHE_PATH)
        used_image_hashes = recent_image_hashes(
            client, args.edition_api, date, RECENT_IMAGE_DAYS, image_hash_cache
        )
        # 트렌딩 집계를 카드 후보 선별보다 먼저 돌린다 — 그날 여러 매체가 동시에
        # 다룬 사건이 무엇인지 알아야 후보 40 자리를 그쪽에 먼저 줄 수 있다.
        trending_news = trending_pool_news(trending_news_raw, window_end_utc)
        trending_videos = trending_pool_videos(yt_raw, window_end_utc)
        trending_candidates = rank_topics(trending_news, trending_videos, window_end_utc)
        trending_corpus = corpus_summary(trending_news, trending_videos)
        hot_urls = trending_article_urls(trending_candidates)
        # 후보 이미지 해시도 같은 클라이언트·캐시로 계산한다. filter_news 는 URL 만
        # 넘기므로 클로저로 묶어 둔다.
        # 산업 보강. 실패해도 수집을 막지 않는다 — 있으면 좋은 것이지, 피드 하나
        # 때문에 06:00 배치가 죽으면 손해가 훨씬 크다.
        try:
            broad_raw = fetch_json(client, args.broad_news_url, "my-news(broad)")
        except (httpx.HTTPError, SystemExit) as exc:
            print(f"경고: 산업 보강 피드를 못 읽어 건너뛴다 ({exc!r})", file=sys.stderr)
            broad_raw = []
        physics_extra = physics_topups(
            broad_raw, window_end_utc, {n.get("url") for n in news_raw if n.get("url")}
        )
        news = filter_news(
            news_raw + physics_extra,
            window_end_utc,
            used_image_hashes,
            lambda url: get_image_hash(client, url, image_hash_cache),
            hot_urls,
            lambda picked: enrich_with_network(
                picked, client, source_url_cache, og_image_cache
            ),
        )
        _save_image_hash_cache(image_hash_cache)
        _save_str_cache(SOURCE_URL_CACHE_PATH, source_url_cache)
        _save_str_cache(OG_IMAGE_CACHE_PATH, og_image_cache)
    finally:
        if owns_client:
            client.close()
    videos = filter_videos(yt_raw, window_end_utc, used_video_ids)
    for video in videos:
        video["thumbnail_url"] = f"https://i.ytimg.com/vi/{video['id']}/hqdefault.jpg"

    # 후보 0건은 "그날 영상이 없었다"일 수도, 소스 응답이 바뀐 것일 수도 있다.
    # 조용히 넘어가면 뉴스만 10장인 에디션이 그대로 나가므로 이유를 구분해 알린다.
    if not videos:
        warn_video_drought(yt_raw)

    if not news and not videos:
        raise SystemExit(
            f"{date.isoformat()} 기준 창 안에 후보가 없다 — 소스 응답이나 "
            "--news-url limit(과거 날짜는 500건으로 부족할 수 있다)을 확인하라."
        )

    sources = list(dict.fromkeys(n["source_ref"] for n in news))
    if is_exhausted(quote_pool, used_quote_ids):
        print(
            "경고: 표지 인용구 풀을 한 바퀴 다 돌았다 — 가장 오래전에 쓴 것부터 "
            f"재사용한다 (풀 {len(quote_pool)}개). quantum_quotes.json 을 늘려라.",
            file=sys.stderr,
        )
    quote = pick_quote(quote_pool, used_quote_ids, date)
    skeleton = build_skeleton(
        date,
        fixture["theme"],
        fixture["brand"],
        fixture["cover"],
        fixture["closing"],
        sources,
        as_cover_quote(quote),
    )
    # 집계는 카드 후보(NEWS_LIMIT · VIDEO_LIMIT)가 아니라 24시간 코퍼스 전체를 본다.

    out_path = (
        Path(args.out) if args.out else REPO_ROOT / "drafts" / f"draft-{date.isoformat()}.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "skeleton": skeleton,
                "candidates": {"news": news, "videos": videos},
                "trending_candidates": trending_candidates,
                "trending_corpus": trending_corpus,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    tier_counts = {t: sum(1 for n in news if n.get("relevance") == t) for t in RELEVANCE_TIERS}
    print(
        f"news candidates: {len(news)} — "
        + " / ".join(f"{tier} {count}" for tier, count in tier_counts.items())
    )
    folded = sum(n.get("cluster_size", 1) - 1 for n in news)
    with_image = sum(1 for n in news if n.get("image_url"))
    print(
        f"event folding: {folded}건을 접어 사건 {len(news)}개 — "
        f"가장 큰 사건 {max((n.get('cluster_size', 1) for n in news), default=0)}매체"
    )
    print(f"image coverage: {with_image}/{len(news)} ({with_image * 100 // max(len(news), 1)}%)")
    print(
        f"video candidates: {len(videos)} — 최근 {RECENT_VIDEO_DAYS}일 발행분 "
        f"{len(used_video_ids)}건 제외"
    )
    print(
        f"trending pool: news {len(trending_news)} / videos {len(trending_videos)}"
        f" -> {len(trending_candidates)} topics"
    )
    print(f"trending corpus: {trending_corpus['note']}")
    print(f"cover quote: {quote.id} ({quote.author}) — 최근 {len(used_quote_ids)}개 제외")
    print(f"wrote {out_path}")
    return out_path


if __name__ == "__main__":
    main()
