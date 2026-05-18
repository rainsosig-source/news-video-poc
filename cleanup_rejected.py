"""sosig.shop DB에서 rejected/deleted 영상의 NAS 파일 삭제.
크론: */5 * * * * (5분마다)
"""
import json
import os
import sys
import urllib.request
import hashlib
import hmac
import time
from pathlib import Path

os.chdir(Path(__file__).parent)
sys.path.insert(0, str(Path(__file__).parent))

from lib.sosig_client import _get_secret, _get_base_url


def fetch_pending_cleanup() -> list:
    """sosig.shop에서 삭제 대기 목록 조회."""
    secret = _get_secret()
    if not secret:
        return []

    ts = int(time.time())
    body = json.dumps({"ts": ts}).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    url = _get_base_url() + "/api/news_videos/pending_cleanup"
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Signature": f"sha256={sig}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode()).get("items", [])
    except Exception as e:
        print(f"[cleanup] 조회 실패: {e}")
        return []


def confirm_deleted(slug: str) -> None:
    """NAS 파일 삭제 완료를 sosig.shop에 알림."""
    secret = _get_secret()
    body = json.dumps({"slug": slug, "ts": int(time.time())}).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    url = _get_base_url() + "/api/news_videos/confirm_deleted"
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Signature": f"sha256={sig}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
    except Exception as e:
        print(f"[cleanup] 확인 전송 실패 ({slug}): {e}")


def main():
    items = fetch_pending_cleanup()
    if not items:
        return

    for item in items:
        slug = item.get("slug", "")
        ko_path = item.get("ko_path", "")
        en_path = item.get("en_path", "")

        deleted_any = False
        for path in (ko_path, en_path):
            if path and Path(path).exists():
                try:
                    Path(path).unlink()
                    print(f"[cleanup] 삭제: {path}")
                    deleted_any = True
                except Exception as e:
                    print(f"[cleanup] 삭제 실패 ({path}): {e}")
            elif path:
                deleted_any = True  # 이미 없음 = OK

        if deleted_any or (not ko_path and not en_path):
            confirm_deleted(slug)


if __name__ == "__main__":
    main()
