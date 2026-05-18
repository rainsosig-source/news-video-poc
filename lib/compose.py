"""ffmpeg 영상 조립: 타이틀 + Ken Burns + 워터마크 + 자막 + 음성."""
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

# NanumGothic은 한글 글자 누락 있음 → Noto Sans CJK KR (완전한 커버리지)
FONT_KO = "Noto Sans CJK KR"
FONT_EN = "DejaVuSans"
FPS = 30
ZOOM_START = 1.0
ZOOM_END = 1.08
TITLE_SEC = 3.0

def _build_watermark(source: str, lang: str) -> str:
    if lang == "ko":
        src = source if source else "출처 미상"
        return f"출처: {src}  ·  AI 생성 영상"
    else:
        src = source if source else "Source unknown"
        return f"Source: {src}  ·  AI-Generated Content"


def _run(cmd: list[str], desc: str = "") -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"{desc} 실패:\n{result.stderr[-500:]}")


def _ken_burns_clip(image_path: str, duration: float, out_path: str) -> None:
    """이미지 → Ken Burns 줌인 mp4 클립."""
    d = max(duration, 1.0)
    zoom_speed = (ZOOM_END - ZOOM_START) / (d * FPS)
    vf = (
        f"zoompan=z='min(zoom+{zoom_speed:.6f},{ZOOM_END})':"
        f"d={int(d * FPS)}:s=1920x1080:fps={FPS},scale=1920:1080"
    )
    _run([
        "ffmpeg", "-y", "-loop", "1", "-i", image_path,
        "-t", str(d),
        "-vf", vf,
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-preset", "medium", "-crf", "18", out_path,
    ], f"ken_burns {os.path.basename(image_path)}")


def _ass_escape_title(text: str) -> str:
    return text.replace("\n", "\\N").replace("{", "\\{").replace("}", "\\}")


def _title_clip(title: str, lang: str, duration: float, out_path: str) -> None:
    """제목 슬라이드: 어두운 배경 + 제목(중앙) + 날짜(아래) + fade in/out.
    libass(subtitles 필터) 기반 — drawtext의 TTC/한글 폴백 한계 회피."""
    font = FONT_KO if lang == "ko" else FONT_EN
    font_size = 72 if lang == "ko" else 60
    date_size = 36

    now = datetime.now()
    date_str = now.strftime("%Y년 %-m월 %-d일") if lang == "ko" else now.strftime("%B %-d, %Y")

    fade_ms = 500
    end = f"0:00:{duration:05.2f}"
    title_text = "{\\fad(" + str(fade_ms) + "," + str(fade_ms) + ")}" + _ass_escape_title(title)
    date_text  = "{\\fad(" + str(fade_ms) + "," + str(fade_ms) + ")}" + _ass_escape_title(date_str)

    ass_path = out_path + ".title.ass"
    ass = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1920\nPlayResY: 1080\nWrapStyle: 2\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: TTitle,{font},{font_size},&H00FFFFFF,&H00000000,&H00000000,"
        f"1,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1\n"
        f"Style: TDate,{font},{date_size},&H80FFFFFF,&H00000000,&H00000000,"
        f"0,0,0,0,100,100,0,0,1,0,0,5,0,0,-110,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        f"Dialogue: 0,0:00:00.00,{end},TTitle,,0,0,0,,{title_text}\n"
        f"Dialogue: 0,0:00:00.00,{end},TDate,,0,0,0,,{date_text}\n"
    )
    Path(ass_path).write_text(ass, encoding="utf-8")

    _run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c=#0f1a2e:s=1920x1080:d={duration}:r={FPS}",
        "-vf", f"subtitles='{ass_path}'",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-preset", "medium", "-crf", "18", out_path,
    ], "title_clip")


def _pad_audio_silence(audio_path: str, silence_sec: float, out_path: str) -> None:
    """오디오 앞쪽에 무음 패딩 추가."""
    _run([
        "ffmpeg", "-y",
        "-i", audio_path,
        "-af", f"adelay={int(silence_sec*1000)}|{int(silence_sec*1000)},apad=pad_dur={silence_sec}",
        "-c:a", "aac", "-ar", "44100", out_path,
    ], "pad_audio")


def _shift_srt(srt_path: str, shift_sec: float, out_path: str) -> None:
    """SRT 타임스탬프 전체를 shift_sec만큼 뒤로 밀기."""
    text = Path(srt_path).read_text(encoding="utf-8")
    def repl(m):
        h, mm, s, ms = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
        total = h*3600 + mm*60 + s + ms/1000.0 + shift_sec
        nh = int(total // 3600)
        nm = int((total % 3600) // 60)
        ns = total % 60
        return f"{nh:02d}:{nm:02d}:{ns:06.3f}".replace(".", ",")
    new_text = re.sub(r"(\d{2}):(\d{2}):(\d{2}),(\d{3})", repl, text)
    Path(out_path).write_text(new_text, encoding="utf-8")


def _shift_ass_time(t_str: str, shift: float) -> str:
    """ASS 시간 문자열(H:MM:SS.cc)에 shift 초 더하기."""
    t_str = t_str.strip()
    h, m, s = t_str.split(":")
    total = int(h) * 3600 + int(m) * 60 + float(s) + shift
    nh = int(total // 3600)
    nm = int((total % 3600) // 60)
    ns = total % 60
    return f"{nh}:{nm:02d}:{ns:05.2f}"


def _shift_ass(ass_path: str, shift_sec: float, out_path: str) -> None:
    """ASS Dialogue 라인의 Start/End 시간을 shift_sec만큼 뒤로 밀기."""
    text = Path(ass_path).read_text(encoding="utf-8")
    out_lines = []
    for line in text.splitlines():
        if line.startswith("Dialogue:"):
            parts = line.split(",", 9)
            parts[1] = _shift_ass_time(parts[1], shift_sec)
            parts[2] = _shift_ass_time(parts[2], shift_sec)
            out_lines.append(",".join(parts))
        else:
            out_lines.append(line)
    Path(out_path).write_text("\n".join(out_lines), encoding="utf-8")


def _concat_clips(clip_paths: list[str], out_path: str) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        for p in clip_paths:
            f.write(f"file '{os.path.abspath(p)}'\n")
        list_path = f.name
    _run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", list_path, "-c", "copy", out_path,
    ], "concat")
    os.unlink(list_path)


def _add_audio_subtitle(
    video_path: str, audio_path: str, ass_path: str,
    out_path: str, lang: str,
    source: str = "",
    work_dir: str = "",
    add_watermark: bool = True,
    gloss_ass: str = "",
) -> None:
    # ASS 자체에 스타일 정의되어 있으므로 force_style 불필요
    vf_parts = [f"subtitles='{ass_path}'"]

    # 어려운 경제 용어 해설 박스 (top-right, 페이드 인/아웃)
    if gloss_ass and Path(gloss_ass).exists():
        try:
            from lib import glossary_gen
            if glossary_gen.has_events(gloss_ass):
                vf_parts.append(f"subtitles='{gloss_ass}'")
        except Exception:
            pass

    if add_watermark:
        font = FONT_KO if lang == "ko" else FONT_EN
        wm_text = _build_watermark(source, lang)
        wm_ass = os.path.join(work_dir, "watermark.ass") if work_dir else "/tmp/wm_tmp.ass"
        wm_text_esc = _ass_escape_title(wm_text)
        wm_ass_content = (
            "[Script Info]\n"
            "ScriptType: v4.00+\n"
            "PlayResX: 1920\nPlayResY: 1080\nWrapStyle: 2\n\n"
            "[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, "
            "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
            "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
            f"Style: WM,{font},22,&H00FFFFFF,&H00000000,&H80000000,"
            f"0,0,0,0,100,100,0,0,3,0,0,7,30,30,30,1\n\n"
            "[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
            f"Dialogue: 0,0:00:00.00,0:00:05.00,WM,,0,0,0,,{wm_text_esc}\n"
        )
        Path(wm_ass).write_text(wm_ass_content, encoding="utf-8")
        vf_parts.append(f"subtitles='{wm_ass}'")

    _run([
        "ffmpeg", "-y",
        "-i", video_path, "-i", audio_path,
        "-vf", ",".join(vf_parts),
        "-c:v", "libx264", "-c:a", "aac", "-ar", "44100", "-shortest",
        "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p", out_path,
    ], "add_audio_subtitle")


def build(
    segments: list[dict],
    prompts: list[dict],
    images_dir: str,
    audio_path: str,
    ass_path: str,
    final_out: str,
    lang: str,
    work_dir: str,
    title: str = "",
    source: str = "",
    gloss_ass: str = "",
) -> None:
    """전체 영상 조립. title이 있으면 앞에 3초 타이틀 슬라이드."""
    Path(work_dir).mkdir(parents=True, exist_ok=True)
    Path(os.path.dirname(final_out)).mkdir(parents=True, exist_ok=True)

    n = min(len(segments), len(prompts))
    clip_paths = []

    audio_total = float(subprocess.check_output(
        ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
         '-of', 'csv=p=0', audio_path]
    ).decode().strip())

    if title:
        title_path = os.path.join(work_dir, "clip_title.mp4")
        print(f"  [타이틀] {TITLE_SEC:.1f}초 — {title}")
        _title_clip(title, lang, TITLE_SEC, title_path)
        clip_paths.append(title_path)

    # 깊이 패럴랙스 모듈 (실패 시 ken burns 폴백)
    try:
        from lib import parallax
        _use_parallax = True
    except Exception as e:
        print(f"  ! parallax import 실패 ({e}) → ken burns 사용")
        _use_parallax = False

    for i in range(n):
        seg = segments[i]
        img_file = prompts[i]["image_file"]
        img_path = os.path.join(images_dir, img_file)
        next_start = segments[i + 1]["start"] if i + 1 < n else audio_total
        duration = next_start - seg["start"]
        clip_out = os.path.join(work_dir, f"clip_{i:02d}.mp4")
        if _use_parallax:
            print(f"  [클립 {i+1}/{n}] {duration:.1f}초 depth-parallax...")
            try:
                parallax.generate_parallax_clip(img_path, duration, clip_out, motion_idx=i)
                clip_paths.append(clip_out)
                continue
            except Exception as e:
                print(f"    ! parallax 실패 ({e}) → 이 클립은 ken burns 폴백")
        print(f"  [클립 {i+1}/{n}] {duration:.1f}초 ken burns...")
        _ken_burns_clip(img_path, duration, clip_out)
        clip_paths.append(clip_out)

    # GPU 메모리 정리 (다음 작업을 위해)
    if _use_parallax:
        try:
            from lib import parallax as _p
            _p.release()
        except Exception:
            pass

    # 타이틀이 있으면 오디오 앞 무음 패딩 + 자막 시간 shift
    if title:
        padded_audio = os.path.join(work_dir, "audio_padded.m4a")
        _pad_audio_silence(audio_path, TITLE_SEC, padded_audio)
        final_audio = padded_audio
        # ASS 또는 SRT 자동 분기
        if ass_path.endswith(".ass"):
            shifted = os.path.join(work_dir, "subtitles_shifted.ass")
            _shift_ass(ass_path, TITLE_SEC, shifted)
        else:
            shifted = os.path.join(work_dir, "subtitles_shifted.srt")
            _shift_srt(ass_path, TITLE_SEC, shifted)
        final_subtitle = shifted

        # 글로서리도 동일 shift 적용
        if gloss_ass and Path(gloss_ass).exists():
            shifted_gloss = os.path.join(work_dir, "glossary_shifted.ass")
            _shift_ass(gloss_ass, TITLE_SEC, shifted_gloss)
            final_gloss = shifted_gloss
        else:
            final_gloss = ""
    else:
        final_audio = audio_path
        final_subtitle = ass_path
        final_gloss = gloss_ass

    slideshow = os.path.join(work_dir, "slideshow_no_audio.mp4")
    print("  클립 concat 중...")
    _concat_clips(clip_paths, slideshow)

    print("  음성 + 자막 합성 중...")
    _add_audio_subtitle(slideshow, final_audio, final_subtitle, final_out, lang,
                        source=source, work_dir=work_dir, gloss_ass=final_gloss)
    print(f"  → {final_out}")

    for p in clip_paths:
        try:
            os.remove(p)
        except Exception:
            pass


if __name__ == "__main__":
    import sys
    lang = sys.argv[1] if len(sys.argv) > 1 else "en"
    segments = json.loads(open(f"work/{lang}/scenes.json").read())
    prompts = json.loads(open("work/prompts.json").read())
    build(
        segments=segments,
        prompts=prompts,
        images_dir="work/images",
        audio_path=f"work/{lang}/audio.mp3",
        ass_path=f"work/{lang}/subtitles.ass",
        final_out=f"/mnt/nas/data2/mov/news_video_poc/{lang}/gas_price_2026_{lang}.mp4",
        lang=lang,
        work_dir=f"work/{lang}",
    )
