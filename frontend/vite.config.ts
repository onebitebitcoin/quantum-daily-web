/// <reference types="vitest/config" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  // /quantum 경로 밑에서 서빙한다. 같은 도메인의 /ai 는 ai-daily-web 것이고
  // 루트의 /api 도 그쪽이 쓰므로, 이쪽은 API 까지 서브패스 밑으로 물러난다
  // (frontend/src/apiBase.ts 와 짝).
  base: '/quantum/',
  plugins: [react()],
  server: {
    // 포트 고정. 5173은 my-academy, 5175는 btc-daily-web이 쓴다.
    port: 5177,
    strictPort: true,
    // 기본값이면 [::1]에만 붙어서 이 머신 밖에서는 안 보인다. 발행분을 폰으로
    // 확인하려고 테일스케일(nsw.golden-ghost.ts.net / 100.76.163.73)로 여는 중이라
    // 모든 인터페이스에 바인딩한다. 같은 공유기의 다른 기기에도 열린다는 뜻이니
    // 카페 와이파이 같은 데서는 꺼라.
    host: true,
    // vite 6은 Host 헤더가 IP나 localhost가 아니면 막는다. 테일스케일 MagicDNS
    // 이름으로 붙으려면 여기 적혀 있어야 한다(IP로만 붙을 거면 없어도 된다).
    allowedHosts: ['nsw.golden-ghost.ts.net'],
    // API 는 `/quantum/api` 로 받아 접두사를 떼고 백엔드에 넘긴다 — 프로덕션의
    // 컨테이너 nginx 와 같은 모양이라야 개발에서만 되는 일이 안 생긴다.
    // 포트 8004 는 btc-daily-web(8002)·ai-daily-web(8003)과 겹치지 않게 고른 값이다.
    proxy: {
      '/quantum/api': {
        target: 'http://localhost:8004',
        rewrite: (path) => path.replace(/^\/quantum/, ''),
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test/setup.ts',
  },
});
