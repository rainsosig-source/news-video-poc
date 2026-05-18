"""자동 뉴스 영상 생성 오케스트레이터.

Google News RSS → Claude 기사 선택 → 본문 수집 → 대화 대본 생성 →
poc.py 파이프라인 실행 → Gmail 이메일 발송

크론: 0 9-21/2 * * *  (9시~21시, 2시간마다)
"""
import fcntl
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# 작업 디렉토리 고정
os.chdir(Path(__file__).parent)
sys.path.insert(0, str(Path(__file__).parent))

from lib import auto_state, news_collector, news_selector, article_fetcher, dialogue_gen, email_sender, sosig_client

NAS_OUT   = "/mnt/nas/data2/mov/news_video_poc"
WORK_BASE = "work"
LOG_DIR   = "logs"
LOCKFILE  = "/tmp/news_video_poc_run_auto.lock"

# 영어 영상 생성 여부 — 한글 품질 안정화 후 True로 전환 (또는 SKIP_EN=0 환경변수)
INCLUDE_EN = os.environ.get("SKIP_EN", "1") == "0"


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")


def _slug_from_title(title: str) -> str:
    import re, unicodedata
    # 한글/영숫자만 남기고 공백→언더스코어
    s = unicodedata.normalize("NFC", title)
    s = re.sub(r"[^\w\s가-힣]", "", s)
    s = re.sub(r"\s+", "_", s.strip())
    return s[:40] or "news"


def _log(msg: str, log_path: Path) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _notify_failure(stage: str, error: str, log_path: Path) -> None:
    """실패 시 이메일 알림 (예외 발생해도 main 흐름에 영향 없음)."""
    try:
        log_tail = ""
        if log_path.exists():
            try:
                lines = log_path.read_text(encoding="utf-8").splitlines()
                log_tail = "\n".join(lines[-40:])
            except Exception:
                pass
        body = (
            f"뉴스 영상 자동 생성 실패\n"
            f"시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"단계: {stage}\n"
            f"오류: {error}\n\n"
            f"--- 로그 마지막 40줄 ---\n{log_tail}"
        )
        email_sender.send(
            subject=f"[news_video_poc] 실패: {stage}",
            body=body,
            attachments=[],
        )
        _log(f"  ✉️ 실패 알림 이메일 발송: {stage}", log_path)
    except Exception as e:
        _log(f"  ! 실패 알림 발송 자체 오류 (무시): {e}", log_path)


def run() -> int:
    """0 = 성공, 1 = 스킵, 2 = 오류."""
    Path(LOG_DIR).mkdir(exist_ok=True)
    now = _now_str()
    log_path = Path(LOG_DIR) / f"auto_{now}.log"

    _log(f"=== 자동 영상 생성 시작 ({now}) ===", log_path)

    # ── Step 1: 뉴스 수집 ─────────────────────────────────────
    _log("[Step 1] Google News RSS 수집 (12h)...", log_path)
    try:
        articles = news_collector.collect(hours=12)
        _log(f"  수집: {len(articles)}건", log_path)
    except Exception as e:
        _log(f"  오류: {e}", log_path)
        return 2

    if not articles:
        _log("  후보 없음 — 스킵", log_path)
        auto_state.record_run("", now, skipped=True, skip_reason="후보 없음")
        return 1

    # ── Step 2: 기사 선택 ─────────────────────────────────────
    _log("[Step 2] Claude 기사 선택...", log_path)
    try:
        covered = auto_state.get_covered_titles(hours=24)
        decision = news_selector.select(articles, already_covered=covered)
        _log(f"  결정: skip={decision['skip']}  reason={decision['reason']}", log_path)
    except Exception as e:
        _log(f"  오류: {e}", log_path)
        return 2

    if decision["skip"]:
        auto_state.record_run("", now, skipped=True, skip_reason=decision["reason"])
        _log("  → 이번 회차 스킵", log_path)
        return 1

    article = decision["article"]
    _log(f"  선택: [{article['source']}] {article['title']}", log_path)

    # ── Step 3: 본문 수집 ─────────────────────────────────────
    _log("[Step 3] 기사 본문 수집...", log_path)
    try:
        article_text = article_fetcher.fetch(article)
        _log(f"  본문: {len(article_text)}자", log_path)
    except Exception as e:
        _log(f"  경고: 본문 수집 실패 ({e}), summary 사용", log_path)
        article_text = article.get("summary", article["title"])

    # ── Step 4: 대화 대본 생성 ────────────────────────────────
    _log("[Step 4] 대화 대본 생성 (Claude sonnet)...", log_path)
    slug = _slug_from_title(article["title"]) + "_" + now
    work_dir = Path("scripts") / slug
    work_dir.mkdir(parents=True, exist_ok=True)

    script_path = work_dir / "script_dialogue.md"
    try:
        dialogue = dialogue_gen.generate(article, article_text)
        script_path.write_text(dialogue, encoding="utf-8")
        _log(f"  대본 저장: {script_path}", log_path)
    except Exception as e:
        _log(f"  오류: {e}", log_path)
        auto_state.record_run(article["title"], slug, skipped=True, skip_reason=f"대본생성실패: {e}")
        return 2

    # ── Step 5: poc.py 파이프라인 실행 ───────────────────────
    ko_out = f"{NAS_OUT}/ko/{slug}_ko.mp4"
    en_out = f"{NAS_OUT}/en/{slug}_en.mp4"

    _log("[Step 5] poc.py 파이프라인 실행...", log_path)

    # work/ 디렉토리를 슬러그별로 격리 (이전 캐시 제거)
    main_work = Path(WORK_BASE)
    backup_work = Path(WORK_BASE + "_backup_" + now)

    # 이전 work 백업 (있으면)
    if main_work.exists():
        shutil.move(str(main_work), str(backup_work))

    # 새 work 디렉토리 생성 후 대본 복사 (script_path는 work/ 밖이므로 안전)
    main_work.mkdir(exist_ok=True)
    shutil.copy(str(script_path), str(main_work / "script_dialogue.md"))

    # 기사 본문 저장 — prompt_gen 컨텍스트로 사용
    article_body_path = main_work / "article_body.txt"
    article_body_path.write_text(article_text, encoding="utf-8")

    poc_cmd = [
        sys.executable, "poc.py",
        "--script", str(main_work / "script_dialogue.md"),
        "--ko-out", ko_out,
        "--en-out", en_out,
        "--source", article.get("source", ""),
        "--article-body", str(article_body_path),
        "--no-cache",
    ]
    if not INCLUDE_EN:
        poc_cmd.append("--skip-en")

    t0 = time.time()
    poc_ok = False
    try:
        result = subprocess.run(
            poc_cmd,
            capture_output=False,  # 로그 직접 출력
            timeout=10800,         # 3시간 타임아웃 (Flux Dev 여유)
        )
        elapsed = time.time() - t0
        _log(f"  poc.py 완료: {elapsed:.0f}초, returncode={result.returncode}", log_path)
        poc_ok = result.returncode == 0

    except Exception as e:
        elapsed = time.time() - t0
        _log(f"  오류: {e} ({elapsed:.0f}초)", log_path)

    finally:
        # work 디렉토리를 slug 폴더로 저장 후 백업 복원 (항상)
        dest_name = "work_done" if poc_ok else "work_failed"
        try:
            if main_work.exists():
                dest = work_dir / dest_name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(main_work), str(dest))
        except Exception:
            pass
        if backup_work.exists():
            try:
                shutil.move(str(backup_work), str(main_work))
            except Exception:
                pass

    if not poc_ok:
        auto_state.record_run(article["title"], slug, skipped=True, skip_reason="poc실패")
        _notify_failure(
            stage="poc.py 파이프라인",
            error=f"slug={slug}, elapsed={elapsed:.0f}s, 기사={article.get('title','')[:80]}",
            log_path=log_path,
        )
        return 2

    # ── Step 6: 제목 읽기 ────────────────────────────────────
    try:
        ko_title = (work_dir / "work_done" / "title_ko.txt").read_text().strip()
        if INCLUDE_EN:
            en_title = (work_dir / "work_done" / "title_en.txt").read_text().strip()
        else:
            en_title = ko_title  # placeholder (sosig 등록 시 동일 사용)
    except Exception:
        ko_title = article["title"]
        en_title = article["title"]

    ko_meta = str(Path(ko_out).with_suffix(".youtube.txt"))
    en_meta = str(Path(en_out).with_suffix(".youtube.txt"))

    # ── Step 6.5: sosig.shop 등록 ────────────────────────────
    _log("[Step 6.5] sosig.shop 영상 등록...", log_path)
    try:
        sosig_client.register(
            slug=slug,
            ko_title=ko_title,
            en_title=en_title,
            ko_path=ko_out,
            en_path=en_out,
            article_title=article["title"],
            article_source=article.get("source", ""),
            reason=decision.get("reason", ""),
        )
    except Exception as e:
        _log(f"  sosig 등록 오류 (계속 진행): {e}", log_path)

    # ── Step 7: 이메일 발송 ──────────────────────────────────
    _log("[Step 7] 이메일 발송...", log_path)
    try:
        email_sender.send_video_notification(
            slug=slug,
            ko_path=ko_out,
            en_path=en_out,
            ko_title=ko_title,
            en_title=en_title,
            article_title=article["title"],
            article_source=article.get("source", ""),
            reason=decision.get("reason", ""),
            ko_meta_path=ko_meta,
            en_meta_path=en_meta,
        )
    except Exception as e:
        _log(f"  이메일 오류: {e}", log_path)

    # ── Step 8: 상태 기록 ────────────────────────────────────
    auto_state.record_run(
        title=article["title"],
        slug=slug,
        skipped=False,
        ko_path=ko_out,
        en_path=en_out,
    )

    _log(f"=== 완료: {ko_title} ===", log_path)
    return 0


if __name__ == "__main__":
    # 이중 실행 방지: flock 비차단 획득 실패 시 즉시 종료
    lock_fp = open(LOCKFILE, "w")
    try:
        fcntl.flock(lock_fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(f"[run_auto] 이미 실행 중 — 이번 호출 스킵 ({datetime.now().strftime('%F %T')})")
        sys.exit(0)

    try:
        sys.exit(run())
    except Exception as e:
        import traceback
        log_path = Path(LOG_DIR) / f"auto_{_now_str()}.log"
        _notify_failure(
            stage="unhandled exception",
            error=f"{type(e).__name__}: {e}\n{traceback.format_exc()[:1500]}",
            log_path=log_path,
        )
        raise
    finally:
        try:
            fcntl.flock(lock_fp.fileno(), fcntl.LOCK_UN)
            lock_fp.close()
        except Exception:
            pass
