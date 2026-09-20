/** 백엔드 API 의 접두사.
 *
 * 이 도메인은 시리즈가 둘이다 — `daily.onebitecoder.com/ai` 를 ai-daily-web 이
 * 먼저 쓰고 있고, 그쪽이 루트의 `/api` 도 가져갔다. 그래서 이쪽 API 는 서브패스
 * 밑으로 물러난다. 호스트 nginx 가 `/quantum/*` 만 이 컨테이너로 보내고, 컨테이너
 * nginx 가 접두사를 떼어 백엔드에 넘긴다 — 백엔드 라우트 자체는 `/api` 그대로다.
 *
 * `vite.config.ts` 의 `base` 와 짝이다. 한쪽만 바꾸면 개발 서버에서는 되는데
 * 배포에서 404 가 난다.
 */
export const API_BASE = '/quantum/api';
