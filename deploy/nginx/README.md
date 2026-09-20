# 호스트 nginx — `/quantum` 서브패스

**이 저장소는 `daily.onebitecoder.com` 의 vhost 파일을 소유하지 않는다.**
그 파일은 `ai-daily-web` 것이고 지금 `/ai` 를 서비스하고 있다. 여기서 전체 conf 를
들고 있다가 덮어쓰면 그 순간 `/ai` 가 죽는다 — 그래서 **추가할 `location` 블록
하나만** 아래 파일로 둔다.

- `quantum-location.conf` — `/etc/nginx/sites-available/daily.onebitecoder.com` 의
  `listen 443` server 블록 **안**에 붙여 넣는다.

## 붙이는 법

```bash
# 1. 지금 설정을 먼저 백업한다 — /ai 가 살아 있는 상태가 돌아갈 자리다
sudo cp /etc/nginx/sites-available/daily.onebitecoder.com \
        /etc/nginx/sites-available/daily.onebitecoder.com.bak-$(date +%F)

# 2. 443 server 블록 안, `location /` 앞에 quantum-location.conf 내용을 넣는다
sudo nano /etc/nginx/sites-available/daily.onebitecoder.com

# 3. 문법을 먼저 본다. 여기서 실패하면 reload 하지 마라 — 현재 설정이 그대로 산다
sudo nginx -t

# 4. 통과했을 때만 반영한다
sudo systemctl reload nginx

# 5. 두 시리즈가 다 살아 있는지 확인한다. 하나라도 200 이 아니면 1번 백업으로 되돌린다
curl -s -o /dev/null -w '/ai       %{http_code}\n' https://daily.onebitecoder.com/ai/
curl -s -o /dev/null -w '/quantum  %{http_code}\n' https://daily.onebitecoder.com/quantum/
curl -s -o /dev/null -w '/api      %{http_code}\n' https://daily.onebitecoder.com/api/editions
```

## 왜 이 순서로 잡히는가

nginx 의 접두사 location 은 **가장 긴 것**이 이긴다. 파일 안의 순서와 무관하게
`/quantum/...` 은 `location /quantum` 이 받고 나머지는 `location /` 이 받는다.
위에 두는 것은 읽는 사람 편의일 뿐이다.

`/api` 와 `/health` 는 루트에 남아 ai-daily-web 이 계속 쓴다. 양자 쪽은
`/quantum/api` 와 `/quantum/health` 로 물러나 있다(`frontend/src/apiBase.ts` 참고).
