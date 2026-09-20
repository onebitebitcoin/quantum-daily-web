# 호스트 nginx — `/quantum` 서브패스

**이 저장소는 `daily.onebitecoder.com` 의 vhost 파일을 소유하지 않는다.**
소유자는 `ai-daily-web` 이고, 최종본은 거기 있다:

```
ai-daily-web/deploy/nginx/daily.onebitecoder.com.conf
```

그 파일의 443 server 블록이 `/ai`(127.0.0.1:8021)와 `/quantum`(127.0.0.1:8023)을
각각 자기 컨테이너로 보낸다. 두 저장소가 각자 conf 를 들고 있으면 나중에 한쪽을
배포하면서 다른 쪽을 지우므로, 여기서는 사본을 두지 않는다.

`quantum-location.conf` 는 그 파일에 들어간 `/quantum` 블록의 사본이다. 읽기
편하라고 남겨 둔 것이고, 서버에 직접 넣는 용도가 아니다. 호스트 설정을 바꿀 일이
있으면 ai-daily-web 쪽 파일을 고친다.

## 서버에 반영하는 법

```bash
# 1. 지금 설정을 먼저 백업한다 — /ai 가 살아 있는 상태가 돌아갈 자리다
sudo cp /etc/nginx/sites-available/daily.onebitecoder.com \
        /etc/nginx/sites-available/daily.onebitecoder.com.bak-$(date +%F)

# 2. ai-daily-web 의 최종본으로 교체한다
sudo cp /home/measly/ai-daily-web/deploy/nginx/daily.onebitecoder.com.conf \
        /etc/nginx/sites-available/daily.onebitecoder.com

# 3. 문법을 먼저 본다. 여기서 실패하면 reload 하지 마라 — 현재 설정이 그대로 산다
sudo nginx -t

# 4. 통과했을 때만 반영한다
sudo systemctl reload nginx

# 5. 두 시리즈가 다 살아 있는지 확인한다. 하나라도 어긋나면 1번 백업으로 되돌린다
D=https://daily.onebitecoder.com
curl -s -o /dev/null -w '/ai/          %{http_code}\n' $D/ai/
curl -s -o /dev/null -w '/ai/api       %{http_code}\n' $D/ai/api/editions
curl -s -o /dev/null -w '/quantum/     %{http_code}\n' $D/quantum/
curl -s -o /dev/null -w '/quantum/api  %{http_code}\n' $D/quantum/api/editions
```

## 왜 이 순서로 잡히는가

nginx 의 접두사 location 은 **가장 긴 것**이 이긴다. 파일 안의 순서와 무관하게
`/quantum/...` 은 `location /quantum` 이, `/ai/...` 는 `location /ai` 가 받는다.

두 시리즈 모두 API 와 헬스체크가 자기 서브패스 밑이다 — `/ai/api`·`/ai/health` 와
`/quantum/api`·`/quantum/health`. 도메인 루트는 어느 쪽의 것도 아니고 `/ai/` 로
301 만 한다(`frontend/src/apiBase.ts` 참고).

루트의 `/api` 와 `/health` 는 ai-daily-web 이 예전에 쓰던 경로이며, 이미 스크랩된
공유 링크 때문에 전환 기간 동안만 살아 있다. 양자 쪽은 처음부터 쓰지 않는다.
