#!/bin/zsh
# quantum-weekly 무인 발행 러너. launchd(com.nsw.quantum-weekly)가 일요일 07:10 KST
# 에 호출한다. 수집은 daily-collect.sh 가 매일 따로 돌린다 — 이 스크립트는 그 주의
# 후보 풀에서 카드 10장을 골라 쓰고 발행하는 일만 한다.
#
# 이번 주 에디션이 이미 있으면 아무것도 하지 않는다 — 맥이 자다 깨서 늦게 발화하거나
# 수동으로 한 번 돌린 뒤에 또 발화해도 같은 날짜를 덮어쓰지 않게 한다.
set -u

ROOT=/Users/nsw/meeting_room/lab/quantum-weekly-web
# 발행처. /quantum 컨테이너를 서버에 올리기 전까지는 로컬(:8004)을 가리킨다.
# 배포가 끝나면 plist 의 QUANTUM_DAILY_API 를 프로덕션 주소로 바꾼다.
API="${QUANTUM_DAILY_API:-http://localhost:8004}"
LOG="$ROOT/logs/weekly-cron-$(date +%F).log"
mkdir -p "$ROOT/logs"
cd "$ROOT" || exit 1

NOTIFY="$HOME/.claude/scripts/launchd-notify-failure.sh"
notify_fail() {
  [[ -x "$NOTIFY" ]] && "$NOTIFY" "com.nsw.quantum-weekly" "${2:-1}" "$1" "$LOG" >/dev/null 2>&1
}

{
  today=$(date +%F)
  echo "=== $(date '+%F %T %Z') start ($today) ==="

  if curl -s -m 15 -o /dev/null -w "%{http_code}" "$API/api/editions/$today" | grep -q '^200$'; then
    echo "SKIP: $today 에디션이 이미 있다"
    exit 0
  fi

  # 소스가 죽어 있으면 시작도 하지 않는다 (더미 발행 방지).
  # 한 번의 타임아웃으로 한 주를 통째로 건너뛰지 않도록 20초 간격 3회 재시도한다.
  check() {
    local code=""
    for _ in 1 2 3; do
      code=$(curl -s -m 20 -o /dev/null -w "%{http_code}" "$1")
      [[ "$code" == "200" ]] && break
      sleep 20
    done
    echo "$code"
  }

  news=$(check "http://localhost:8000/api/news?asset=quantum&limit=1")
  yt=$(check "http://localhost:23456/api/queue")
  pub=$(check "$API/health")
  echo "source check: my-news=$news my-youtube=$yt 발행처=$pub"
  if [[ "$news" != "200" || "$yt" != "200" ]]; then
    echo "ABORT: 소스 서버 비정상 — 발행하지 않음"
    notify_fail "소스 서버 비정상 (my-news=$news my-youtube=$yt)" 1
    exit 1
  fi
  if [[ "$pub" != "200" ]]; then
    echo "ABORT: 발행처($API) 응답 없음 — 발행하지 않음"
    notify_fail "발행처 응답 없음 ($API)" 1
    exit 1
  fi

  # 137(=128+9)은 외부에서 보낸 SIGKILL 이다. 머신 사정이지 발행 내용의 문제가
  # 아니므로 한 번은 다시 시도한다(ai-daily-web 에서 2026-09-17 에 실제로 겪었다).
  attempt=1
  while true; do
    /Users/nsw/.local/bin/claude -p \
      "quantum-weekly 스킬을 사용해 이번 주(Asia/Seoul 기준, 최근 7일) 양자 카드뉴스 10장을 만들어 $API 에 발행하라. 스킬 SKILL.md 의 단계를 하나도 빼지 말고 순서대로 수행한다. 후보의 cluster_titles 를 카드마다 반드시 펼쳐서 확인하라 — 이 도메인은 매체가 10곳뿐이라 군이 작지만, 주제가 붙은 다른 연구가 한 군으로 묶이는 일이 있다. 배제 축 셋을 눈으로 한 번 더 걸러라: 양자화(quantization, LLM 경량화), 퀀텀닷 TV(디스플레이), 퀀텀점프(비유). 크립토 각도는 내용으로 가른다 — 비트코인 시황 기사는 빼되 양자내성암호 전환 자체를 다루면 양자 소재다. 논문(arXiv·Nature)과 Quantum Zeitgeist 요약체는 후보에 보여도 일반 독자가 읽을 문장이 나오는지 먼저 확인하고 써라. 한국어 기사와 영문 기사가 같은 사건이면 한국어 쪽을 대표로 써라. 고유명사 표기는 backend/app/wording.py 의 BANNED_TERMS 를 따르라 — 양자컴퓨팅·양자컴퓨터·양자암호·양자기술·양자내성암호는 붙여 쓰고, 퀀텀 컴퓨팅 대신 양자컴퓨팅, 아이온Q 대신 아이온큐, 큐빗 대신 큐비트다. closing.sources 는 카드가 실제로 링크한 매체만 남겨라 — 수집 스크립트가 코퍼스 전체 매체를 채워 두므로 반드시 줄여야 하고, 안 줄이면 verify_edition.py 가 FAIL 을 낸다. 트렌딩 블록은 이 도메인에서 선택이다 — 후보 토픽이 RSS 카테고리 이름뿐이라 사건을 못 짚으면 빼고, 뺀 이유를 보고에 적어라. 수집 결과가 비었거나 소스가 죽어 있으면 더미 데이터로 대체하지 말고 그 자리에서 중단하라. 사실에 없는 숫자를 지어내지 마라. 발행은 push_edition.py 로 하고 링크·이미지 검증(verify_edition.py)을 절대 끄지 마라 — --skip-link-check 는 쓰지 않는다. 마지막에 발행된 날짜, 카드 10장의 제목, 검증 FAIL·WARN 수와 눈으로 확인한 내용을 출력하라." \
      --model claude-sonnet-5 \
      --dangerously-skip-permissions \
      --output-format text
    # zsh 에서 status 는 $? 의 예약 별칭이라 대입하면 스크립트가 그 자리에서 죽는다.
    rc=$?
    echo "=== $(date '+%F %T %Z') claude exit=$rc (시도 $attempt/2) ==="

    [[ "$rc" != "137" || "$attempt" -ge 2 ]] && break

    echo "--- SIGKILL 감지 — 120초 뒤 재시도한다 ---"
    sleep 120
    if curl -s -m 15 -o /dev/null -w "%{http_code}" "$API/api/editions/$today" | grep -q '^200$'; then
      echo "재시도 생략: 다시 보니 $today 에디션이 이미 올라가 있다"
      rc=0
      break
    fi
    attempt=$((attempt + 1))
  done

  [[ "$rc" != "0" ]] && notify_fail "claude 발행 실패" "$rc"

  # claude 가 0 을 반환해도 실제로 올라갔는지는 별개다.
  final=$(curl -s -m 15 -o /dev/null -w "%{http_code}" "$API/api/editions/$today")
  if [[ "$final" != "200" ]]; then
    echo "VERIFY FAIL: 발행 후에도 $today 에디션이 없다 (http=$final)"
    notify_fail "발행 후 검증 실패 — $today 에디션 없음 (http=$final)" "$rc"
  else
    echo "VERIFY OK: $today 에디션 확인"
  fi
} >> "$LOG" 2>&1
