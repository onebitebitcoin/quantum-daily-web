# 구현 진행 상황

quantum-daily-web → quantum-weekly-web 전면 개명.
계획: ~/.claude/plans/flickering-exploring-marble.md

## 완료된 Phase
- [x] Phase 1: 코드·문서 안의 이름을 전부 바꾼다 (로컬, 저장소명 변경 전)

- [x] Phase 2: 로컬 디렉토리 rename + launchd 재등록

- [x] Phase 3: GitHub 저장소 rename + 로컬 remote 갱신

- [x] Phase 3: GitHub 저장소 rename + 로컬 remote 갱신 + ai-daily-web 쪽 주석 정정

- [x] Phase 4: 서버 디렉토리·러너 rename (사람이 SSH 로 완료 — 러너 measly-quantum-weekly
  online, deploy 잡 success, 2026-09-20 발행분 데이터 보존 확인)

## 현재 진행 중
- [ ] Phase 5: 마무리 확인
  - [x] og:site_name 하드코딩("데일리 AI") 발견·수정 — Phase 5 검증 중 실측으로 발견,
    BRAND 상수로 통합, 회귀 테스트 추가 (d1d55a7, 배포 확인 중)
  - [ ] 최종 프로덕션 확인 (화면 제목·site_name·카드 이미지 10장)
  - [ ] PROGRESS.md 삭제 + 최종 커밋
