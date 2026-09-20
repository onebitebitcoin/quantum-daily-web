import io

import pytest
from PIL import Image

from app import imgproxy


def make_image_bytes(
    size: tuple[int, int] = (1600, 900), fmt: str = "JPEG", mode: str = "RGB"
) -> bytes:
    img = Image.new(mode, size, (200, 40, 40) if mode == "RGB" else (200, 40, 40, 128))
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


# ---- resolve_card_image_url ----


def test_resolve_returns_remote_url_for_matching_card() -> None:
    content = {"cards": [{"num": 3, "media": {"image": "https://cdn.example/a.jpg"}}]}

    assert imgproxy.resolve_card_image_url(content, 3) == "https://cdn.example/a.jpg"


def test_resolve_returns_none_for_bundled_stem() -> None:
    """번들 asset을 가리키는 stem은 프록시 대상이 아니다."""
    content = {"cards": [{"num": 1, "media": {"image": "fed-macro"}}]}

    assert imgproxy.resolve_card_image_url(content, 1) is None


def test_resolve_returns_none_when_card_has_no_media() -> None:
    content = {"cards": [{"num": 8, "media": None}]}

    assert imgproxy.resolve_card_image_url(content, 8) is None


def test_resolve_returns_none_for_unknown_card_number() -> None:
    content = {"cards": [{"num": 1, "media": {"image": "https://cdn.example/a.jpg"}}]}

    assert imgproxy.resolve_card_image_url(content, 99) is None


# ---- normalize_width ----


@pytest.mark.parametrize(
    ("requested", "expected"),
    [(None, 800), (800, 800), (480, 480), (500, 480), (700, 800), (4000, 800), (1, 480)],
)
def test_normalize_width_snaps_to_allowed_set(requested: int | None, expected: int) -> None:
    assert imgproxy.normalize_width(requested) == expected


# ---- cache_path ----


def test_cache_path_changes_when_source_url_changes() -> None:
    """재발행으로 이미지가 바뀌면 캐시 키도 바뀌어야 낡은 그림이 안 남는다."""
    first = imgproxy.cache_path("/tmp/img", "2026-08-04", 3, 800, "https://cdn.example/a.jpg")
    second = imgproxy.cache_path("/tmp/img", "2026-08-04", 3, 800, "https://cdn.example/b.jpg")

    assert first != second


def test_cache_path_is_stable_for_same_inputs() -> None:
    args = ("/tmp/img", "2026-08-04", 3, 800, "https://cdn.example/a.jpg")

    assert imgproxy.cache_path(*args) == imgproxy.cache_path(*args)


def test_cache_path_separates_widths() -> None:
    wide = imgproxy.cache_path("/tmp/img", "2026-08-04", 3, 800, "https://cdn.example/a.jpg")
    narrow = imgproxy.cache_path("/tmp/img", "2026-08-04", 3, 480, "https://cdn.example/a.jpg")

    assert wide != narrow


# ---- to_webp ----


def test_to_webp_resizes_down_and_shrinks_payload() -> None:
    raw = make_image_bytes((1600, 900))

    out = imgproxy.to_webp(raw, 800)

    assert Image.open(io.BytesIO(out)).size == (800, 450)
    assert Image.open(io.BytesIO(out)).format == "WEBP"


def test_to_webp_does_not_upscale_narrow_source() -> None:
    raw = make_image_bytes((320, 180))

    out = imgproxy.to_webp(raw, 800)

    assert Image.open(io.BytesIO(out)).size == (320, 180)


def test_to_webp_composites_transparency_over_white_not_black() -> None:
    img = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")

    out = imgproxy.to_webp(buf.getvalue(), 800)

    assert Image.open(io.BytesIO(out)).convert("RGB").getpixel((50, 50)) == (255, 255, 255)


def test_to_webp_rejects_oversized_pixel_count(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(imgproxy, "MAX_SOURCE_PIXELS", 1000)
    raw = make_image_bytes((1600, 900))

    with pytest.raises(imgproxy.ImageTooLargeError):
        imgproxy.to_webp(raw, 800)


def test_to_webp_rejects_non_image_bytes() -> None:
    with pytest.raises(OSError):
        imgproxy.to_webp(b"this is not an image", 800)


# ---- write_cache_atomically ----


def test_write_cache_creates_parents_and_leaves_no_tmp_file(tmp_path) -> None:
    target = tmp_path / "nested" / "deeper" / "out.webp"

    imgproxy.write_cache_atomically(target, b"payload")

    assert target.read_bytes() == b"payload"
    assert list(target.parent.glob("*.tmp")) == []


# ---- fetch_source 요청 헤더 ----


class _FakeStream:
    """httpx.stream 의 컨텍스트 매니저 자리를 대신한다."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self.headers: dict[str, str] = {}

    def __enter__(self) -> "_FakeStream":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    def iter_bytes(self):  # noqa: ANN201 - httpx 인터페이스를 그대로 흉내 낸다
        yield self._payload


def test_fetch_source_presents_a_browser_user_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """일부 매체가 기본 UA(python-httpx/x.y)를 403으로 막는다.

    2026-09-20 실측: thequantuminsider.com 이 그렇게 막아 카드 이미지가 502로 깨졌다.
    같은 URL도 브라우저 UA면 200을 준다. 수집 단계는 이미 같은 UA를 쓴다.
    """
    captured: dict[str, object] = {}

    def fake_stream(method: str, url: str, **kwargs: object) -> _FakeStream:
        captured["method"] = method
        captured["url"] = url
        captured["headers"] = kwargs.get("headers")
        return _FakeStream(b"payload")

    monkeypatch.setattr(imgproxy.httpx, "stream", fake_stream)

    assert imgproxy.fetch_source("https://cdn.example/a.jpg") == b"payload"

    headers = captured["headers"]
    assert headers is not None, "fetch_source 가 헤더를 보내지 않는다"
    assert "Mozilla/5.0" in headers["User-Agent"]
    assert "python-httpx" not in headers["User-Agent"]


def test_fetch_source_accepts_images(monkeypatch: pytest.MonkeyPatch) -> None:
    """Accept 가 */* 뿐이면 이미지 대신 HTML 안내 페이지를 주는 CDN이 있다."""
    captured: dict[str, object] = {}

    def fake_stream(method: str, url: str, **kwargs: object) -> _FakeStream:
        captured["headers"] = kwargs.get("headers")
        return _FakeStream(b"payload")

    monkeypatch.setattr(imgproxy.httpx, "stream", fake_stream)
    imgproxy.fetch_source("https://cdn.example/a.jpg")

    assert "image/" in captured["headers"]["Accept"]
