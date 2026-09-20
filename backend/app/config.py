from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./ai_daily.db"
    # Empty means "no publishing key configured" — auth must fail closed, never
    # ship a usable default secret.
    admin_api_key: str = ""
    # 캐시 기본값은 **로컬 개발 기준**이다. 컨테이너에서는 docker-compose.yml 이
    # OG_CACHE_DIR/IMG_CACHE_DIR 를 /data/* 로 덮어쓴다(그쪽에 볼륨이 붙어 있다).
    # 기본값을 /data 로 두면 로컬에서 imgproxy 가 읽기 전용 파일시스템 오류로 죽는다.
    og_cache_dir: str = str(Path(__file__).resolve().parents[1] / ".cache" / "og")
    img_cache_dir: str = str(Path(__file__).resolve().parents[1] / ".cache" / "img")


@lru_cache
def get_settings() -> Settings:
    return Settings()
