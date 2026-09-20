"""OG(Open Graph) 이미지·메타 HTML 생성 — 링크 공유 미리보기용.

SNS 크롤러는 JS를 실행하지 않으므로 SPA의 정적 index.html로는 날짜별 메타를
줄 수 없다. 이 모듈이 만드는 값은 컨테이너 nginx가 크롤러 User-Agent만 골라
백엔드로 라우팅했을 때 쓰인다(frontend/nginx.conf 참고).
"""

import html
import io
from pathlib import Path
from typing import Any

from fastapi import Request
from PIL import Image

OG_IMAGE_SIZE = (1200, 630)
DESCRIPTION_MAX_LEN = 160


def pick_card(content: dict[str, Any], card_index: int | None) -> dict[str, Any] | None:
    """슬라이드 위치로 카드를 고른다. 없거나 범위를 벗어나면 None.

    `card_index` 는 `/quantum/d/:date/:index` 의 index, 곧 **에디션 안의 슬라이드 위치**다
    (0 = 표지, 1 = 첫 뉴스 카드). 카드 자체의 번호인 `card["num"]` 과는 다른
    개념이라 `cards[card_index - 1]` 로 찾는다.

    범위를 벗어나도 예외를 던지지 않는다. 호출자가 에디션 기준으로 되돌아가게
    두려는 것이다 — 크롤러에게 404 를 주면 미리보기가 통째로 사라지므로, 링크가
    잘못되었어도 날짜 미리보기는 뜨는 편이 낫다."""
    if card_index is None or card_index < 1:
        return None
    cards = content.get("cards") or []
    if card_index > len(cards):
        return None
    return cards[card_index - 1]


def resolve_og_image_url(content: dict[str, Any], card_index: int | None = None) -> str | None:
    """링크 미리보기에 쓸 이미지 URL을 반환한다.

    `card_index` 가 가리키는 카드에 쓸 그림이 있으면 그것을 먼저 쓴다. 그 카드에
    그림이 없거나 인덱스가 범위를 벗어나면 카드 1(첫 뉴스 카드)로 되돌아간다.

    stem(번들 asset)이나 이미지 자체가 없으면 None — CONTENT_CONTRACT.md 4장에
    따르면 배포본은 항상 절대 URL을 쓰므로, stem은 로컬 시드 fixture에서만 나온다."""
    card = pick_card(content, card_index)
    if card is not None:
        picked = (card.get("media") or {}).get("image")
        if picked and picked.startswith("http"):
            return picked

    cards = content.get("cards") or []
    if not cards:
        return None
    media = cards[0].get("media")
    if not media:
        return None
    image = media.get("image")
    if not image or not image.startswith("http"):
        return None
    return image


def crop_to_fill(img: Image.Image, size: tuple[int, int] = OG_IMAGE_SIZE) -> Image.Image:
    """비율을 유지한 채 리사이즈한 뒤 중앙을 기준으로 target 크기에 맞춰 자른다."""
    target_w, target_h = size
    src_w, src_h = img.size
    scale = max(target_w / src_w, target_h / src_h)
    resized = img.resize((round(src_w * scale), round(src_h * scale)))
    left = (resized.width - target_w) // 2
    top = (resized.height - target_h) // 2
    return resized.crop((left, top, left + target_w, top + target_h))


def og_image_bytes_to_jpeg(raw: bytes) -> bytes:
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    cropped = crop_to_fill(img)
    buf = io.BytesIO()
    cropped.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def og_cache_path(cache_dir: str, date_iso: str, card_index: int | None = None) -> Path:
    """카드마다 그림이 다르므로 파일도 갈라 둔다 — 한 파일을 나눠 쓰면 먼저 구운
    카드의 그림이 다른 카드 미리보기로 나간다."""
    if card_index is None:
        return Path(cache_dir) / f"{date_iso}.jpg"
    return Path(cache_dir) / f"{date_iso}-{card_index}.jpg"


def build_og_description(
    content: dict[str, Any],
    max_len: int = DESCRIPTION_MAX_LEN,
    card_index: int | None = None,
) -> str:
    card = pick_card(content, card_index)
    if card is not None:
        text = f"{card.get('title', '')} — {card.get('body', '')}".strip()
        return text if len(text) <= max_len else text[:max_len].rstrip() + "…"

    cards = content.get("cards") or []
    if not cards:
        return content.get("cover", {}).get("hint", "")
    first = cards[0]
    text = f"{first.get('title', '')} — {first.get('body', '')}".strip()
    if len(text) <= max_len:
        return text
    return text[:max_len].rstrip() + "…"


# 이 카드뉴스가 서빙되는 서브패스. frontend/vite.config.ts 의 `base`,
# frontend/src/apiBase.ts 의 `API_BASE`, frontend/nginx.conf 의 location 들과
# 같은 값이어야 한다 — 한 곳만 바꾸면 미리보기 링크가 404 로 간다.
SUBPATH = "/quantum"


def _request_origin(request: Request) -> str:
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("host", request.url.netloc)
    return f"{scheme}://{host}"


def render_og_html(
    content: dict[str, Any], date_iso: str, request: Request, card_index: int | None = None
) -> str:
    """공유 미리보기용 메타 HTML.

    `card_index` 를 주면 그 카드의 제목·본문·그림으로 채운다. 카드 한 장이 공유
    단위이므로, 어느 카드를 공유하든 같은 미리보기가 뜨면 링크를 받은 사람이
    무엇을 여는지 알 수 없다. 범위를 벗어난 인덱스는 에디션 기준으로 돌아간다."""
    origin = _request_origin(request)
    card = pick_card(content, card_index)
    headline = card["title"] if card is not None else content["meta"]["title"]

    title = html.escape(f"{headline} · 위클리 퀀텀")
    description = html.escape(build_og_description(content, card_index=card_index))
    # 이 도메인은 시리즈가 둘이라 API 가 `/quantum/api` 로 물러나 있다
    # (frontend/src/apiBase.ts 와 짝). 미리보기 봇이 여는 절대 URL 이므로
    # 접두사가 빠지면 ai-daily-web 의 API 로 가서 엉뚱한 그림을 받는다.
    image_path = (
        f"{SUBPATH}/api/og/{date_iso}/image.jpg"
        if card is None
        else f"{SUBPATH}/api/og/{date_iso}/{card_index}/image.jpg"
    )
    image_url = html.escape(f"{origin}{image_path}")
    page_path = (
        f"{SUBPATH}/d/{date_iso}" if card is None else f"{SUBPATH}/d/{date_iso}/{card_index}"
    )
    page_url = html.escape(f"{origin}{page_path}")

    return f"""<!doctype html>
<html lang="ko">
  <head>
    <meta charset="utf-8" />
    <title>{title}</title>
    <meta name="description" content="{description}" />
    <meta property="og:type" content="article" />
    <meta property="og:site_name" content="데일리 AI" />
    <meta property="og:title" content="{title}" />
    <meta property="og:description" content="{description}" />
    <meta property="og:image" content="{image_url}" />
    <meta property="og:url" content="{page_url}" />
    <meta name="twitter:card" content="summary_large_image" />
    <meta name="twitter:title" content="{title}" />
    <meta name="twitter:description" content="{description}" />
    <meta name="twitter:image" content="{image_url}" />
  </head>
  <body></body>
</html>
"""
