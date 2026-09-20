#!/bin/zsh
# ai-daily 무인 발행 러너. launchd(com.nsw.ai-daily)가 매일 06:00 KST 에 호출한다.
#
# 오늘자 에디션이 이미 있으면 아무것도 하지 않는다 — 맥이 자다 깨서 늦게
# 발화하거나 수동으로 한 번 돌린 뒤에 또 발화해도 같은 날짜를 덮어쓰지 않게 한다.
set -u

ROOT=/Users/nsw/meeting_room/lab/ai-daily-web
# 발행 대상. 프로덕션이다 — 로컬 :8003 백엔드는 개발용이라 여기 적으면 사이트에
# 아무것도 안 올라간다. push_edition.py 는 로컬 backend/.env 의 ADMIN_API_KEY 로
# 인증하므로 그 값이 서버 .env 의 것과 같아야 한다(다르면 401).
API=https://daily.onebitecoder.com
LOG="$ROOT/logs/daily-cron-$(date +%F).log"
mkdir -p "$ROOT/logs"

cd "$ROOT" || exit 1

# 실패는 조용히 지나가면 안 된다 — 하루치 발행이 통째로 빠져도 로그를 열어보기
# 전까지 아무도 모른다. 중단 지점마다 텔레그램으로 알린다.
NOTIFY="$HOME/.claude/scripts/launchd-notify-failure.sh"
notify_fail() {  # $1=사유  $2=종료코드
  [[ -x "$NOTIFY" ]] && "$NOTIFY" "com.nsw.ai-daily" "${2:-1}" "$1" "$LOG" >/dev/null 2>&1
}

{
  today=$(date +%F)
  echo "=== $(date '+%F %T %Z') start ($today) ==="

  # 이미 발행됐으면 재발행하지 않는다
  if curl -s -m 15 -o /dev/null -w "%{http_code}" "$API/api/editions/$today" | grep -q '^200$'; then
    echo "SKIP: $today 에디션이 이미 있다"
    exit 0
  fi

  # 소스가 죽어 있으면 시작도 하지 않는다 (더미 발행 방지)
  #
  # 한 번의 000(타임아웃) 으로 하루를 통째로 건너뛰지 않도록 20초 간격 3회 재시도한다.
  # my-youtube 서버가 간헐적으로 응답이 늦어져 멀쩡한데도 죽은 것으로 오판한 적이 있다.
  check() {  # $1=url  → 마지막 http_code 를 echo
    local code=""
    for _ in 1 2 3; do
      code=$(curl -s -m 20 -o /dev/null -w "%{http_code}" "$1")
      [[ "$code" == "200" ]] && break
      sleep 20
    done
    echo "$code"
  }

  news=$(check "http://localhost:8000/api/news?asset=ai&limit=1")
  yt=$(check "http://localhost:23456/api/queue")
  echo "source check: my-news=$news my-youtube=$yt"
  if [[ "$news" != "200" || "$yt" != "200" ]]; then
    echo "ABORT: 소스 서버 비정상 — 발행하지 않음"
    notify_fail "소스 서버 비정상 (my-news=$news my-youtube=$yt)" 1
    exit 1
  fi

  # 137(=128+9)은 외부에서 보낸 SIGKILL 이다. 2026-09-17 에 syspolicyd 가 폭주해
  # Gatekeeper 가 claude 를 악성코드로 오판정했고, 8분쯤 돌던 발행이 그대로 끊겨
  # 하루치가 빠졌다. 메모리 압박(jetsam)으로도 같은 코드가 나온다. 어느 쪽이든
  # 발행 내용의 문제가 아니라 머신 사정이므로 한 번은 다시 시도한다.
  attempt=1
  while true; do
    /Users/nsw/.local/bin/claude -p \
      "ai-daily 스킬을 사용해 오늘자(Asia/Seoul 기준) AI 카드뉴스 10장을 만들어 $API 에 발행하라. 스킬 SKILL.md의 1~7단계를 하나도 빼지 말고 순서대로 수행한다. 특히 3.1단계(최근 발행분 대비 중복 점검)는 필수다 — recent_editions.py 를 돌려 최근 7일에 무엇이 나갔는지 먼저 보고 카드를 골라라. 2~3일 안의 재등장은 새 숫자나 새 국면이 있으면 괜찮지만, 최근 7일에 4장 이상 나갔거나 4일 이상 연속 나간 토픽, 어제 카드와 사실상 같은 사건인데 새 내용이 없는 후보는 빼고 다른 후보로 채워라(시황 카드는 예외). 무엇을 왜 뺐고 무엇으로 채웠는지 마지막 보고에 적어라. 5.1단계(24시간 트렌딩 토픽 10개)도 필수다 — trending 블록 없이 발행하지 마라. trending.note 는 draft의 trending_corpus.note 를 그대로 복사하고 집계 건수를 직접 어림해서 쓰지 마라. trending_candidates 가 10개 미만이라 트렌딩을 뺐다면 그 사실과 이유를 출력에 명시하라. 후보의 cluster_titles 를 카드마다 반드시 펼쳐서 확인하라 — cluster_events 가 주제만 인접한 다른 사건을 가끔 같이 묶고(실측: 스페이스X 베라 CPU 도입에 엔비디아 베라 루빈 성능 확장이 붙었다) 표기가 갈린 같은 사건은 쪼개므로, 대표 제목만 보고 카드를 쓰면 안 된다. 코인·크립토 소재는 카드에 싣지 마라 — asset=ai 피드에도 토큰포스트·블록미디어발 코인 시황 기사가 섞여 들어온다. 다만 AI 인프라에 돈이 흐르는 기사는 코인 매체가 썼어도 AI 소재로 쳐라, 매체가 아니라 내용으로 가른다. 고유명사 표기는 CONTENT_CONTRACT.md 2.2절(app/wording.py의 BANNED_TERMS)을 따르라 — 앤스로픽 대신 앤트로픽, 할라피뇨 대신 할라페뇨, 제미니 대신 제미나이, 데이터 센터 대신 데이터센터, 챗지피티 대신 챗GPT, 오픈에이아이 대신 오픈AI, 라마3 대신 라마 3. 수집 결과가 비었거나 소스가 죽어 있으면 더미 데이터로 대체하지 말고 그 자리에서 중단하라. 사실에 없는 숫자를 지어내지 마라. 유튜브 후보의 요약은 그 자체가 틀릴 수 있으니 소스끼리 숫자가 어긋나면 웹으로 검증한 값을 써라. 썸네일 문구가 본문과 충돌하면 그 카드는 media를 null로 둬라. 발행은 push_edition.py 로 하고 링크·이미지 검증(verify_edition.py)을 절대 끄지 마라 — --skip-link-check 는 쓰지 않는다. FAIL 이 나오면 그 카드의 링크나 이미지를 사건 클러스터의 다른 매체 기사 것으로 바꿔 다시 돌려라. 발행 뒤 verify_edition.py 가 찍어주는 원문 제목 10줄을 카드 제목과 대조하고, 카드 이미지 10장의 원본 주소를 실제로 열어 기사와 맞는지 확인하라 — og:image 는 다른 기사 표지의 재탕이거나 매체가 돌려쓰는 브랜드 렌더일 수 있다. 본문에 쓴 숫자와 고유명사가 링크한 기사에 실제로 있는지도 대조하라. 없으면 그 문장을 빼라. 마지막에 발행된 날짜, 카드 10장의 제목, 트렌딩 10개의 순위와 토픽, 검증 FAIL·WARN 수와 눈으로 확인한 내용을 출력하라." \
      --model claude-sonnet-5 \
      --dangerously-skip-permissions \
      --output-format text
    # zsh에서 status 는 $? 의 예약 별칭이라 대입하면 스크립트가 그 자리에서 죽는다.
    rc=$?
    echo "=== $(date '+%F %T %Z') claude exit=$rc (시도 $attempt/2) ==="

    [[ "$rc" != "137" || "$attempt" -ge 2 ]] && break

    echo "--- SIGKILL 감지 — 120초 뒤 재시도한다 ---"
    sleep 120

    # 죽기 직전에 발행까지는 끝냈을 수 있다. 재시도 전에 확인해 같은 날짜를 두 번
    # 올리는 일을 막는다.
    if curl -s -m 15 -o /dev/null -w "%{http_code}" "$API/api/editions/$today" | grep -q '^200$'; then
      echo "재시도 생략: 다시 보니 $today 에디션이 이미 올라가 있다"
      rc=0
      break
    fi
    attempt=$((attempt + 1))
  done

  if [[ "$rc" != "0" ]]; then
    if [[ "$attempt" -ge 2 ]]; then
      notify_fail "claude 발행 실패 — SIGKILL 후 재시도까지 실패" "$rc"
    else
      notify_fail "claude 발행 실패" "$rc"
    fi
  fi

  # claude 가 0 을 반환해도 실제로 올라갔는지는 별개다.
  final=$(curl -s -m 15 -o /dev/null -w "%{http_code}" "$API/api/editions/$today")
  if [[ "$final" != "200" ]]; then
    echo "VERIFY FAIL: 발행 후에도 $today 에디션이 없다 (http=$final)"
    notify_fail "발행 후 검증 실패 — $today 에디션 없음 (http=$final)" "$rc"
  else
    echo "VERIFY OK: $today 에디션 확인"
  fi
} >> "$LOG" 2>&1
