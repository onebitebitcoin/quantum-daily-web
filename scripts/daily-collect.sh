#!/bin/zsh
# 매일 도는 수집 러너. launchd(com.nsw.quantum-collect)가 06:40 KST 에 호출한다.
#
# 발행은 주 1회지만 수집은 매일 돈다. 이유가 둘이다.
#   1. 후보 창이 7일이라 매일 돌리면 그날까지의 한 주 풀이 항상 최신으로 있다.
#      일요일에 처음 돌려서 소스가 죽어 있으면 그 주가 통째로 빈다.
#   2. 일별 집계를 남겨야 "주말에 몇 건까지 떨어지는가"를 나중에 근거로 본다.
#      logs/collect-stats.tsv 가 그 기록이다.
set -u

ROOT=/Users/nsw/meeting_room/lab/quantum-daily-web
LOG="$ROOT/logs/collect-$(date +%F).log"
STATS="$ROOT/logs/collect-stats.tsv"
mkdir -p "$ROOT/logs"
cd "$ROOT/backend" || exit 1

NOTIFY="$HOME/.claude/scripts/launchd-notify-failure.sh"
notify_fail() {
  [[ -x "$NOTIFY" ]] && "$NOTIFY" "com.nsw.quantum-collect" "${2:-1}" "$1" "$LOG" >/dev/null 2>&1
}

{
  echo "=== $(date '+%F %T %Z') collect start ==="

  # 소스가 죽어 있으면 빈 draft 를 덮어쓰지 않는다.
  news=$(curl -s -m 20 -o /dev/null -w "%{http_code}" "http://localhost:8000/api/news?asset=quantum&limit=1")
  if [[ "$news" != "200" ]]; then
    echo "ABORT: my-news 비정상 (http=$news)"
    notify_fail "my-news 비정상 (http=$news)" 1
    exit 1
  fi

  # --edition-api 는 발행 이력 조회처다. /quantum 배포 전에는 로컬을 가리키며,
  # 로컬 백엔드가 꺼져 있으면 인용구·이미지 중복 회피만 건너뛰고 수집은 계속된다.
  out=$(.venv/bin/python scripts/collect_daily.py \
          --edition-api "${QUANTUM_DAILY_API:-http://localhost:8004}" 2>&1)
  rc=$?
  echo "$out"
  echo "=== $(date '+%F %T %Z') collect exit=$rc ==="

  if [[ "$rc" != "0" ]]; then
    notify_fail "수집 실패" "$rc"
    exit "$rc"
  fi

  # 일별 집계 한 줄. 헤더는 파일이 없을 때만 쓴다.
  [[ -f "$STATS" ]] || printf 'date\tcandidates\tquantum\tphysics\tevents\tfolded\timages\tvideos\n' > "$STATS"
  line=$(echo "$out" | grep -E '^news candidates:')
  fold=$(echo "$out" | grep -E '^event folding:')
  img=$(echo "$out"  | grep -E '^image coverage:')
  vid=$(echo "$out"  | grep -E '^video candidates:')
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$(date +%F)" \
    "$(echo "$line" | sed -E 's/.*candidates: ([0-9]+).*/\1/')" \
    "$(echo "$line" | sed -E 's/.*quantum ([0-9]+).*/\1/')" \
    "$(echo "$line" | sed -E 's/.*physics ([0-9]+).*/\1/')" \
    "$(echo "$fold" | sed -E 's/.*사건 ([0-9]+)개.*/\1/')" \
    "$(echo "$fold" | sed -E 's/^event folding: ([0-9]+).*/\1/')" \
    "$(echo "$img"  | sed -E 's/.*coverage: ([0-9]+\/[0-9]+).*/\1/')" \
    "$(echo "$vid"  | sed -E 's/.*candidates: ([0-9]+).*/\1/')" \
    >> "$STATS"
  echo "stats 기록: $(tail -1 "$STATS")"
} >> "$LOG" 2>&1
