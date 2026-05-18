"""뉴스 영상 Telegram 명령어 처리 스크립트.
Usage:
  python3 tg_video_cmd.py vadd <youtube_url>              ← pending 최근 1건 자동 매칭
  python3 tg_video_cmd.py vadd <slug|번호> <youtube_url>  ← 직접 지정
  python3 tg_video_cmd.py vreject [slug|번호]             ← 생략 시 pending 1건 자동
  python3 tg_video_cmd.py vdel [slug|번호]
  python3 tg_video_cmd.py vlist
  python3 tg_video_cmd.py vstatus
"""
import hashlib
import hmac
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).parent
STATE_FILE = BASE_DIR / ".vlist_state.json"

# YouTube URL 정규식: 11자 video ID 추출
_YT_RE = re.compile(
    r"^https?://"
    r"(?:(?:www\.|m\.)?youtube\.com/(?:watch\?v=|shorts/|live/|embed/)|youtu\.be/)"
    r"([A-Za-z0-9_-]{11})"
    r"(?:[&?#].*)?$"
)


def _extract_video_id(s: str) -> str | None:
    """유효한 YouTube URL이면 11자 video ID 반환, 아니면 None."""
    if not isinstance(s, str):
        return None
    m = _YT_RE.match(s.strip())
    return m.group(1) if m else None


def _get_secret() -> str:
    env_path = BASE_DIR / "config" / "sosig.env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("SOSIG_VIDEO_API_SECRET="):
                return line.split("=", 1)[1].strip()
    return ""


def _get_base_url() -> str:
    env_path = BASE_DIR / "config" / "sosig.env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("SOSIG_BASE_URL="):
                return line.split("=", 1)[1].strip().rstrip("/")
    return "http://100.123.228.20:5000"


def _http_error_msg(e: Exception, op: str) -> str:
    """urllib 예외를 사용자 친화 메시지로."""
    if isinstance(e, urllib.error.HTTPError):
        try:
            body = e.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            body = ""
        return f"❌ {op} 실패: HTTP {e.code} {e.reason}{(' / ' + body) if body else ''}"
    if isinstance(e, urllib.error.URLError):
        return f"❌ {op} 실패: 네트워크 오류 - {e.reason}"
    if isinstance(e, TimeoutError):
        return f"❌ {op} 실패: 타임아웃 (서버 응답 없음)"
    return f"❌ {op} 실패: {type(e).__name__}: {e}"


def _post(path: str, payload: dict) -> dict:
    secret = _get_secret()
    if not secret:
        raise RuntimeError("SOSIG_VIDEO_API_SECRET 미설정 (config/sosig.env 확인)")
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    req = urllib.request.Request(
        _get_base_url() + path,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "X-Signature": sig},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def _get(path: str) -> object:
    with urllib.request.urlopen(_get_base_url() + path, timeout=15) as r:
        return json.loads(r.read().decode())


def _is_youtube_url(s: str) -> bool:
    return _extract_video_id(s) is not None


def _get_yt_title(yt_url: str) -> str | None:
    """YouTube oEmbed로 영상 제목 조회 (API key 불필요). 실패 시 None."""
    try:
        from urllib.parse import quote
        req = urllib.request.Request(
            f"https://www.youtube.com/oembed?url={quote(yt_url, safe=':/?=&')}&format=json",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.loads(r.read().decode()).get("title")
    except Exception:
        return None


def _title_similarity(ko_title: str, yt_title: str) -> float:
    """ko_title 과 yt_title 유사도 (0.0~1.0). SequenceMatcher와 토큰 교집합 비율의 max."""
    from difflib import SequenceMatcher

    def norm(s: str) -> str:
        s = re.sub(r"[\[\]()【】「」『』#\-_,.!?\"'／/|·•:;]", " ", s.lower())
        return re.sub(r"\s+", " ", s).strip()

    a, b = norm(ko_title), norm(yt_title)
    if not a or not b:
        return 0.0
    seq = SequenceMatcher(None, a, b).ratio()
    ta, tb = set(a.split()), set(b.split())
    short = min(len(ta), len(tb)) or 1
    token_overlap = len(ta & tb) / short
    return max(seq, token_overlap)


def _get_pending() -> list:
    result = _post("/api/news_videos/pending_list", {"ts": int(time.time())})
    return result.get("items", [])


def _save_state(pending: list) -> None:
    state = {str(i + 1): p["slug"] for i, p in enumerate(pending)}
    STATE_FILE.write_text(json.dumps(state))


def _resolve_slug(arg: str) -> str:
    """번호면 state에서 slug 조회, 아니면 그대로 반환."""
    if arg.isdigit():
        if STATE_FILE.exists():
            state = json.loads(STATE_FILE.read_text())
            slug = state.get(arg)
            if slug:
                return slug
        print(f"⚠️ 번호 {arg} 를 찾을 수 없습니다. /vlist 먼저 실행하세요.")
        sys.exit(1)
    return arg


SIMILARITY_WARN_THRESHOLD = 0.25  # 한국어 ko_title vs YouTube 제목(괄호·해시태그·약어 차이)은 절대 100% 일치하지 않으므로 낮게 잡음


def _approve(slug: str, yt_url: str, ko_title: str | None = None) -> None:
    vid = _extract_video_id(yt_url)
    if not vid:
        print(f"❌ 잘못된 YouTube URL: {yt_url}")
        print("   허용 형식: https://youtu.be/<id>, https://youtube.com/watch?v=<id>, /shorts/<id>")
        return

    if ko_title:
        yt_title = _get_yt_title(yt_url)
        if yt_title is None:
            print("⚠️  YouTube 제목 조회 실패 (비공개·삭제·네트워크 오류) — 제목 비교 건너뜀")
        else:
            sim = _title_similarity(ko_title, yt_title)
            mark = "✅" if sim >= SIMILARITY_WARN_THRESHOLD else "⚠️ "
            print(f"{mark} 제목 유사도 {sim:.0%}")
            print(f"   저장: {ko_title[:60]}")
            print(f"   YouTube: {yt_title[:60]}")
            if sim < SIMILARITY_WARN_THRESHOLD:
                print(f"   ↳ 임계값({SIMILARITY_WARN_THRESHOLD:.0%}) 미달 — 매칭 실수 가능성. 잘못 매칭됐다면 /vreject {slug} 로 취소.")

    try:
        result = _post("/api/news_videos/approve", {
            "slug": slug, "youtube_url": yt_url, "ts": int(time.time())
        })
    except Exception as e:
        print(_http_error_msg(e, "승인"))
        return
    if result.get("ok"):
        print(f"✅ 승인 완료: {slug}\n   {yt_url}")
    else:
        print(f"❌ 서버 거절: {result.get('error', 'unknown')}")


def main():
    if len(sys.argv) < 2:
        print("Usage: tg_video_cmd.py <command> [args...]")
        sys.exit(1)

    cmd = sys.argv[1].lower().lstrip("/")
    args = sys.argv[2:]

    if cmd == "vadd":
        # URL을 args 어느 위치든 자동 감지
        url_args = [a for a in args if _is_youtube_url(a)]
        non_url_args = [a for a in args if not _is_youtube_url(a)]

        if not url_args:
            if args and any("youtu" in a for a in args):
                print("❌ 잘못된 YouTube URL 형식입니다.")
                print("   예: https://youtu.be/dQw4w9WgXcQ")
            else:
                print("사용법:")
                print("  /vadd <youtube_url>            ← pending 1건일 때만 자동 매칭")
                print("  /vadd <번호> <youtube_url>     ← 번호 지정 (URL 위치 무관)")
                print("  /vadd <slug> <youtube_url>     ← slug 직접")
            return

        if len(url_args) > 1:
            print("❌ URL을 2개 이상 지정했습니다. 한 번에 1개만 처리합니다.")
            return

        yt_url = url_args[0]

        if not non_url_args:
            # /vadd <url> → pending이 정확히 1건일 때만 자동 매칭
            try:
                pending = _get_pending()
            except Exception as e:
                print(_http_error_msg(e, "pending 조회"))
                return
            if not pending:
                print("❌ 대기 중인 영상이 없습니다.")
                return
            if len(pending) > 1:
                _save_state(pending)
                print(f"⚠️ 대기 영상이 {len(pending)}건입니다 — 번호로 명확히 지정하세요:")
                for i, p in enumerate(pending, 1):
                    print(f"  {i}. {p.get('ko_title','')[:40]}")
                print(f"\n예: /vadd 1 {yt_url}")
                return
            # 정확히 1건 → 자동 매칭 안전
            target = pending[0]
            print(f"📌 자동 매칭: {target.get('ko_title','')[:40]}")
            _approve(target["slug"], yt_url, ko_title=target.get("ko_title"))

        elif len(non_url_args) == 1:
            # /vadd <slug|번호> <url> 또는 <url> <slug|번호>
            slug = _resolve_slug(non_url_args[0])
            ko_title = None
            try:
                for p in _get_pending():
                    if p.get("slug") == slug:
                        ko_title = p.get("ko_title")
                        break
            except Exception:
                pass  # 조회 실패해도 승인 자체는 진행 (제목 비교만 건너뜀)
            _approve(slug, yt_url, ko_title=ko_title)

        else:
            print("❌ 인자가 너무 많습니다. /vadd <번호|slug> <url>")

    elif cmd in ("vreject", "vdel"):
        if not args:
            try:
                pending = _get_pending()
            except Exception as e:
                print(_http_error_msg(e, "pending 조회"))
                return
            if not pending:
                print("❌ 대기 중인 영상이 없습니다.")
                return
            if len(pending) > 1:
                _save_state(pending)
                print(f"⚠️ 대기 중인 영상이 {len(pending)}건입니다. 번호로 지정하세요:")
                for i, p in enumerate(pending, 1):
                    print(f"  {i}. {p.get('ko_title','')[:35]}")
                return
            slug = pending[0]["slug"]
        else:
            slug = _resolve_slug(args[0])

        try:
            result = _post("/api/news_videos/reject", {"slug": slug, "ts": int(time.time())})
        except Exception as e:
            print(_http_error_msg(e, "거부"))
            return
        if result.get("ok"):
            print(f"🗑 거부 완료: {slug}")
        else:
            print(f"❌ 서버 거절: {result.get('error', 'unknown')}")

    elif cmd == "vlist":
        try:
            pending = _get_pending()
            approved = _get("/api/news_videos.json")
        except Exception as e:
            print(_http_error_msg(e, "조회"))
            return

        if pending:
            _save_state(pending)
            print(f"📋 대기 중 ({len(pending)}건)")
            for i, p in enumerate(pending, 1):
                dt = p.get("created_at", "")
                print(f"  {i}. {p.get('ko_title','')[:35]}")
                print(f"     {dt}")
        else:
            print("대기 중인 영상 없음")

        if approved:
            print(f"\n✅ 승인됨 ({len(approved)}건)")
            for r in approved[:5]:
                print(f"  • {r['ko_title'][:35]}")

    elif cmd == "vstatus":
        try:
            pending = _get_pending()
            approved = _get("/api/news_videos.json")
        except Exception as e:
            print(_http_error_msg(e, "조회"))
            return
        print(f"대기: {len(pending)}건\n승인: {len(approved)}건")

    else:
        print(f"알 수 없는 명령어: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
