"""Google News RSS 수집: 2시간 윈도우 + 화이트리스트 필터 + fuzzy 중복 제거."""
import hashlib
import time
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

import feedparser

RSS_URLS = [
    "https://news.google.com/rss?hl=ko&gl=KR&ceid=KR:ko",
    "https://news.google.com/rss/topics/CAAqJggKIiBDQkFTRWdvSUwyMHZNRGx6TVdZU0FtdHZHZ0pMVWlnQVAB?hl=ko&gl=KR&ceid=KR:ko",  # 비즈니스
    "https://www.mk.co.kr/rss/30000001/",   # 매일경제 경제
    "https://www.yna.co.kr/rss/economy.xml", # 연합뉴스 경제
]
RSS_URL = RSS_URLS[0]  # 하위 호환

WHITELIST = [
    "연합뉴스", "SBS", "KBS", "MBC", "한겨레", "조선일보", "중앙일보",
    "동아일보", "매일경제", "한국경제", "이데일리",
    "연합", "조선", "중앙", "동아", "매경", "한경",
]

EXCLUDE_KEYWORDS = [
    "연예", "스포츠", "사건", "사고", "드라마", "영화", "배우", "아이돌",
    "K팝", "K-팝", "야구", "축구", "농구", "골프", "올림픽",
    "배드민턴", "테니스", "수영", "육상", "체조", "태권도", "씨름",
    "e스포츠", "리그오브레전드", "롤챔스", "우버컵", "토마스컵",
    "범죄", "살인", "폭행", "성범죄", "화재", "교통사고",
    "노래", "앨범", "콘서트", "팬미팅", "뮤직비디오",
]


def _item_id(title: str) -> str:
    return hashlib.md5(title.encode("utf-8")).hexdigest()[:12]


def _pub_dt(entry) -> datetime:
    try:
        dt = parsedate_to_datetime(entry.published)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return datetime.now(timezone.utc)


def _source_name(entry) -> str:
    if hasattr(entry, "source") and hasattr(entry.source, "title"):
        return entry.source.title
    title = entry.get("title", "")
    if " - " in title:
        return title.rsplit(" - ", 1)[-1]
    return ""


def _is_whitelisted(source: str) -> bool:
    return any(w in source for w in WHITELIST)


def _is_excluded(title: str, summary: str = "") -> bool:
    text = title + " " + summary
    return any(kw in text for kw in EXCLUDE_KEYWORDS)


def _fuzzy_dedupe(items: list[dict], threshold: int = 85) -> list[dict]:
    try:
        from rapidfuzz import fuzz
        seen_titles = []
        result = []
        for item in items:
            t = item["title"]
            duplicate = any(fuzz.ratio(t, s) >= threshold for s in seen_titles)
            if not duplicate:
                seen_titles.append(t)
                result.append(item)
        return result
    except ImportError:
        # rapidfuzz 없으면 정확 중복만 제거
        seen = set()
        result = []
        for item in items:
            if item["title"] not in seen:
                seen.add(item["title"])
                result.append(item)
        return result


def collect(hours: float = 2.0, rss_url: str = None) -> list[dict]:
    """여러 RSS 피드에서 최근 hours 시간 내 기사를 수집해 반환.

    Returns list of dicts: {id, title, summary, source, link, pub_date, score_hint}
    score_hint: whitelist 포함 여부 가중치 (1.2 = whitelist, 1.0 = 기타)
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    urls = [rss_url] if rss_url else RSS_URLS

    items = []
    for url in urls:
        try:
            feed = feedparser.parse(url)
        except Exception:
            continue
        for entry in feed.entries:
            pub_dt = _pub_dt(entry)
            if pub_dt < cutoff:
                continue

            title = entry.get("title", "").strip()
            if not title:
                continue

            # Google News는 제목에 " - 출처" 붙음 → 분리
            clean_title = title
            source = _source_name(entry)
            if source and clean_title.endswith(f" - {source}"):
                clean_title = clean_title[: -(len(source) + 3)].strip()

            summary = entry.get("summary", "").strip()
            link = entry.get("link", "").strip()

            if _is_excluded(clean_title, summary):
                continue

            score_hint = 1.2 if _is_whitelisted(source) else 1.0

            items.append({
                "id": _item_id(clean_title),
                "title": clean_title,
                "summary": summary,
                "source": source,
                "link": link,
                "pub_date": pub_dt.isoformat(),
                "score_hint": score_hint,
            })

    items = _fuzzy_dedupe(items)
    # score_hint 높은 것 먼저 (화이트리스트 우선)
    items.sort(key=lambda x: (-x["score_hint"], x["pub_date"]))
    return items


if __name__ == "__main__":
    import json
    articles = collect(hours=4)
    print(f"수집: {len(articles)}건\n")
    for a in articles[:10]:
        print(f"[{a['source']}] {a['title']}")
        print(f"  {a['pub_date']}  score={a['score_hint']}")
        print(f"  {a['link'][:80]}\n")
