"""기사 본문 추출: Google News 리다이렉트 따라가기 + trafilatura."""
import re
import urllib.parse
import urllib.request
from pathlib import Path

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}
TIMEOUT = 15
MAX_CHARS = 8000


def _follow_redirect(url: str) -> str:
    """Google News 단축 URL → 실제 기사 URL 반환."""
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        opener = urllib.request.build_opener(urllib.request.HTTPRedirectHandler())
        resp = opener.open(req, timeout=TIMEOUT)
        return resp.geturl()
    except Exception:
        return url


def _extract_trafilatura(url: str) -> str:
    """trafilatura로 본문 추출."""
    try:
        import trafilatura
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            return ""
        text = trafilatura.extract(downloaded, include_comments=False,
                                    include_tables=False, no_fallback=False)
        return text or ""
    except ImportError:
        return ""


def _extract_bs4(html: bytes) -> str:
    """trafilatura 없을 때 BeautifulSoup 폴백."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer",
                          "aside", "form", "noscript"]):
            tag.decompose()
        # 본문 후보 태그
        for sel in ["article", "main", ".article-body", "#article-body",
                    ".news-article", ".content"]:
            el = soup.select_one(sel)
            if el:
                return el.get_text(separator="\n", strip=True)
        return soup.get_text(separator="\n", strip=True)
    except ImportError:
        return ""


def fetch(article: dict) -> str:
    """기사 URL에서 본문 텍스트를 반환. 실패하면 summary 반환."""
    url = article.get("link", "")
    title = article.get("title", "")
    summary = article.get("summary", "")

    if not url:
        return f"{title}\n\n{summary}"

    # Google News 리다이렉트 따라가기
    real_url = _follow_redirect(url)

    # trafilatura 시도
    text = _extract_trafilatura(real_url)

    # 폴백: requests + bs4
    if not text:
        try:
            import urllib.request as req
            r = req.urlopen(
                req.Request(real_url, headers=HEADERS), timeout=TIMEOUT
            )
            html = r.read()
            text = _extract_bs4(html)
        except Exception:
            pass

    if not text:
        return f"{title}\n\n{summary}"

    # 너무 긴 경우 자르기
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "\n...(이하 생략)"

    return text.strip()


if __name__ == "__main__":
    import sys
    test_url = sys.argv[1] if len(sys.argv) > 1 else ""
    if test_url:
        article = {"title": "테스트", "link": test_url, "summary": ""}
        print(fetch(article)[:1000])
