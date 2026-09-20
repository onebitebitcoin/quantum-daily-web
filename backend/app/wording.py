"""발행 직전 문구 게이트 — `CONTENT_CONTRACT.md` 2.1(어미)·2.1.1(제목)·2.2(용어집)를 강제한다.

같은 실수가 매일 재발하는 걸 막는 게 목적이다. 사람이 쓴 문구를 사람이 검토하면
"오늘은 통과"가 반복되지만, 표에 한 줄 적어두면 다음 발행부터 자동으로 걸린다.
(계기: 2026-08-07 발행분이 Galaxy를 "갈럭시"로 내보냈다.)

여기서 잡는 건 **표에 적힌 것뿐이다.** 오역이나 어색한 음차처럼 판단이 필요한 문제는
잡지 못한다 — 그건 리뷰 에이전트(2층)의 몫이다.

ai-daily-web 에서 갈라져 나왔다. 발효일 예외가 없는 것은 그쪽과 같고, 표의 내용이
다르다 — 저쪽은 회사·모델 이름의 음차가 갈렸고 여기는 '양자+명사' 합성어의
띄어쓰기가 갈린다.
"""

import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 런타임 임포트는 순환을 만든다 (schemas -> wording 방향이 열려 있어야 함)
    from app.schemas import EditionContent

# 이 프로젝트는 첫 발행부터 계약이 있다. 발효일 예외가 없다는 걸 코드로 남겨둔다 —
# 나중에 규칙을 추가할 때만 그 규칙의 발효일을 여기 옆에 새로 세운다.
EFFECTIVE_DATE = datetime.date.min
TITLE_RULE_DATE = datetime.date.min

# 쓰면 안 되는 표기 -> 써야 하는 표기. CONTENT_CONTRACT.md 2.2와 같은 내용을 유지한다.
#
# ai-daily-web 은 회사·모델 이름의 음차가 갈리는 게 문제였다. 양자 도메인은 **합성어
# 띄어쓰기**가 갈린다. 아래는 지어낸 목록이 아니라 2026-09-20 코퍼스에서 실제로 양쪽
# 표기가 다 쓰인 것들이고, 괄호 안이 (띄어 쓴 쪽 vs 붙여 쓴 쪽) 등장 기사 수다.
# 다수파인 붙여쓰기로 고정한다 — 같은 기사 안에서 두 표기가 섞이면 다른 개념처럼 읽힌다.
BANNED_TERMS: dict[str, str] = {
    "양자 컴퓨팅": "양자컴퓨팅",        # 59 vs 76
    "양자 컴퓨터": "양자컴퓨터",        # 40 vs 71
    "양자 암호": "양자암호",            # 13 vs 11 — 소수파지만 위 둘과 결을 맞춘다
    "양자 기술": "양자기술",            # 13 vs 13 — 동수라 위 규칙을 따라 붙인다
    "양자 내성 암호": "양자내성암호",    # 4 vs 6
    "양자 센서": "양자센서",            # 3 vs 2
    "양자 우위": "양자우위",            # 1 vs 0
    # 같은 규칙을 적용하는 나머지 합성어. 아직 코퍼스에서 갈리지 않았지만 위와
    # 형태가 같아 언제든 갈릴 자리다 — 갈린 뒤에 고치면 그날 발행분이 이미 나간다.
    "양자 역학": "양자역학",
    "양자 얽힘": "양자얽힘",
    "양자 통신": "양자통신",
    "양자 정보": "양자정보",
    "양자 광학": "양자광학",
    # 음차·영문 혼용. 한 카드 안에서 '퀀텀'과 '양자'가 섞이면 다른 회사처럼 읽힌다.
    "퀀텀 컴퓨팅": "양자컴퓨팅",
    "퀀텀컴퓨팅": "양자컴퓨팅",
    "포스트 퀀텀": "포스트퀀텀",
    "아이온Q": "아이온큐",
    "큐빗": "큐비트",
    "퀀텀비트": "큐비트",
    "퀀티늄": "퀀티넘",
}

# 했습니다체 판정. "니다로 끝나는가"만 보면 **평서체 "아니다"가 통과한다** — 아/니/다도
# "니다"로 끝나기 때문이다. 진짜 구분점은 "니다" 앞 음절의 종성이 ㅂ인지다:
#   입니다(입) · 습니다(습) · 탑니다(탑) · 아닙니다(닙) · 기댑니다(댑)  -> 종성 ㅂ
#   아니다(아)                                                        -> 종성 없음
_HANGUL_FIRST, _HANGUL_LAST = "가", "힣"
_JONGSEONG_COUNT = 28
_JONGSEONG_BIEUP = 17
_TRAILING_PUNCT = ".!?…\"'”’)"


def is_polite_ending(text: str) -> bool:
    stripped = text.strip().rstrip(_TRAILING_PUNCT)
    if len(stripped) < 3 or not stripped.endswith("니다"):
        return False
    stem = stripped[-3]
    if not (_HANGUL_FIRST <= stem <= _HANGUL_LAST):
        return False
    return (ord(stem) - ord(_HANGUL_FIRST)) % _JONGSEONG_COUNT == _JONGSEONG_BIEUP


def _check_text(where: str, text: str | None, problems: list[str]) -> None:
    """한국어로 노출되는 문구 하나를 용어집에 걸어본다."""
    if not text:
        return
    for banned, correct in BANNED_TERMS.items():
        if banned in text:
            problems.append(f"{where}: '{banned}' -> '{correct}' 로 고쳐라 ({text.strip()!r})")



def _check_ending(where: str, text: str | None, problems: list[str]) -> None:
    if text and not is_polite_ending(text):
        problems.append(f"{where}: 했습니다체가 아니다 (…{text.strip()[-16:]!r})")


# 제목의 서술형·의문형 종결. "명사로 끝났는가"를 직접 판정하려면 형태소 분석이 필요하고
# 사전에 없는 신조어·티커에서 오탐이 난다. 그래서 **아닌 것만 막는다** — 실제로 반복되는
# 사고는 "~했다/~된다/~인가"로 끝내는 것이지, 희귀한 명사가 오해받는 쪽이 아니다.
#
# 의문형은 어미 한 글자로 잡을 수 없다. "가" 하나만 보면 평가·물가·국가·전문가가 전부
# 걸리므로, 앞 음절까지 묶은 "는가/은가/인가/나요/까요"로만 판정한다.
_TITLE_BAD_ENDINGS = ("다", "까", "죠", "군", "네", "는가", "은가", "인가", "나요", "까요")
# "판다"(동물)처럼 다로 끝나는 명사를 살려두는 탈출구. 여기 적은 것으로 끝나면 통과한다.
_TITLE_NOUN_EXCEPTIONS = ("소다", "판다", "노다")


def _check_title_ending(where: str, text: str | None, problems: list[str]) -> None:
    """제목은 명사로 끝난다 (CONTENT_CONTRACT.md 2.1.1).

    실제 뉴스 헤드라인의 관례이자, 제목이 매일 같은 리듬으로 읽히는 걸 막는 장치다
    (2026-08-18 이전 230장 중 77%가 `~다`로 끝났다).
    """
    if not text:
        return
    stripped = text.strip().rstrip(_TRAILING_PUNCT)
    if not stripped or stripped.endswith(_TITLE_NOUN_EXCEPTIONS):
        return
    if stripped.endswith(_TITLE_BAD_ENDINGS):
        problems.append(
            f"{where}: 제목은 명사로 끝낸다 — 서술형·의문형 종결 금지 (…{stripped[-16:]!r})"
        )


def find_problems(content: "EditionContent") -> list[str]:
    """문구 규칙 위반 목록. 비어 있으면 통과다.

    검사 대상은 **Claude가 쓴 한국어 문구**로 한정한다. 전부가 영문인 `subtitle`,
    매체명인 `link.label`, 기사 원제를 그대로 옮기는 `trending.items[].links[].title`
    은 제외한다 — 원제를 다듬는 건 계약이 금지한 행위라 여기서 걸면 안 된다.

    `cover.quote`도 제외다. 표지 인용구는 튜링·섀넌의 말을 옮긴 번역문이라 했습니다체로
    고칠 대상이 아니다("…없습니다"라고 말한 적이 없다). 여기에 커버를 추가하려는
    시도는 `test_wording.py`가 막는다.
    """
    if content.meta.date < EFFECTIVE_DATE:
        return []

    problems: list[str] = []
    for card in content.cards:
        at = f"카드 {card.num}"
        _check_text(f"{at} title", card.title, problems)
        if content.meta.date >= TITLE_RULE_DATE:
            _check_title_ending(f"{at} title", card.title, problems)
        _check_text(f"{at} body", card.body, problems)
        _check_text(f"{at} quote", card.quote, problems)
        _check_text(f"{at} chip.text", card.chip.text, problems)
        for i, chip in enumerate(card.chips, 1):
            _check_text(f"{at} chips[{i}]", chip, problems)
        _check_ending(f"{at} body", card.body, problems)
        _check_ending(f"{at} quote", card.quote, problems)
        for i, qa in enumerate(card.qa or [], 1):
            _check_text(f"{at} qa[{i}].answer", qa.answer, problems)
            _check_ending(f"{at} qa[{i}].answer", qa.answer, problems)

    for item in (content.trending.items if content.trending else []):
        _check_text(f"트렌딩 {item.rank}위 topic", item.topic, problems)

    return problems
