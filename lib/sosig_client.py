"""DGX → sosig.shop 뉴스 영상 등록 API 클라이언트 (HMAC-SHA256 인증)."""
import hashlib
import hmac
import json
import os
import time
import urllib.request
from pathlib import Path


def _get_secret() -> str:
    env_path = Path(__file__).parent.parent / "config" / "sosig.env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line.startswith("SOSIG_VIDEO_API_SECRET="):
                return line.split("=", 1)[1].strip()
    return os.environ.get("SOSIG_VIDEO_API_SECRET", "")


def _get_base_url() -> str:
    env_path = Path(__file__).parent.parent / "config" / "sosig.env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line.startswith("SOSIG_BASE_URL="):
                return line.split("=", 1)[1].strip().rstrip("/")
    return os.environ.get("SOSIG_BASE_URL", "http://100.123.228.20:5000")


def _sign(payload_bytes: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()


def register(
    slug: str,
    ko_title: str,
    en_title: str,
    ko_path: str,
    en_path: str,
    article_title: str,
    article_source: str,
    reason: str,
) -> dict:
    """sosig.shop에 뉴스 영상 등록. 성공 시 {"ok": True} 반환."""
    secret = _get_secret()
    if not secret:
        raise RuntimeError("SOSIG_VIDEO_API_SECRET 없음. config/sosig.env 확인.")

    payload = {
        "slug": slug,
        "ko_title": ko_title,
        "en_title": en_title,
        "ko_path": ko_path,
        "en_path": en_path,
        "article_title": article_title,
        "article_source": article_source,
        "reason": reason,
        "ts": int(time.time()),
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    sig = _sign(body, secret)

    url = _get_base_url() + "/api/news_videos"
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "X-Signature": f"sha256={sig}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())
            print(f"  [sosig] 등록 완료: {slug}")
            return result
    except Exception as e:
        print(f"  [sosig] 등록 실패: {e}")
        raise
