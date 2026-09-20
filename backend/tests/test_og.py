import io

import httpx
from PIL import Image
from test_routes import reference_payload, seed_edition

from app.config import Settings, get_settings


def _fake_source_image_bytes(size: tuple[int, int] = (300, 200)) -> bytes:
    img = Image.new("RGB", size, color=(200, 50, 50))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _payload_with_image_url(date: str, url: str | None = "https://example.com/thumb.jpg"):
    payload = reference_payload(date)
    if url is None:
        payload["cards"][0]["media"] = None
    else:
        payload["cards"][0]["media"]["image"] = url
    return payload


def override_og_cache_dir(client, cache_dir) -> None:
    client.app.dependency_overrides[get_settings] = lambda: Settings(og_cache_dir=str(cache_dir))


# ---- GET /api/og/{date}/image.jpg ----


def test_og_image_generates_and_crops_to_1200x630(client, tmp_path, monkeypatch) -> None:
    override_og_cache_dir(client, tmp_path)
    seed_edition(client.session_factory, _payload_with_image_url("2026-07-30"))

    calls = []

    def fake_get(url, timeout=None, follow_redirects=None):
        calls.append(url)
        fake_request = httpx.Request("GET", url)
        return httpx.Response(200, content=_fake_source_image_bytes(), request=fake_request)

    monkeypatch.setattr("app.routes.httpx.get", fake_get)

    response = client.get("/api/og/2026-07-30/image.jpg")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    img = Image.open(io.BytesIO(response.content))
    assert img.size == (1200, 630)
    assert img.format == "JPEG"
    assert calls == ["https://example.com/thumb.jpg"]


def test_og_image_second_request_hits_cache(client, tmp_path, monkeypatch) -> None:
    override_og_cache_dir(client, tmp_path)
    seed_edition(client.session_factory, _payload_with_image_url("2026-07-30"))

    calls = []

    def fake_get(url, timeout=None, follow_redirects=None):
        calls.append(url)
        fake_request = httpx.Request("GET", url)
        return httpx.Response(200, content=_fake_source_image_bytes(), request=fake_request)

    monkeypatch.setattr("app.routes.httpx.get", fake_get)

    first = client.get("/api/og/2026-07-30/image.jpg")
    second = client.get("/api/og/2026-07-30/image.jpg")

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(calls) == 1


def test_og_image_missing_media_returns_404(client, tmp_path) -> None:
    override_og_cache_dir(client, tmp_path)
    seed_edition(client.session_factory, _payload_with_image_url("2026-07-30", url=None))

    response = client.get("/api/og/2026-07-30/image.jpg")

    assert response.status_code == 404


def test_og_image_missing_edition_returns_404(client, tmp_path) -> None:
    override_og_cache_dir(client, tmp_path)

    response = client.get("/api/og/2026-01-01/image.jpg")

    assert response.status_code == 404


# ---- GET /api/og/{date} and /api/og/latest ----


def test_og_html_contains_meta_tags(client) -> None:
    seed_edition(client.session_factory, _payload_with_image_url("2026-07-30"))

    response = client.get("/api/og/2026-07-30")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    body = response.text
    # 앞부분은 레퍼런스 페이로드의 meta.title(비트코인 카드뉴스 시절 표본이라
    # CLAUDE.md 가 그대로 두기로 한 파일이다), 뒤에 붙는 브랜드는 이 서비스 것이다.
    assert 'property="og:title" content="비트코인 하이라이트' in body
    assert '데일리 퀀텀" />' in body
    assert 'property="og:description"' in body
    assert 'property="og:image" content="http://testserver/quantum/api/og/2026-07-30/image.jpg"' in body
    assert 'property="og:url" content="http://testserver/quantum/d/2026-07-30"' in body


def test_og_html_missing_date_returns_404(client) -> None:
    response = client.get("/api/og/2026-01-01")

    assert response.status_code == 404


def test_og_html_latest_picks_max_date(client) -> None:
    seed_edition(client.session_factory, _payload_with_image_url("2026-07-30"))
    later = _payload_with_image_url("2026-08-01")
    later["meta"]["slug"] = "btc-daily-0801"
    seed_edition(client.session_factory, later)

    response = client.get("/api/og/latest")

    assert response.status_code == 200
    assert 'property="og:url" content="http://testserver/quantum/d/2026-08-01"' in response.text


def test_og_html_latest_returns_404_when_empty(client) -> None:
    response = client.get("/api/og/latest")

    assert response.status_code == 404


# ---------- 카드별 미리보기 ----------

CARD_DATE = "2026-07-30"


def _seed_cards_with_distinct_images(session_factory) -> dict:
    """카드마다 다른 그림을 물린 에디션을 심는다.

    미리보기가 카드별로 갈리는지 보려면 그림부터 달라야 한다. 레퍼런스 콘텐츠는
    번들 asset(stem)을 쓰는 카드가 섞여 있어 그대로는 절대 URL이 안 나온다.
    """
    payload = reference_payload(CARD_DATE)
    for card in payload["cards"]:
        card["media"] = {
            "image": f"https://cdn.example/card-{card['num']}.jpg",
            "href": None,
            "cta": None,
        }
    seed_edition(session_factory, payload)
    return payload


def test_card_og_uses_that_cards_title_and_image(client) -> None:
    payload = _seed_cards_with_distinct_images(client.session_factory)

    body = client.get(f"/api/og/{CARD_DATE}/3").text

    assert payload["cards"][2]["title"] in body
    assert f"/api/og/{CARD_DATE}/3/image.jpg" in body
    assert f'og:url" content="http://testserver/quantum/d/{CARD_DATE}/3"' in body


def test_card_og_differs_between_cards(client) -> None:
    # 카드마다 같은 미리보기가 뜨면 링크를 받은 사람이 무엇을 여는지 알 수 없다.
    _seed_cards_with_distinct_images(client.session_factory)

    third = client.get(f"/api/og/{CARD_DATE}/3").text
    fifth = client.get(f"/api/og/{CARD_DATE}/5").text

    assert third != fifth


def test_edition_og_is_unchanged_without_card_index(client) -> None:
    payload = _seed_cards_with_distinct_images(client.session_factory)

    body = client.get(f"/api/og/{CARD_DATE}").text

    assert payload["meta"]["title"] in body
    assert f"/api/og/{CARD_DATE}/image.jpg" in body
    assert f'og:url" content="http://testserver/quantum/d/{CARD_DATE}"' in body


def test_card_og_falls_back_to_edition_when_index_is_out_of_range(client) -> None:
    """범위를 벗어난 인덱스에 404를 주면 미리보기가 통째로 사라진다.

    크롤러는 오류를 만나면 카드를 아예 안 그린다. 링크가 잘못되었더라도 날짜
    미리보기는 뜨는 편이 낫다.
    """
    payload = _seed_cards_with_distinct_images(client.session_factory)

    for index in (0, 99):
        body = client.get(f"/api/og/{CARD_DATE}/{index}").text
        assert payload["meta"]["title"] in body
        assert f"/api/og/{CARD_DATE}/image.jpg" in body


def test_card_og_image_is_cached_per_card(client, tmp_path, monkeypatch) -> None:
    """카드마다 캐시 파일이 갈려야 한다. 한 파일을 나눠 쓰면 먼저 구운 카드의
    그림이 다른 카드 미리보기로 나간다."""
    _seed_cards_with_distinct_images(client.session_factory)
    client.app.dependency_overrides[get_settings] = lambda: Settings(og_cache_dir=str(tmp_path))

    def fake_get(url, **kwargs):
        fake_request = httpx.Request("GET", url)
        return httpx.Response(200, content=_fake_source_image_bytes(), request=fake_request)

    monkeypatch.setattr("app.routes.httpx.get", fake_get)

    assert client.get(f"/api/og/{CARD_DATE}/3/image.jpg").status_code == 200
    assert client.get(f"/api/og/{CARD_DATE}/5/image.jpg").status_code == 200

    assert (tmp_path / f"{CARD_DATE}-3.jpg").exists()
    assert (tmp_path / f"{CARD_DATE}-5.jpg").exists()


def test_card_og_404_for_unknown_date(client, tmp_path) -> None:
    client.app.dependency_overrides[get_settings] = lambda: Settings(og_cache_dir=str(tmp_path))

    assert client.get("/api/og/2000-01-01/1/image.jpg").status_code == 404
    assert client.get("/api/og/2000-01-01/1").status_code == 404
