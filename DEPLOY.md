# DEPLOY.md — quantum-daily-web 최초 배포 런북

**이 문서를 읽는 대상은 배포 서버에 접속한 사람 또는 Claude다.** 위에서부터
순서대로 실행하고, 각 단계 끝의 "확인"이 기대한 값을 내지 않으면 **다음 단계로
넘어가지 말고 멈춰라.** 되돌리는 법은 마지막 절에 있다.

`btc-daily-web`(`daily.onebitebitcoin.com`)이 이미 같은 서버에서 돌고 있다는 것을
전제로 쓴 문서다. 그쪽을 건드리지 않는 것이 이 배포의 제약이다.

| | 값 |
|---|---|
| 도메인 | `daily.onebitecoder.com` |
| 사람이 보는 주소 | `https://daily.onebitecoder.com/ai` (루트 `/`는 `/quantum/`로 301) |
| 컨테이너 노출 포트 | `127.0.0.1:8022` (btc-daily-web이 8020) |
| compose 프로젝트명 | `quantum-daily-web` (디렉토리명 — btc와 갈라져 볼륨·네트워크가 안 겹친다) |
| 서비스 | `db`(Postgres 16) · `backend`(uvicorn) · `web`(nginx) |

---

## 0. 시작 전 확인

```bash
# btc-daily-web 이 살아 있는지 — 이 배포가 끝난 뒤에도 살아 있어야 한다
curl -s -o /dev/null -w "btc: %{http_code}\n" https://daily.onebitebitcoin.com/

# 8022 이 비어 있는지. 뭔가 물려 있으면 멈추고 사람에게 물어라
sudo lsof -iTCP:8022 -sTCP:LISTEN || echo "8022 비어 있음 (정상)"

# DNS 가 이 서버를 가리키는지
dig +short daily.onebitecoder.com
curl -s -o /dev/null -w "%{http_code}\n" http://daily.onebitecoder.com/
```

DNS는 Cloudflare 프록시(주황 구름) 뒤에 있어도 된다 — `dig`에는 Cloudflare IP가
나온다. HTTP-01 챌린지는 CF를 통과한다(같은 서버의 `mempool.onebitebitcoin.com`,
`daily.onebitebitcoin.com`이 같은 방식으로 발급받은 선례가 있다).

**Cloudflare SSL/TLS 모드가 `Full (strict)`여야 한다.** `Flexible`이면 CF가 서버로
평문 HTTP를 보내는데, 아래 vhost의 `:80 → :443` 리다이렉트와 물려 무한 루프가 난다.
이건 서버에서 고칠 수 없다 — 사람이 Cloudflare 대시보드에서 확인할 몫이다.

---

## 1. 코드 배치

```bash
cd /home/measly
git clone https://github.com/onebitebitcoin/quantum-daily-web.git   # 이미 있으면 git pull
cd /home/measly/quantum-daily-web
git log --oneline -1
```

> `/home/measly`는 btc-daily-web이 있는 자리다(`/home/measly/btc-daily-web`).
> 형제 프로젝트를 같은 부모 아래 둔다. 다르면 `ls -d /home/measly/btc-daily-web`
> 으로 실제 위치를 먼저 확인하고 그 옆에 붙여라 — 이 문서의 나머지 경로도 같이
> 바꿔야 한다.

**확인** — `/quantum` 서브패스가 들어온 커밋인지 본다. 이게 없으면 옛 코드다:

```bash
grep -n "base: '/quantum/'" frontend/vite.config.ts
grep -n 'basename="/quantum"' frontend/src/App.tsx
grep -n 'location = / {' frontend/nginx.conf
```

세 줄이 다 나와야 한다.

---

## 2. `.env` 작성

```bash
cp .env.example .env
```

`.env`를 열어 아래 넷을 채운다.

| 키 | 값 | 비고 |
|---|---|---|
| `POSTGRES_PASSWORD` | `openssl rand -hex 32` | |
| `DATABASE_URL` | 위 비밀번호를 **URL 안에도 똑같이** | 어긋나면 backend가 DB에 못 붙는다 |
| `ADMIN_API_KEY` | **발행 머신에서 가져온 값** | 아래 경고 참고 |
| `WEB_PORT` | `8022` | `.env.example` 기본값 그대로 |
| `DOMAIN` | `daily.onebitecoder.com` | `.env.example` 기본값 그대로 |

> **`ADMIN_API_KEY`를 서버에서 새로 뽑지 마라.** 발행은 개발 머신(맥)에서
> `push_edition.py`가 하는데, 그 스크립트는 **맥의 `backend/.env`** 에 있는
> `ADMIN_API_KEY`로 인증한다. 서버 값이 다르면 카드 10장을 다 만들고 게이트를
> 전부 통과한 뒤 마지막 POST에서 401로 튕긴다. 사람이 맥의
> `quantum-daily-web/backend/.env`에서 그 줄을 가져와 여기 붙여넣어야 한다.
>
> Claude가 이 단계를 대신할 수 없다 — 값을 물어보고 기다려라.

Postgres는 **최초 init 때만** 비밀번호를 반영한다. 볼륨을 만든 뒤에 바꾸려면
`docker compose down -v`로 지우고 다시 올려야 한다(데이터도 같이 날아간다).

**확인** — 값이 다 찼는지만 본다. 값 자체는 출력하지 마라:

```bash
sed -n 's/^\([A-Z_]*\)=\(.\+\)/\1 OK/p' .env
```

`POSTGRES_USER`·`POSTGRES_PASSWORD`·`POSTGRES_DB`·`DATABASE_URL`·`ADMIN_API_KEY`·
`WEB_PORT`·`DOMAIN` 일곱 줄이 `OK`로 나와야 한다.

---

## 3. 컨테이너 기동

```bash
docker compose up -d --build
docker compose ps
```

backend는 기동하면서 `alembic upgrade head`를 돌린다. 마이그레이션이 실패하면
컨테이너가 뜨지 않는다 — 의도된 설계다. `docker compose logs backend`를 본다.

**확인**

```bash
curl -s localhost:8022/health                    # {"status":"ok"}
curl -s localhost:8022/api/editions              # [] (아직 발행 전이라 빈 배열)
curl -sI localhost:8022/ | grep -iE '^(HTTP|location)'
#   → HTTP/1.1 301 / Location: /quantum/
#   Location 이 `/quantum/` 상대 경로여야 한다. `http://…/quantum/` 절대 URL 이면
#   absolute_redirect off 가 없는 옛 이미지다(https 가 http 로 한 번 떨어진다).
# index.html 이 참조하는 자산을 **실제로 받아본다**. HTML 안의 경로만 grep 하면
# 파일이 없어도 통과한다 — 2026-08-29 첫 배포가 정확히 그렇게 새어 나갔다.
for p in $(curl -s localhost:8022/quantum/ | grep -o '/quantum/assets/[^"]*'); do
  printf "%s -> " "$p"; curl -s -o /dev/null -w "%{http_code}\n" "localhost:8022$p"
done
#   → 모두 200. 하나라도 404 면 dist 가 html 루트에 풀린 옛 이미지다
```

셋 다 통과해야 이 배포가 끝난 것이다. `301`이 안 나오면 `frontend/nginx.conf`의
`location = /` 블록이 없는 옛 이미지고, 자산이 404 면 Dockerfile 이 dist 를
`/usr/share/nginx/html/ai` 로 넣지 않는 옛 이미지다 — 1단계로 돌아가 커밋을
확인하고 `docker compose up -d --build`를 다시 돌려라.

`web`은 `127.0.0.1`에만 바인딩된다. 공인 IP로 8022이 열려 있으면 안 된다:

```bash
sudo lsof -iTCP:8022 -sTCP:LISTEN    # 127.0.0.1:8022 이어야 한다. *:8022 이면 잘못됐다
```

---

## 4. 인그레스 + TLS (2단계)

호스트 nginx(`/etc/nginx`)가 80/443과 인증서를 소유한다. 컨테이너 안의 nginx와
역할이 다르다 — 이건 TLS 종단 + `127.0.0.1:8022` 프록시만 한다.

인증서가 없는 상태로 `:443` 블록을 넣으면 `nginx -t`가 깨지므로 반드시 두 번에
나눠 올린다.

### 4-1. `:80` 전용 vhost 먼저

```bash
sudo cp deploy/nginx/daily.onebitecoder.com.bootstrap.conf \
        /etc/nginx/sites-available/daily.onebitecoder.com
sudo ln -sf /etc/nginx/sites-available/daily.onebitecoder.com \
            /etc/nginx/sites-enabled/daily.onebitecoder.com
sudo nginx -t && sudo systemctl reload nginx
```

**확인**: `curl -s -o /dev/null -w "%{http_code}\n" http://daily.onebitecoder.com/.well-known/acme-challenge/probe` → `404`
(200이 아니라 404가 정상이다. nginx가 그 경로를 webroot로 넘겼다는 뜻)

### 4-2. 인증서 발급

```bash
sudo certbot certonly --webroot -w /var/www/letsencrypt \
     --cert-name daily.onebitecoder.com -d daily.onebitecoder.com
```

> **기존 공용 인증서에 `--expand`로 붙이지 마라.** SAN 목록을 잘못 넘기면
> `daily.onebitebitcoin.com`이 갱신에서 조용히 빠진다. `--cert-name`으로 별도
> 인증서를 만드는 게 이 서버의 관례다.

`/var/www/letsencrypt`가 없으면 만들고 4-1부터 다시 한다:
`sudo mkdir -p /var/www/letsencrypt`

**확인**: `sudo certbot certificates | grep -A3 daily.onebitecoder.com`

### 4-3. TLS 포함 최종 vhost로 교체

```bash
sudo cp deploy/nginx/daily.onebitecoder.com.conf \
        /etc/nginx/sites-available/daily.onebitecoder.com
sudo nginx -t && sudo systemctl reload nginx
```

**확인**

```bash
curl -sIL https://daily.onebitecoder.com/ | grep -iE '^(HTTP|location)'
#   → 301, Location: /quantum/  그리고 200. 중간에 http:// 가 끼면 안 된다
curl -s  https://daily.onebitecoder.com/health            # {"status":"ok"}
curl -s  https://daily.onebitecoder.com/api/editions      # []
for p in $(curl -s https://daily.onebitecoder.com/quantum/ | grep -o '/quantum/assets/[^"]*'); do
  printf "%s -> " "$p"
  curl -s -o /dev/null -w "%{http_code}\n" "https://daily.onebitecoder.com$p"
done
#   → 모두 200
```

리다이렉트 체인에 `http://`가 한 홉이라도 보이면 컨테이너 이미지가 옛것이다 —
3단계의 `Location: /quantum/` 확인으로 돌아간다.

갱신은 certbot이 등록한 스케줄 작업이 처리한다. 이 문서를 다시 쓸 일은 없다.

---

## 5. 첫 발행 (개발 머신에서)

여기부터는 **서버가 아니라 맥**에서 한다. 수집 소스(my-news `:8000`,
my-youtube `:23456`)가 맥에만 있기 때문이다.

```bash
cd ~/meeting_room/lab/quantum-daily-web/backend && source .venv/bin/activate
python scripts/push_edition.py ../drafts/edition-2026-08-29.json \
       --api https://daily.onebitecoder.com
```

`401`이 나오면 맥의 `backend/.env`와 서버 `.env`의 `ADMIN_API_KEY`가 다른 것이다.
서버 값을 맥 값으로 맞춘 뒤 `docker compose up -d`로 backend를 다시 띄운다.

로컬 백엔드(`:8003`)에 이미 있는 발행분을 프로덕션으로 옮기려면 같은 명령을
날짜별로 반복한다(`meta.date`가 같으면 upsert라 여러 번 쏴도 안전하다):

```bash
for f in ../drafts/edition-*.json; do
  python scripts/push_edition.py "$f" --api https://daily.onebitecoder.com
done
```

**확인**: `https://daily.onebitecoder.com/quantum/d/2026-08-29`를 브라우저로 연다.
카드 10장과 트렌딩 슬라이드가 보여야 한다.

---

## 6. 무인 발행 전환 (개발 머신에서)

`scripts/daily-cron.sh`의 `API`는 이미 프로덕션을 가리킨다. launchd 등록만 남았다.

```bash
cp deploy/com.nsw.quantum-daily.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.nsw.quantum-daily.plist
launchctl list | grep quantum-daily
```

매일 **06:10 KST**에 발화한다(btc-daily가 06:00이라 10분 비켜 뒀다 — 둘 다 claude를
부르고 같은 소스 서버를 두드린다). 오늘자 에디션이 프로덕션에 이미 있으면 아무것도
하지 않는다(`SKIP` 로그만 남는다). 손으로 한 번 돌려보려면:

```bash
zsh scripts/daily-cron.sh && tail -40 logs/daily-cron-$(date +%F).log
```

---

## 7. 갱신 배포 (다음부터) — 자동

`main`에 푸시하면 끝난다. 손으로 서버에 들어갈 일이 없다.

`.github/workflows/ci.yml`의 `deploy` job이 self-hosted 러너에서 `git pull` →
`docker compose build backend web` → `up -d` → **자산을 실제로 GET 해 200 확인**까지
한다. `backend`·`frontend` job이 통과해야만 돈다(`needs`).

| | 값 |
|---|---|
| 러너 이름 | `measly-quantum-daily` |
| 러너 라벨 | `self-hosted, quantum-daily-web` ← btc 러너 라벨(`btc-daily-web`)과 반드시 달라야 한다 |
| 러너 경로 | `/home/measly/actions-runner-quantum-daily` |
| systemd | `actions.runner.onebitebitcoin-quantum-daily-web.measly-quantum-daily.service` |

```bash
# 러너 상태
systemctl status actions.runner.onebitebitcoin-quantum-daily-web.measly-quantum-daily.service
gh api repos/onebitebitcoin/quantum-daily-web/actions/runners \
  --jq '.runners[] | "\(.name) \(.status) \([.labels[].name]|join(","))"'

# 배포 결과
gh run list --repo onebitebitcoin/quantum-daily-web --limit 5
```

마이그레이션은 backend 컨테이너가 기동하며 `alembic upgrade head`로 적용한다.
호스트 nginx vhost(`/etc/nginx`)는 자동화에 들어 있지 **않다** — `deploy/nginx/`의
파일이 바뀐 경우에만 사람이 `sudo nginx -t` 후 손으로 복사한다. 컨테이너 안의
`frontend/nginx.conf`는 이미지에 들어가므로 자동 배포에 포함된다.

### 손으로 배포해야 할 때

러너가 죽었거나 워크플로를 우회할 때만 쓴다:

```bash
cd /home/measly/quantum-daily-web && git pull && docker compose up -d --build
curl -s localhost:8022/health
```

### 러너를 다시 붙일 때

등록 토큰은 일회용이라 매번 새로 받는다:

```bash
cd /home/measly/actions-runner-quantum-daily
TOKEN=$(gh api -X POST repos/onebitebitcoin/quantum-daily-web/actions/runners/registration-token --jq .token)
./config.sh --url https://github.com/onebitebitcoin/quantum-daily-web --token "$TOKEN" \
  --name measly-quantum-daily --labels quantum-daily-web --work _work --unattended --replace
sudo ./svc.sh install measly && sudo ./svc.sh start
```

---

## 8. 되돌리기

| 상황 | 조치 |
|---|---|
| 컨테이너만 문제 | `docker compose down && git checkout <이전 커밋> && docker compose up -d --build` |
| vhost가 nginx를 깨뜨림 | `sudo rm /etc/nginx/sites-enabled/daily.onebitecoder.com && sudo nginx -t && sudo systemctl reload nginx` — btc-daily-web은 별도 vhost라 영향받지 않는다 |
| DB를 갈아엎어야 함 | `docker compose down -v` (**데이터가 지워진다**). 발행분은 맥의 `drafts/edition-*.json`에 남아 있으므로 5절로 다시 올리면 복구된다 |
| 전체 철수 | `docker compose down -v` + vhost 심볼릭 링크 제거 + `sudo certbot delete --cert-name daily.onebitecoder.com` |

발행분 백업은 `deploy/backup.sh`가 `.env`의 `POSTGRES_USER`/`POSTGRES_DB`를 읽어
`pg_dump`를 뜬다. 첫 발행 뒤 한 번 돌려 두는 게 좋다.

---

## 이 배포에서 하지 말아야 할 것

- **btc-daily-web의 vhost·컨테이너·인증서를 건드리지 마라.** 같은 서버에 있지만
  완전히 별개다. `certbot --expand`로 인증서를 합치는 것도 여기 포함된다.
- **`WEB_PORT`를 8020으로 바꾸지 마라.** btc-daily-web이 쓰고 있다.
- **`web` 서비스를 `0.0.0.0`에 바인딩하지 마라.** 공인 IP:8022로 TLS와
  Cloudflare를 우회한 평문 직결이 뚫린다.
- **`.env` 값을 로그나 터미널에 출력하지 마라.** 있는지 없는지만 확인한다.
- **`--skip-link-check`로 발행하지 마라.** 링크·이미지 검증은 켜 두는 게 기본이다.

---

## 이 저장소만의 선행 과제 — 한 도메인에 두 시리즈

`daily.onebitecoder.com` 은 이미 ai-daily-web 컨테이너(127.0.0.1:8021)가 쓰고 있고,
그쪽이 도메인 루트(`/`)를 `/ai/` 로 301 하며 `/api` 도 루트에서 받는다. 여기에
`/quantum` 을 붙이려면 **호스트 nginx 의 vhost 파일 하나를 두 시리즈가 나눠 쓰는
구조로 바꿔야 한다.** 이 저장소의 `deploy/nginx/*.conf` 는 그 전제로 쓰인 것이 아니라
ai-daily-web 것을 포트만 바꿔 들고 온 것이라, 그대로 덮어쓰면 `/ai` 가 죽는다.

정리해야 할 것 셋이다.

1. **vhost 소유권** — `daily.onebitecoder.com` conf 는 한 파일뿐이다. 두 저장소가
   각자 conf 를 들고 있으면 나중에 한쪽을 배포하면서 다른 쪽을 지운다. 한쪽(ai) 이
   소유하고 여기서는 추가할 `location` 블록만 문서로 남기는 편이 안전하다.
2. **API 경로 충돌** — ai 쪽 `/api` 가 루트에 있다. quantum 백엔드도 `/api` 를
   쓰므로 `/quantum/api` 로 받도록 프런트의 API 베이스와 nginx 프록시를 같이 맞춰야
   한다. 지금 코드는 `/api` 를 그대로 부른다.
3. **루트 리다이렉트** — 지금은 `/` → `/ai/` 다. 시리즈가 둘이 되면 루트를 무엇으로
   할지 정해야 한다(목록 페이지를 두거나, 한쪽을 기본으로 남기거나).

배포 전까지는 `scripts/weekly-cron.sh` 가 `QUANTUM_DAILY_API` 로 로컬 백엔드
(`http://localhost:8004`)에 발행한다. plist 두 개의 같은 이름 환경변수를 프로덕션
주소로 바꾸는 것이 배포의 마지막 단계다.
