/// <reference types="vitest/config" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  // /ai 경로 밑에서 서빙한다 — 앞으로 이 도메인 루트에 다른 시리즈가 올라올 수 있어서
  // 이 카드뉴스는 서브패스로 물러난다.
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
    // 백엔드도 btc-daily-web(8002)과 겹치지 않게 8003이다. 여기가 8002면
    // 개발 서버가 조용히 비트코인 에디션을 읽는다.
    proxy: { '/api': 'http://localhost:8004' },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test/setup.ts',
  },
});
