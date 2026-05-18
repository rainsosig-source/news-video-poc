"""뉴스 슬라이드쇼 영상 PoC — 메인 엔트리.

Usage:
    python poc.py --script /mnt/nas/data2/news/podcast_script_gas_price_2026.md
"""
import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

# 작업 디렉토리 고정
os.chdir(Path(__file__).parent)
sys.path.insert(0, str(Path(__file__).parent))

from concurrent.futures import ThreadPoolExecutor

from lib import parser, transform, tts, align, prompt_gen, image_gen, compose, meta_gen, title_gen, glossary_gen
from lib.gpu_helper import free_gpu_for_heavy_work

NAS_OUT = "/mnt/nas/data2/mov/news_video_poc"
WORK    = "work"


def _slug(script_path: str) -> str:
    return Path(script_path).stem.replace("podcast_script_", "")


def _timer(label: str, func, *args, **kwargs):
    t0 = time.time()
    result = func(*args, **kwargs)
    elapsed = time.time() - t0
    print(f"  ✓ {label} 완료 ({elapsed:.0f}초)\n")
    return result, elapsed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", default="work/script_dialogue.md")
    ap.add_argument("--ko-out", default=None, help="KO 영상 출력 경로 (기본: NAS)")
    ap.add_argument("--en-out", default=None, help="EN 영상 출력 경로 (기본: NAS)")
    ap.add_argument("--no-cache", action="store_true", help="캐시 무시, 처음부터 재생성")
    ap.add_argument("--source", default="", help="기사 출처 (워터마크용)")
    ap.add_argument("--article-body", default="", help="원본 기사 본문 파일 (이미지 프롬프트 컨텍스트)")
    ap.add_argument("--skip-en", action="store_true", help="영어 영상 생성 스킵 (KO만)")
    args = ap.parse_args()

    slug = _slug(args.script)
    timings = {}

    print(f"\n{'='*60}")
    print(f" 뉴스 영상 PoC: {slug}")
    print(f"{'='*60}\n")

    # ── Phase 1: 파서 ──────────────────────────────────────────
    print("[Phase 1] 대본 파싱...")
    utterances = parser.parse(args.script)
    print(f"  {len(utterances)}개 발화 파싱 완료\n")

    # 원본 복사 (읽기 전용 원칙)
    if Path(args.script).resolve() != Path(f"{WORK}/script_dialogue.md").resolve():
        shutil.copy(args.script, f"{WORK}/script_dialogue.md")

    # ── Phase 2: 대본 변환 ────────────────────────────────────
    if args.skip_en:
        print("[Phase 2] 대본 변환 (한국어 narration만)...")
    else:
        print("[Phase 2] 대본 변환 (한국어 분석 narration + 영어 현지화)...")
    ko_path = f"{WORK}/script_narration_ko.md"
    en_path = f"{WORK}/script_narration_en.md"

    if args.skip_en:
        if not args.no_cache and Path(ko_path).exists():
            print("  캐시된 KO 대본 사용\n")
            ko_text = Path(ko_path).read_text()
        else:
            t0 = time.time()
            dialogue_text = Path(f"{WORK}/script_dialogue.md").read_text(encoding="utf-8")
            ko_text = transform.dialogue_to_ko(dialogue_text)
            Path(ko_path).write_text(ko_text, encoding="utf-8")
            timings["transform"] = time.time() - t0
            print(f"  ✓ 대본 변환 (KO만) 완료 ({timings['transform']:.0f}초)\n")
        en_text = ""
    else:
        if not args.no_cache and Path(ko_path).exists() and Path(en_path).exists():
            print("  캐시된 대본 사용\n")
            ko_text = Path(ko_path).read_text()
            en_text = Path(en_path).read_text()
        else:
            (ko_text, en_text), t = _timer("대본 변환", transform.run,
                                            f"{WORK}/script_dialogue.md", ko_path, en_path)
            timings["transform"] = t

    ko_paras = len([p for p in ko_text.split("\n\n") if p.strip()])
    if args.skip_en:
        print(f"  KO {ko_paras}단락 확인 (EN 스킵)\n")
    else:
        en_paras = len([p for p in en_text.split("\n\n") if p.strip()])
        print(f"  KO {ko_paras}단락, EN {en_paras}단락 확인\n")

    # ── Phase 3: TTS ────────────────────────────────────────────
    if args.skip_en:
        print("[Phase 3] 음성 생성 (KO만)...")
    else:
        print("[Phase 3] 음성 생성 (KO/EN 병렬)...")

    ko_scenes_path = f"{WORK}/ko/scenes.json"
    en_scenes_path = f"{WORK}/en/scenes.json"

    def _run_ko():
        if not args.no_cache and Path(f"{WORK}/ko/audio.wav").exists():
            print("  [KO] 캐시 사용")
            return json.loads(Path(ko_scenes_path).read_text()) if Path(ko_scenes_path).exists() else [], 0.0
        t0 = time.time()
        print("  [KO] Edge TTS 생성 중...")
        segs = tts.generate_ko(ko_path, f"{WORK}/ko")
        Path(ko_scenes_path).write_text(json.dumps(segs, ensure_ascii=False, indent=2))
        return segs, time.time() - t0

    def _run_en():
        if not args.no_cache and Path(f"{WORK}/en/audio.wav").exists():
            print("  [EN] 캐시 사용")
            return json.loads(Path(en_scenes_path).read_text()) if Path(en_scenes_path).exists() else [], 0.0
        t0 = time.time()
        print("  [EN] Kokoro 생성 중...")
        segs = tts.generate_en(en_path, f"{WORK}/en")
        Path(en_scenes_path).write_text(json.dumps(segs, ensure_ascii=False, indent=2))
        return segs, time.time() - t0

    if args.skip_en:
        ko_segments, t_ko = _run_ko()
        en_segments, t_en = [], 0.0
    else:
        with ThreadPoolExecutor(max_workers=2) as ex:
            fut_ko = ex.submit(_run_ko)
            fut_en = ex.submit(_run_en)
            ko_segments, t_ko = fut_ko.result()
            en_segments, t_en = fut_en.result()
    if t_ko: timings["tts_ko"] = t_ko
    if t_en: timings["tts_en"] = t_en
    if args.skip_en:
        print(f"  ✓ KO {t_ko:.0f}s\n")
    else:
        print(f"  ✓ KO {t_ko:.0f}s / EN {t_en:.0f}s (병렬 wall-clock {max(t_ko,t_en):.0f}s)\n")

    # ── Phase 4: 자막 (TTS 문장 타임스탬프 기반, ASS dual-line) ──────────────
    print("[Phase 4] 자막 생성 (ASS 2줄: 현재+다음 미리보기)...")

    ko_ass = f"{WORK}/ko/subtitles.ass"
    en_ass = f"{WORK}/en/subtitles.ass"

    if args.no_cache or not Path(ko_ass).exists():
        _, t = _timer("KO 자막", align.generate_ass_dual_line, ko_segments, ko_ass, "ko")
        timings["align_ko"] = t
    else:
        print("  [KO] 캐시된 자막 사용")

    if not args.skip_en:
        if args.no_cache or not Path(en_ass).exists():
            _, t = _timer("EN 자막", align.generate_ass_dual_line, en_segments, en_ass, "en")
            timings["align_en"] = t
        else:
            print("  [EN] 캐시된 자막 사용")
    print()

    # ── Phase 5: 이미지 프롬프트 ──────────────────────────────
    print("[Phase 5] 이미지 프롬프트 생성...")
    prompts_path = f"{WORK}/prompts.json"

    if not args.no_cache and Path(prompts_path).exists():
        print("  캐시된 프롬프트 사용")
        prompts = json.loads(Path(prompts_path).read_text())
    else:
        article_body = ""
        if args.article_body and Path(args.article_body).exists():
            article_body = Path(args.article_body).read_text(encoding="utf-8")
        prompts, t = _timer("프롬프트 생성", prompt_gen.generate, ko_path, prompts_path, article_body)
        timings["prompts"] = t

    # ── Phase 5.5: 어려운 용어 해설 박스 ──────────────────────
    print("[Phase 5.5] 경제 용어 해설 박스 생성 (KO/EN 병렬)...")
    ko_gloss_json = f"{WORK}/ko/glossary.json"
    en_gloss_json = f"{WORK}/en/glossary.json"
    ko_gloss_ass = f"{WORK}/ko/glossary.ass"
    en_gloss_ass = f"{WORK}/en/glossary.ass"

    def _gloss_ko():
        if (not args.no_cache and Path(ko_gloss_json).exists()
                and Path(ko_gloss_ass).exists()):
            print("  [KO] 캐시된 용어 박스 사용")
            return
        try:
            g = glossary_gen.generate(ko_path, ko_segments, "ko", ko_gloss_json)
            glossary_gen.to_ass(g, ko_gloss_ass, "ko")
        except Exception as e:
            print(f"  [KO] 용어 박스 생성 실패 (스킵): {e}")
            glossary_gen.to_ass([], ko_gloss_ass, "ko")

    def _gloss_en():
        if (not args.no_cache and Path(en_gloss_json).exists()
                and Path(en_gloss_ass).exists()):
            print("  [EN] 캐시된 용어 박스 사용")
            return
        try:
            g = glossary_gen.generate(en_path, en_segments, "en", en_gloss_json)
            glossary_gen.to_ass(g, en_gloss_ass, "en")
        except Exception as e:
            print(f"  [EN] 용어 박스 생성 실패 (스킵): {e}")
            glossary_gen.to_ass([], en_gloss_ass, "en")

    t0 = time.time()
    if args.skip_en:
        _gloss_ko()
    else:
        with ThreadPoolExecutor(max_workers=2) as ex:
            fut_ko = ex.submit(_gloss_ko)
            fut_en = ex.submit(_gloss_en)
            fut_ko.result()
            fut_en.result()
    timings["glossary"] = time.time() - t0
    print(f"  ✓ 용어 박스 ({timings['glossary']:.0f}초)\n")

    # ── Phase 6: 이미지 생성 ─────────────────────────────────
    print("[Phase 6] Flux 이미지 생성 (단락별 고유, 한/영 공유)...")
    free_gpu_for_heavy_work()  # ollama 모델 언로드 → NVRM OOM 방지
    prompts, t = _timer("이미지 생성", image_gen.generate, prompts, f"{WORK}/images")
    timings["images"] = t

    # ── Phase 6.5: 타이틀 ────────────────────────────────────
    print("[Phase 6.5] 영상 제목 생성...")
    title_ko_path = f"{WORK}/title_ko.txt"
    title_en_path = f"{WORK}/title_en.txt"
    if args.skip_en:
        if not args.no_cache and Path(title_ko_path).exists():
            ko_title = Path(title_ko_path).read_text().strip()
        else:
            ko_title = title_gen.generate(ko_path, "ko")
            Path(title_ko_path).write_text(ko_title)
        en_title = ""
        print(f"  KO: {ko_title}\n")
    else:
        if not args.no_cache and Path(title_ko_path).exists() and Path(title_en_path).exists():
            print("  캐시된 제목 사용")
            ko_title = Path(title_ko_path).read_text().strip()
            en_title = Path(title_en_path).read_text().strip()
        else:
            ko_title = title_gen.generate(ko_path, "ko")
            en_title = title_gen.generate(en_path, "en")
            Path(title_ko_path).write_text(ko_title)
            Path(title_en_path).write_text(en_title)
        print(f"  KO: {ko_title}")
        print(f"  EN: {en_title}\n")

    # ── Phase 7: 영상 조립 (KO/EN ffmpeg 병렬) ────────────────
    print("[Phase 7] 영상 조립 (KO/EN 병렬)...")

    ko_final = args.ko_out or f"{NAS_OUT}/ko/{slug}_ko.mp4"
    en_final = args.en_out or f"{NAS_OUT}/en/{slug}_en.mp4"

    ko_mp3 = f"{WORK}/ko/audio.mp3"
    if args.no_cache or not Path(ko_mp3).exists():
        os.system(f'ffmpeg -y -i {WORK}/ko/audio.wav -q:a 2 {ko_mp3} -loglevel error')

    def _compose_ko():
        t0 = time.time()
        compose.build(
            segments=ko_segments, prompts=prompts,
            images_dir=f"{WORK}/images",
            audio_path=ko_mp3,
            ass_path=ko_ass,
            final_out=ko_final,
            lang="ko",
            work_dir=f"{WORK}/ko",
            title=ko_title,
            source=args.source,
            gloss_ass=ko_gloss_ass,
        )
        return time.time() - t0

    def _compose_en():
        t0 = time.time()
        compose.build(
            segments=en_segments, prompts=prompts,
            images_dir=f"{WORK}/images",
            audio_path=f"{WORK}/en/audio.mp3",
            ass_path=en_ass,
            final_out=en_final,
            lang="en",
            work_dir=f"{WORK}/en",
            title=en_title,
            source=args.source,
            gloss_ass=en_gloss_ass,
        )
        return time.time() - t0

    if args.skip_en:
        t_ko = _compose_ko()
        t_en = 0.0
        timings["compose_ko"] = t_ko
        print(f"  ✓ KO {t_ko:.0f}s\n")
    else:
        with ThreadPoolExecutor(max_workers=2) as ex:
            fut_ko = ex.submit(_compose_ko)
            fut_en = ex.submit(_compose_en)
            t_ko = fut_ko.result()
            t_en = fut_en.result()
        timings["compose_ko"] = t_ko
        timings["compose_en"] = t_en
        print(f"  ✓ KO {t_ko:.0f}s / EN {t_en:.0f}s (병렬 wall-clock {max(t_ko,t_en):.0f}s)\n")

    # ── Phase 8: 메타 생성 ────────────────────────────────────
    print("[Phase 8] YouTube 메타 생성...")
    ko_meta = str(Path(ko_final).with_suffix(".youtube.txt"))
    en_meta = str(Path(en_final).with_suffix(".youtube.txt"))
    try:
        meta_gen.generate_ko(ko_path, ko_meta)
        if not args.skip_en:
            meta_gen.generate_en(en_path, en_meta)
    except Exception as e:
        print(f"  ! 메타 생성 실패 (영상은 정상): {e}")
    print()

    # ── Phase 10: 최종 보고 ───────────────────────────────────
    total = sum(timings.values())
    print(f"\n{'='*60}")
    print(" 완료 보고")
    print(f"{'='*60}")
    print(f"\n📹 최종 영상 (NAS):")
    print(f"   KO: {ko_final}")
    if not args.skip_en:
        print(f"   EN: {en_final}")
    print(f"\n📄 YouTube 메타:")
    print(f"   KO: {ko_meta}")
    if not args.skip_en:
        print(f"   EN: {en_meta}")
    print(f"\n⏱  단계별 소요 시간:")
    for k, v in timings.items():
        print(f"   {k:<18} {v:>6.0f}초")
    print(f"   {'총계':<18} {total:>6.0f}초")
    print(f"\n⚠️  유튜브 업로드 전 반드시 사람이 검토 후 수동 업로드.")
    print()


if __name__ == "__main__":
    main()
