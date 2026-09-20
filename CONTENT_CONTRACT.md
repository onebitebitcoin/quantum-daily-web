# CONTENT_CONTRACT — 에디션 JSON 계약

`ai-daily-web`이 `POST /api/editions`로 받는 JSON의 실제 계약이다. `btc-daily-web`을
포크해 만든 프로젝트라 구조는 같고, 도메인 용어집·인용구 풀·후보 클러스터링만
다르다. 원본 카드뉴스 템플릿 문서(`reference/template-field-reference.md`)를
베이스로 하되, 이 프로젝트의 `backend/app/schemas.py`(pydantic, `extra="forbid"`)가
강제하는 실제 델타를 반영한다. **스키마와 이 문서가 다르면 스키마가 맞다** —
`backend/app/schemas.py`를 최종 소스로 본다.

## 1. 필드 표 (실제 스키마 기준)

전 모델이 `extra="forbid"`다 — 표에 없는 키를 보내면 `POST /api/editions`가
422를 반환한다. 오타 하나로 조용히 무시되는 필드는 없다.

| 블록 | 필드 | 타입 | 필수 | 비고 |
|---|---|---|---|---|
| `meta` | `title` | str | ✓ | `<title>` |
| | `slug` | str | ✓ | 출력용 식별자 |
| | `date` | str(`YYYY-MM-DD`) | ✓ | **템플릿 원본에 없던 필드 — 라우팅 키(`GET /api/editions/{date}`)로 신규 추가됨** |
| `theme` | `bg`,`bg2`,`bg_light`,`bg2_light`,`paper`,`paper2`,`ink`,`ink_dim`,`accent`,`accent_strong`,`glow`,`accent2`,`accent2_light`,`line`,`chip_bg`,`seg_off` | str | ✓ (16개 전부) | 템플릿 문서는 "4~6쌍"이라 썼지만 실제 스키마는 **16개 키 전부 필수** — 하나라도 빠지면 422 |
| `brand` | — | str | ✓ | 좌상단 라벨 |
| `cover` | `eyebrow` | str | ✓ | |
| | `mark` | list[str] | ✓ | 줄바꿈 배열 |
| | `meta` | list[str] | ✓ | 관례상 3개(수집기간/소스/날짜) |
| | `hint` | str | ✓ | |
| | `quote` | object \| null | | **선택 블록(스키마상 optional).** 표지 하단에 나가는 그날의 AI·컴퓨팅 인물 인용구. `collect_daily.py`가 매 발행 자동으로 채우니 손으로 쓰지 마라 |
| | `quote.id` | str | quote 있으면 ✓ | `ai_quotes.json`의 id. 렌더에 쓰이지 않지만 다음 날 중복 회피가 이 값을 되읽는다 |
| | `quote.text` | str | quote 있으면 ✓ | 한국어 번역. 인용문이라 [2.1절](#21-어미--body와-quote는-했습니다체)의 했습니다체 규칙에서 제외 |
| | `quote.author` | str | quote 있으면 ✓ | 예: `"앨런 튜링"` |
| | `quote.portrait` | str \| null | | 번들 초상 stem. null이면 이름을 조판한 아바타, [5.1절](#51-표지-인용구의-화자-초상-coverquoteportrait) 참고 |
| `cards` | `num` | int | ✓ | |
| | `chip.text` | str | ✓ | |
| | `chip.emphasis` | `"primary"` \| `"secondary"` \| null | | |
| | `title` | str | ✓ | |
| | `subtitle` | str | ✓ | |
| | `chips_label` | str | ✓ | |
| | `chips` | list[str] | ✓ | |
| | `body` | str | ✓ | |
| | `quote` | str \| null | | |
| | `link.label`, `link.href` | str | link 있으면 둘 다 ✓ | 없으면 `link: null` |
| | `media.image` | str | media 있으면 ✓ | stem 또는 URL, [5장](#5-이미지-규칙) 참고 |
| | `media.href`, `media.cta` | str \| null | | 없으면 `media: null` |
| `closing` | `eyebrow` | str | ✓ | |
| | `mark_lines` | list[str] | ✓ | 마지막 줄만 강조색 |
| | `links` | list[Link] | ✓ | 각 원소 `label`/`href` 모두 필수 |
| | `stamp` | str | ✓ | |
| | `restart` | str | ✓ | |
| | `sources` | list[str] | ✓ | **카드에 실제로 링크한 매체만.** 수집기는 후보 전체의 `source_ref`를 넣어 주므로(36곳까지 나온다) 카드 10장을 고른 뒤 쓴 것만 남긴다. 카드 출처를 바꾸면 이 목록도 같이 고친다 — `verify_edition.py`가 어긋나면 FAIL |
| | `disclaimer` | str \| null | | **안 쓴다(2026-08-26~).** 넣지 말 것 — 없으면 프론트가 그 자리를 비운다. 옛 발행분에 남은 값은 그대로 보여준다 |
| `trending` | — | object \| null | | **선택 블록(스키마상 optional).** 없으면 슬라이드 12장(표지+카드10+클로징), 있으면 클로징 앞에 트렌딩 슬라이드가 끼어 13장 |
| | `eyebrow` | str | trending 있으면 ✓ | |
| | `title` | str | trending 있으면 ✓ | |
| | `note` | str \| null | | 40자 이내 한 줄. draft의 `trending_corpus.note`를 그대로 복사한다 (예: `"뉴스 205건 12매체 · 유튜브 18건 17채널 집계"`) |
| | `items` | list[TrendingItem] | trending 있으면 ✓ | **정확히 10개, `rank`는 1~10 오름차순** — 아니면 422 |
| | `items[].rank` | int | ✓ | 1~10 |
| | `items[].topic` | str | ✓ | 사람이 읽을 라벨 (후보 태그를 그대로 쓰지 않고 다듬는다) |
| | `items[].heat` | int (0~100) | ✓ | 1위가 100이 되도록 정규화. 카드에서 막대 길이로 렌더 |
| | `items[].mentions` | int | ✓ | 그 토픽을 다룬 후보 기사/영상 수 |
| | `items[].sources` | int | ✓ | 서로 다른 매체 수 |
| | `items[].links` | list[TrendingLink] \| null | | 펼침 목록(새 탭 원문). 없으면 그 줄은 안 눌린다. draft의 `trending_candidates[].articles`에서 옮긴다 |
| | `items[].links[].title` | str | ✓ | 기사 원제 그대로. **카드의 `link`와 달리 label이 아니다** — 목록에서는 무슨 기사인지가 먼저 보여야 한다 |
| | `items[].links[].href` | str | ✓ | 원문 URL |
| | `items[].links[].source` | str \| null | | 매체명. 제목 아래 작게 붙는다 |

`trending`은 단순 언급 빈도가 아니라 "무엇이 진짜 핫했는가"를 보여주는 게
목적이다. `backend/app/trending.py`의 `rank_topics`가 매체 다양성에 지수를 줘
계산하고(같은 매체가 5번 쓴 것보다 매체 5곳이 한 번씩 다룬 게 더 핫하다),
`collect_daily.py`가 상위 15개를 draft의 `trending_candidates`에 남기면
`.claude/skills/ai-daily/SKILL.md` 5.1단계에서 사람(Claude)이 겹치는 토픽을
10개로 병합·라벨링해 이 블록을 만든다.

`note`에 들어갈 집계 규모는 같은 스크립트가 `trending_corpus`로 함께 남긴다.
문구는 Claude가 쓰지만 이 숫자는 세어서 옮기는 값이다 — draft에 없으면 무인
발행이 "N건 집계"를 지어내게 된다.

## 2. 톤 가이드

`frontend/src/fixtures/content.json`의 실제 카드 패턴을 따른다 —
`reference/content.json`은 원본 카드뉴스 템플릿의 스키마 테스트용 btc-daily-web
표본이라 브랜드가 다르고(`CLAUDE.md`의 "reference/ 디렉토리" 참고), 톤 기준으로는
쓰지 않는다:

- **`quote`**: 사실을 요약하지 않고 해석을 얹는 한 문장(fixture 카드 6개 평균 39자).
  ("시장 규모를 크게 그리는 것과 그 시장을 가져오는 것은 다른 일입니다.")
- **`title`**: 펀치라인형 헤드라인, 한 문장(fixture 평균 25자 안팎). **명사로
  끝낸다** — 2.1.1절 참고. ("오픈웨이트로 지키려다 뚫린 허깅페이스")
- **`subtitle`**: 영문 요약, `title`의 짧은 재진술. ("The Open-Weight Paradox")
- **`chips`**: 정확히 3개, `#`로 시작하는 키워드. ("#허깅페이스" "#오픈웨이트" "#보안")
- **`body`**: 130자 안팎(fixture 10장 평균). 무슨 일이 있었는지 → 왜 중요한지
  순서로 2~3문장.
- **`link`**: 원문 기사/영상 URL. 후보에 없으면 카드 자체를 스킵.

### 2.1. 어미 — `body`와 `quote`는 했습니다체

읽는 사람에게 말을 거는 자리라 평서체("~했다", "~이다")는 딱딱하다. **`body`와
`quote`는 `-습니다`/`-ㅂ니다`로 끝낸다.** 이미 습니다체인 `qa[].answer`와도 톤이 맞는다.

| 필드 | 어미 | 예 |
|---|---|---|
| `body` | 했습니다체 | "…엔비디아 블랙웰을 앞섰다고 밝혔습니다." / "…나스닥도 0.76% 내렸습니다." |
| `quote` | 했습니다체 | "웹의 독자가 사람에서 에이전트로 옮겨가는 속도가 숫자로 찍혔습니다." |
| `qa[].answer` | 했습니다체 | (Gemini가 이미 이렇게 생성한다) |
| `title` | **명사형 종결** | "오르막에서는 안 보이던 실적 하루 전, 7거래일 연속 밀린 엔비디아" |
| `subtitle` | 영문 | "Nvidia Slides Into Earnings" |

`title`은 헤드라인이라 어미 규칙에서 빠진다 — 습니다체로 늘이면 카드뉴스 제목
관례에서 벗어나고 줄이 길어진다.

첫 발행부터 적용된다 — `backend/app/wording.py`의 `EFFECTIVE_DATE`가
`datetime.date.min`으로 고정돼 있어 발효일 예외가 없다.

### 2.1.1. 제목은 명사로 끝낸다

실제 뉴스 헤드라인은 서술형으로 끝나지 않는다. `title`도 같은 관례를 따라
**체언(명사·명사구)으로 끝맺는다.**

| 쓰지 말 것 | 쓸 것 |
|---|---|
| "오픈AI가 브로드컴과 함께 자체 칩을 만들었다" | "엔비디아 블랙웰을 앞섰다는 오픈AI 첫 자체 칩" |
| "허깅페이스가 오픈웨이트 모델 때문에 해킹당했다" | "오픈웨이트로 지키려다 뚫린 허깅페이스" |
| "AI 에이전트가 웹의 새 독자가 됐는가" | "1년 새 1700% 늘어난 AI 에이전트 트래픽" |

- 서술형 종결(`~다`/`~한다`/`~했다`/`~이다`)과 의문형 종결(`~는가`/`~인가`/`~까`)을
  쓰지 않는다. 이 규칙은 `btc-daily-web`에서 관찰된 것이다 — 발행분 230장 중
  77%가 `~다`로 끝나 제목이 매일 같은 리듬으로 읽혔다. 명사형은 그 단조로움을
  깨는 장치이기도 하다.
- 관형절로 주어를 수식해 끝내는 형태가 기본이다("…앞선 오픈AI 첫 자체 칩",
  "…을 노린 데이터센터 해킹").
- 대구를 쓸 때는 쉼표로 끊고 뒤를 명사로 닫는다("실적 하루 전 밀린 엔비디아,
  사전학습 끝낸 차세대 모델").
- `push_edition.py`의 문구 게이트(`app/wording.py`)가 첫 발행부터 이 규칙을
  강제한다 — `TITLE_RULE_DATE`도 `EFFECTIVE_DATE`와 같은 이유로
  `datetime.date.min`이라 소급 적용을 걱정할 발행 이력 자체가 없다.

### 2.2. 표기 용어집

번역·음차가 갈리는 고유명사는 여기 적힌 쪽으로 통일한다. 후보 데이터(`summary`)의
표기가 달라도 이 표를 따른다 — 수집원마다 제각각이라 그대로 쓰면 날마다 흔들린다.
`backend/app/wording.py`의 `BANNED_TERMS`와 정확히 같은 내용이다.

| 쓰지 말 것 | 쓸 표기 | 근거 |
|---|---|---|
| 앤스로픽 | 앤트로픽 | 5 vs 45건 |
| 할라피뇨 | 할라페뇨 | 9 vs 6건 — 다수파가 아닌 쪽을 골랐다. 스페인어 jalapeño의 한국어 외래어 표기가 '할라페뇨'다 |
| 제미니 | 제미나이 | 1 vs 3건 |
| 데이터 센터 | 데이터센터 | 2 vs 64건 |
| 챗지피티 | 챗GPT | — |
| 오픈에이아이 | 오픈AI | — |
| 라마3 | 라마 3 | — |

이 목록은 지어낸 게 아니라 2026-08-26 하루치 `asset=ai` 응답 500건에서 실제로
갈린 표기다(근거 열의 숫자가 그날 등장 횟수). 새 모델·회사 이름이 두 갈래로
들어오는 걸 보면 표에 한 줄 추가하고 그날 발행분부터 적용한다.

`btc-daily-web`의 "BTC 금지" 같은 표기 금지어는 이 프로젝트에 없다 — 코인 소재
배제는 카드를 고르는 단계(`CLAUDE.md`의 "도메인 경계" 절)에서 하고, 이 표는
순수하게 같은 개체를 매일 같은 표기로 쓰기 위한 것이다.

> 새 표기 분쟁이 생기면 고친 뒤 이 표에 한 줄 추가한다. 표에 없으면 다음 발행 때
> 같은 실수가 반복된다.

## 3. 후보의 클러스터링 필드

여기서부터는 `collect_daily.py`가 만드는 draft의 뉴스 후보 항목에 붙는
필드를 다룬다 — 최종 스키마(`EditionContent`)에는 없지만, 카드 10장을 고를
때 반드시 읽어야 하므로 계약 문서에 남겨둔다.

| 필드 | 타입 | 의미 |
|---|---|---|
| `cluster_size` | int | 이 사건을 다룬 후보 기사 수(매체 수의 하한) — 클수록 화제성이 높다 |
| `also_covered_by` | list[str] | 대표 기사를 뺀 나머지 매체 이름(중복 제거) |
| `cluster_titles` | list[{title, url}] | 나머지 기사의 제목/URL. 같은 사건의 다른 각도를 보여준다 |
| `google_url` | str | googlenews 경유로 들어와 원문 주소를 되돌린 후보에만 붙는다. 원래 리디렉션 주소다 — 카드 링크로 쓰지 않는다 |

`asset=ai` 피드는 매체 54곳(googlenews 경유 포함)을 모으므로 같은 사건이 여러
매체에 동시에 실리는 일이 흔하다. `collect_daily.py`의 `collapse_events`가 이걸
사건 하나당 후보 하나로 접어 위 세 필드를 대표 기사에 붙인다.

- `cluster_size`가 크면 여러 매체가 동시에 다룬 사건이다 — 카드로 쓸 우선순위
  신호로 삼는다.
- **`cluster_titles`를 반드시 훑어라.** `cluster_events`는 주제가 인접한 다른
  사건을 가끔 같이 묶는다(2026-08-26 실측: "스페이스X 베라 CPU 도입"과 "엔비디아
  베라 루빈 성능 확장"이 6매체짜리 한 군이 됐다). 대표 제목만 보고 카드를 쓰면
  다른 사건의 매체 수를 빌려 쓰게 된다. 반대로 표기가 갈리면 같은 사건도
  쪼개진다 — 그때는 사람이 합친다.
- 대표는 "군에서 가장 앞선 것 중 이미지가 있는 것"이다. 이미지와 후보의 `link`는
  항상 같은 기사에서 나온다 — **다른 매체 이미지를 가져다 붙이지 마라.**

## 4. 발행 방법

```bash
cd backend
source .venv/bin/activate
python scripts/push_edition.py drafts/edition-<date>.json --api http://localhost:8003
# 또는 meta.date와 교차검증하며:
python scripts/push_edition.py drafts/edition-<date>.json --api http://localhost:8003 --date <date>
```

> `DEFAULT_API`는 `http://localhost:8003`(이 프로젝트 백엔드)이라 `--api`를
> 생략해도 된다. 포크 직후에는 이 값이 8002(`btc-daily-web`)였다 — 어디로
> 쏘는지 눈에 보이게 두려고 예시에는 그대로 적어 둔다.

내부 동작:
1. `app.schemas.EditionContent`로 **로컬 선검증** — 여기서 실패하면 서버에
   아무것도 보내지 않고 어떤 필드가 왜 틀렸는지 출력 후 종료.
2. `check_cover_matches_date`로 `cover.mark`/`cover.meta[2]`가 `meta.date`에서
   파생된 값인지 확인 — stale draft가 DB의 올바른 cover를 되돌리는 사고를 막는다.
   여기서 걸리면 draft를 최신 코드로 재생성해야 하며, **가드를 우회하지 않는다**.
3. `check_wording`(`app/wording.py`)으로 **문구 게이트** — 2.1(어미)·2.1.1(제목)·
   2.2(표기 용어집) 위반이면 어느 카드 어느 필드인지 찍고 POST 없이 종료.
   발효일 예외가 없다 — `EFFECTIVE_DATE`/`TITLE_RULE_DATE`가 `datetime.date.min`
   이라 첫 발행부터 전부 검사한다. 이 가드도 우회 옵션이 없다.
4. `verify_edition.py`로 **링크·이미지 검증** — 카드마다 원문과 이미지를 실제로
   두드린다. 죽은 링크, 매체 홈페이지, 구글 리디렉션, 이미지 아닌 이미지, 한
   에디션 안 이미지 중복은 여기서 FAIL 로 막힌다. 매체가 봇을 403 으로 막아
   확인만 못 한 것은 WARN 이라 발행을 막지 않는다. 이 검사만 `--skip-link-check`
   로 끌 수 있다 — 바깥 네트워크에 의존하기 때문이고, 네트워크가 없는 자리에서만
   쓴다.
5. `backend/.env`(절대경로로 탐색)에서 `ADMIN_API_KEY` 로드. 없으면 실패.
6. `POST {api}/api/editions`.

> 게이트는 표에 적힌 것만 잡는다. 오역이나 어색한 음차처럼 판단이 필요한 문제는
> 걸러내지 못하므로, 새 사례가 나오면 고친 뒤 2.2절 표에 한 줄 추가한다.

### 도메인이 정해지면

수집 소스(my-news `:8000`, my-youtube `:23456`)는 개발 머신에만 있다. 수집과
문구작성은 개발 머신에서 하고, 완성된 에디션만 프로덕션으로 POST한다:

```bash
python scripts/push_edition.py ../drafts/edition-<date>.json --api https://daily.onebitecoder.com
```

- `backend/.env`의 `ADMIN_API_KEY`로 인증한다. **서버 `.env`의 값과 같아야 한다** —
  다르면 401이고, 게이트를 다 통과한 뒤 마지막 POST에서만 드러난다.
- 같은 `meta.date`로 다시 보내면 upsert다 — 오타 수정 후 재발행이 안전하다.
- 발행 확인: `curl -s https://daily.onebitecoder.com/api/editions`
- 리허설은 `--api http://localhost:8003`으로 로컬 백엔드에 쏜다.

### 발행처와 이력 조회처는 다른 축이다

기본값이 갈려 있다. 헷갈리면 프로덕션 이력을 안 보고 어제 카드를 또 낸다.

| 스크립트 | 기본값 | 이유 |
|---|---|---|
| `push_edition.py` · `recent_editions.py`의 `DEFAULT_API` | 로컬 `:8003` | 손으로 돌릴 때 사고를 덜 내는 쪽 |
| `collect_daily.py`의 `DEFAULT_EDITION_API` | 프로덕션 | 무인 발행이 거기로 나가니 "어제 뭐가 나갔나"도 거기서 읽어야 한다 |
| `scripts/daily-cron.sh`의 `API` | 프로덕션 | 무인 발행의 실제 대상 |

세 값은 `test_edition_scripts_default_to_this_projects_backend`가 고정한다.

## 5. 이미지 규칙

`media.image`는 **stem(번들 asset 파일명)과 절대 URL 둘 다 허용**된다
(`frontend/src/imageUrl.ts`의 `cardImageSrc`가 둘 다 해석). 시드 fixture는 전부
stem(`frontend/src/assets/media/`에 실물 파일 존재). 자동 발행 파이프라인
(`scripts/collect_daily.py` → 이 계약)은 **원본 썸네일 URL을 그대로 쓴다** —
발행 시점에는 다운로드/재호스팅을 하지 않는다. 대신 프론트가 절대 URL을
`/api/img/{date}/{num}` 프록시로 돌려 WebP로 줄여 받는다(원본 평균 380KB →
약 35KB). 계약에는 원본 URL을 그대로 넣으면 된다:
- 뉴스: 후보의 `image_url` 그대로.
- 유튜브: `https://i.ytimg.com/vi/{video_id}/hqdefault.jpg`.

이 프로젝트가 실제로 마주치는 이미지 문제는 있느냐 없느냐였다. 2026-08-26 실측에서
my-news가 준 `image_url`은 최종 후보 100건 중 41건뿐이었다 — googlenews 경유 기사가
이미지 없이 들어오는 탓이다. 그래서 수집기가 최종 후보에 한해 구글 리디렉션을 매체
원문으로 되돌리고 기사 `<head>`의 `og:image`를 채운다(같은 100건 기준 41 → 99건).
`collapse_events`의 대표 승격은 그 앞단에서 한 번 더 거들 뿐이다.

남은 문제는 있느냐가 아니라 **맞느냐**다. `og:image`는 매체가 그 기사에 붙인
대표 이미지라 출처가 어긋나지는 않지만 인포그래픽이나 범용 사진일 수 있다.
아래 순서를 그대로 밟고, 끝내 아니면 `media: null`을 두려워하지 않는다.

이미지가 없는 후보는 `media: null`로 둔다 — 가짜 URL이나 플레이스홀더로
채우지 않는다. 개발자 포럼·레딧처럼 대표 이미지를 주지 않는 소스, 그리고 썸네일이
본문과 어긋나 일부러 뺀 카드까지 여기 해당한다.

`media: null`인 카드는 프론트가 이미지 자리를 비우지 않고 **카드가 이미 가진 문구를
조판해 채운다**(`CardArtFallback`). 고르는 순서는 `quote` → `subtitle` → `chip.text`
→ 카드 번호다. `quote`가 1순위인 이유는 카드 표면에 나오지 않는 유일한 필드라
중복 없이 정보가 하나 늘기 때문이다. 부제가 아트로 올라간 카드는 본문에서 같은 줄을
빼 메아리를 없앤다. 따라서 `quote`를 채워 두면 이미지 없는 카드의 완성도가 올라간다.

다만 폴백은 최후 수단이지 기본값이 아니다. 조판된 문구 카드가 여러 장 이어지면
카드뉴스가 글 모음처럼 보인다. 아래 5.0절 순서를 먼저 다 밟고, 그래도 쓸 이미지가
없을 때만 `media: null`로 간다.

### 5.0. 어떤 이미지를 고르는가 — 우선순위

`collect_daily.py`가 넘겨주는 `image_url`은 후보지 정답이 아니다.
`btc-daily-web`에서 관찰된 사례로는, 2026-08-19~23 발행 48장을 전수 점검했더니
23%가 기사와 무관한 그림이었다(채굴 카드에 테더 로고, ETF 카드에 채굴기 사진,
보안 카드에 구글 독스 아이콘) — 매체가 기사마다 이미지를 성의껏 고르지 않기
때문이다. 이 프로젝트는 아직 그 정도로 축적된 실측이 없지만, 같은 googlenews
경유 매체 생태계를 공유하므로 같은 위험이 있다고 보고 아래 순서를 그대로 쓴다.

그래서 아래 순서로 고른다. 위에서 걸리면 거기서 멈춘다.

1. **링크한 기사 본문의 실사진.** `<figcaption>`이 붙은 이미지가 실사일 확률이 높다
   (예: `캡션: 젠슨 황. 자료=CNBC 화면 갈무리`). `og:image`가 AI 생성이어도
   본문 안에는 진짜 사진이 있는 경우가 있으니 본문을 열어 확인한다.
2. **같은 기업·인물을 찍은 다른 기사의 실사진.** 카드가 다루는 대상 자체를 찍은
   사진이면 다른 기사에서 가져와도 된다(예: 오픈AI 인사 카드에 다른 기사의 샘
   알트만 청문회 사진). 링크와 이미지 출처가 갈리지만, 무관한 스톡보다 낫다.
   **같은 대상을 찍었는지가 기준이다** — 사건까지 같을 필요는 없다. 아무
   데이터센터 사진이나 붙이지 말 것.
3. **후보가 준 `image_url`.** 이제 대개 그 기사의 `og:image`다(수집기가 원문에서
   읽어 채운다) — 출처는 맞지만 인포그래픽·범용 사진일 수 있으니 눈으로 보고 판단한다.
4. **`media: null`.** 위 셋이 다 안 되면.

거르는 것:
- **재탕 표지.** 매체가 `og:image`에 다른 기사의 표지를 그대로 거는 일이 있다
  (2026-08-26 실측: 코인텔레그래프 매거진이 "AGI가 우리를 죽이지 않게 만들기"
  표지를 허깅페이스 해킹 기사에 걸었다 — 로봇이 사람을 쫓는 만화였다).
- **브랜드 렌더 돌려쓰기.** 한 매체가 회사 로고 3D 렌더 하나를 그 회사 기사마다
  건다(토큰포스트의 오픈AI 로고). 같은 그림이 카드 두 장에 들어가면 표지까지
  세 번 나온다 — `verify_edition.py`가 FAIL 로 막지만, 애초에 고르지 마라.
- **AI 생성 그림 중 글자가 깨진 것은 반드시 뺀다.** 화면·간판에 뭉개진 가짜 텍스트가
  박혀 있으면 확대했을 때 품질을 깎는다(`btc-daily-web` 실측: `SUH`·`WBTH` 같은
  오타가 그대로 노출됐다). AI 도메인 소재(오픈AI 칩·데이터센터 등)에도 AI 생성
  일러스트가 흔히 붙으므로 같은 위험이 있다.
- **범용 비즈니스 스톡**(펜 든 손, 악수, 노트북 앞 사람)은 어느 기사에도 맞지 않는다.
- **최근 발행분에 나간 그림.** `collect_daily.py`가 average hash로 거르지만
  (`IMAGE_HASH_MAX_DISTANCE`), 매체가 같은 소재를 다르게 찍은 것까지는 못 잡는다 —
  `recent_editions.py` 출력의 이미지를 눈으로도 대조한다.

### 5.1. 표지 인용구의 화자 초상 (`cover.quote.portrait`)

뉴스 이미지와 규칙이 다르다. 원격 URL을 쓰지 않고 **저장소에 번들한 파일**
(`frontend/src/assets/portraits/<stem>.jpg`)만 참조한다 — 매일 같은 몇 장이
반복되므로 프록시를 태울 이유가 없다.

퍼블릭 도메인 사진만 담는다. 공개 사이트라 CC BY-SA 사진은 저작자·라이선스
표기 줄이 따라붙어야 하는데, 그 줄이 인용구보다 길어져 표지가 지저분해진다.

`ai_quotes.json`은 현재 28개 전부 `portrait: null`이다 — 아직 퍼블릭 도메인
초상을 하나도 확보하지 못했다는 뜻이고, 그래서 표지 인용구는 지금 전원 이름을
조판한 원형 아바타로 나간다(`SpeakerAvatar`). 이미지 없는 카드를
`CardArtFallback`이 다루는 방식과 같은 태도다.

초상을 새로 추가할 때는:
- **퍼블릭 도메인 사진만 쓴다.** 위키미디어 공용 등에서 라이선스를 직접 확인한다.
- `ai_quotes.json`의 `portrait_source`(파일 페이지 URL)와 `portrait_license`를
  반드시 함께 채운다. 빈 값이면 테스트가 막는다.
- **라이선스가 애매하면 사진을 넣지 않고 아바타로 간다.**
