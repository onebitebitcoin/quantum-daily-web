import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';
import { ClosingSlide } from './slides/ClosingSlide';
import type { Closing } from './content';

// CoverSlide.test.tsx 와 같은 이유로 slides/ 밖에 둔다 — 컴포넌트 파일에서 헬퍼를
// export 하면 eslint react-refresh/only-export-components 가 경고한다.

const CLOSING: Closing = {
  eyebrow: 'SUMMARY',
  mark_lines: ['데일리', 'AI'],
  links: [{ label: '유튜브 구독하기', href: 'https://youtube.example/c' }],
  stamp: '오늘의 발행 완료',
  restart: '처음부터 다시 보기 ↺',
  sources: ['토큰포스트', 'TechCrunch'],
};

function renderClosing(closing: Closing) {
  return render(
    <ClosingSlide closing={closing} isActive onRestart={() => {}} hasNextEdition={false} />,
  );
}

describe('ClosingSlide', () => {
  it('출처를 카드에 쓴 매체만 한 줄로 늘어놓는다', () => {
    const { container } = renderClosing(CLOSING);

    expect(container.querySelector('.src-list')?.textContent).toBe(
      '출처 — 토큰포스트 · TechCrunch',
    );
  });

  it('면책 문구가 없으면 그 자리를 아예 비운다', () => {
    // 2026-08-26 부터 안 쓰기로 했다. 빈 <p> 가 남으면 여백만 생긴다.
    const { container } = renderClosing(CLOSING);

    expect(container.querySelector('.disclaimer')).toBeNull();
  });

  it('옛 발행분에 남아 있는 면책 문구는 그대로 보여준다', () => {
    const { container } = renderClosing({ ...CLOSING, disclaimer: '지난 발행분 문구' });

    expect(container.querySelector('.disclaimer')?.textContent).toBe('지난 발행분 문구');
  });
});
