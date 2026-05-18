# 뉴스 슬라이드쇼 영상 PoC — 설계 문서 (v3: 한/영 다국어 버전)

> Sonnet이 이 문서를 그대로 따라 PoC를 구현한다. 의문 발생시 사용자에게 확인 후 진행.
>
> **목적: YouTube 신규 채널 업로드를 위한 한국어 + 영어 동시 영상 PoC.** 이전 채널 벤 경험 있어 정책 준수 필수.
>
> **다국어 전략: 이미지는 1번만 생성해 공유, 음성/자막/메타만 언어별 분기.** Flux 추론 비용 50% 절감.

---

## 1. 절대 원칙 (변경 금지)

1. **기존 뉴스 파이프라인은 절대 수정하지 않는다.**
   - `/mnt/nas/data2/news/` 의 모든 파일 — 읽기 전용
   - `/home/sddari/news_runtime/.venv/` — 건드리지 않음
   - 기존 cron / `run.sh` — 수정 금지
2. **PoC는 완전히 격리된 디렉토리에서 작업한다.**
   - 작업 위치: `/home/sddari/news_video_poc/`
   - 별도 venv: `/home/sddari/news_video_poc/.venv/`
3. **최종 영상은 NAS에 저장한다.**
   - 출력 위치: `/mnt/nas/data2/mov/news_video_poc/`
   - 로컬 작업 산출물(이미지/임시MP3/자막)은 `/home/sddari/news_video_poc/work/` 에만 둠
4. **자동 업로드 절대 금지.** 사람이 검토 후 수동 업로드.
5. **PoC 검증 전까지 OpenClaw 크론에 등록하지 않는다.**

---

## 2. YouTube 정책 준수 사항 (벤 방지 핵심)

| 항목 | 조치 |
|---|---|
| **TTS 라이선스** | 상업 사용 가능한 로컬 모델만 사용 (Kokoro, Apache 2.0) |
| **콘텐츠 변형성** | 단순 뉴스 낭독 X. fact-check/비교/영향 분석 추가 (분석 강화 단계) |
| **이미지 라이선스** | Flux.1-schnell (Apache 2.0). 인물 클로즈업/실존 인물/저작 캐릭터 프롬프트 금지 |
| **AI 합성 공개** | 영상 설명란에 명시. YouTube 업로드 시 "변형/합성 콘텐츠" 체크 |
| **출처 표기** | 영상 첫 5초에 자막으로 출처 + 설명란에 URL 명시 |
| **업로드 빈도** | 주 2~3편 이하. 자동 X. 수동 검토 필수. |
| **채널** | **신규 채널** 생성. 이전 벤 계정/디바이스 재사용 금지. |

---

## 3. 입력 / 출력

### 입력
- **대본**: `/mnt/nas/data2/news/podcast_script_gas_price_2026.md` (PoC 고정 사용)
  - 형식: `상현:` / `지민:` 줄 단위 대화. 18줄 내외.
  - 복사본을 `work/script_dialogue.md` 에 둔다 (원본 그대로 유지).
- **출처 메타**: 가능하면 `/mnt/nas/data2/news/` 내 DB 또는 sosig.shop 에피소드 기록에서 원본 URL 가져옴. 없으면 PoC에서는 `<출처 미상>` 으로 두고 사용자에게 보고.

### 출력 (한/영 2벌)

```
/mnt/nas/data2/mov/news_video_poc/
├── ko/
│   ├── gas_price_2026_ko.mp4
│   └── gas_price_2026_ko.youtube.txt
└── en/
    ├── gas_price_2026_en.mp4
    └── gas_price_2026_en.youtube.txt
```

- **부산물 (로컬 작업폴더만)**:
  - `work/script_dialogue.md` — 원본 복사본
  - `work/script_narration_ko.md` — 한국어 분석 narration
  - `work/script_narration_en.md` — 영어 번역/현지화 narration
  - `work/ko/audio.wav` / `audio.mp3` / `subtitles.srt`
  - `work/en/audio.wav` / `audio.mp3` / `subtitles.srt`
  - `work/images/scene_NN.png` — **언어 공유** (1회 생성, 두 영상에서 재사용)
  - `work/prompts.json` — 이미지 프롬프트 로그
  - `work/scenes_ko.json` / `work/scenes_en.json` — 장면 메타 (언어별 타임스탬프 다름)

---

## 4. 기술 스택 결정

| 영역 | 선택 | 라이선스 | 이유 |
|---|---|---|---|
| TTS | **Kokoro v1.0** | Apache 2.0 | 상업 사용 가능, 한국어 지원, 경량 (~330MB), GB10에서 빠름 |
| 자막 정렬 | **Whisper-large-v3** (forced alignment) | MIT | Kokoro는 word boundary 미제공. Whisper로 한국어 정확 정렬 |
| 이미지 모델 | **Flux.1-schnell** (4-step) | Apache 2.0 | GB10에서 빠름, 라이선스 자유 |
| 이미지 추론 | `diffusers` + `bfloat16` | Apache 2.0 | 별도 ComfyUI 설치 불필요 |
| 분석/프롬프트 LLM | Ollama `qwen3:32b` | Apache 2.0 | 한글 이해 좋음, 이미 GPU 오프로드 검증됨 |
| 영상 조립 | `ffmpeg` 6.1.1 (시스템) | LGPL/GPL | filter_complex로 Ken Burns + 자막 burn-in |

**제거된 의존성:**
- ❌ Edge TTS (MS 약관상 상업 사용 불가)
- ❌ 화자 2인 분리 (Kokoro 한국어 음성 1개 고정 사용)
- ❌ edge-tts SubMaker (Whisper로 대체)

---

## 5. 음성 보이스 매핑 (언어별)

**단일 내레이터.** 상현/지민 대화 형식 → 1인 분석 내레이션.

```python
# Kokoro v1.0+ 보이스
VOICE_KO = "kf_aria"     # Korean Female, 차분한 분석 톤 (기본)
VOICE_EN = "af_heart"    # English Female, 한국 보이스와 톤 일관성
SPEED_KO = 1.05
SPEED_EN = 1.0           # 영어는 기본 속도 (한국어보다 느리게 들리지 않게)
```

Kokoro 한국어/영어 둘 다 동일 모델 / 동일 API에서 처리됨. 보이스 ID만 바꿈.
**사용 가능 보이스는 Sonnet이 모델 로드 후 `pipeline.list_voices()` 확인 → 후보 중 차분한 여성 톤 선택.**

**오프닝 음악 / 제목 낭독 PoC 제외.** 본문 내레이션만.

---

## 6. 대본 변환 (분석 강화 + 영어 현지화) — 신규 단계

**입력**: 기존 dialogue 형식 (상현/지민)
**출력 1**: 단일 내레이터 한국어 분석 대본 (`work/script_narration_ko.md`)
**출력 2**: 영어 현지화 대본 (`work/script_narration_en.md`)

### 6-1. 변환 원칙
원본 대본을 다음 4단계로 재구성:

1. **인트로 (10~15초)** — "안녕하세요. 오늘은 [주제]를 분석해보겠습니다." + 출처 명시
2. **사실 정리 (40~60초)** — 원 대본의 fact만 추려서 정리
3. **분석 강화 블록 (60~90초, 핵심)** — 다음 중 2개 이상 포함:
   - **비교 분석**: "작년 동기 대비 N%, 5년 평균 대비 X%"
   - **영향 분석**: "월소득 N만원 가구 기준 추가 부담 X원"
   - **fact-check**: "정부 발표 vs 실제 수치"
   - **맥락 제시**: "비슷한 사례로 2008년 또는 2022년..."
4. **마무리 (10~15초)** — 시청자 행동 제안 또는 향후 관전 포인트

### 6-2. 변환 구현
Ollama `qwen3:32b` 호출:

```
시스템: 너는 데이터 기반 뉴스 분석 진행자다. 주어진 대화 형식 대본을 
단일 내레이터의 분석 영상 대본으로 재구성한다.

규칙:
- 출력은 한국어 평어체 내레이션 (200~280자/단락, 4~6단락)
- 인트로 / 사실정리 / 분석 / 마무리 4섹션 구조
- 분석 섹션에 반드시 비교/영향/fact-check/맥락 중 2개 이상 포함
- 출처가 불명확한 수치는 "보도된 바에 따르면" 같은 단서 사용
- 화자 표시 X. 줄바꿈으로만 구분.
- 영상 길이 2~3분 목표 (원고 글자수 약 700~1000자)

대화: <원본 대본 전체>
```

### 6-3. 영어 현지화 (한국어 narration 완성 후 별도 호출)

```
시스템: 너는 한국 뉴스를 영어권 시청자에게 전달하는 전문 번역가다.
주어진 한국어 분석 내레이션을 자연스러운 영어로 옮긴다.

규칙:
- 한국 고유 맥락(원-달러 환율, 정부부처명 등)은 첫 등장시 짧은 부연 추가
  예: "the Ministry of Trade, Industry and Energy (MOTIE)"
- 한국 시청자에게는 익숙하나 영어권에는 생소한 정보는 보충 설명
  예: "won (Korean currency, ~0.00073 USD)"
- 톤: 분석적, 차분, 뉴스룸 스타일 (BBC/NPR 톤)
- 길이: 한국어 대본 길이 ±15% 이내 (음성 길이 매칭 위해)
- 단락 수 동일하게 유지 (4섹션 구조 보존)
- 출력은 영문 평문만, 메타정보 X

한국어 대본:
<work/script_narration_ko.md 전체>
```

### 6-4. 검증
- 한국어 narration: PoC 1회차에서 사용자에게 보여주고 OK 받음
- 영어 narration: 동일하게 사용자 검토 후 OK
- 두 대본 모두 단락 수 일치 (이미지 매핑 보장)

---

## 7. 자막 생성 방식

Kokoro는 word boundary를 직접 제공하지 않으므로 Whisper로 forced alignment 수행.

### 7-1. 절차 (한/영 각각 수행)
1. Kokoro로 단락별 음성 wav 생성
2. 단락별로 Whisper-large-v3 호출 (한국어 → `language="ko"`, 영어 → `language="en"`)
3. Whisper의 word-level timestamps 추출 (`word_timestamps=True`)
4. 음성 합성 시 누적 시간 + Whisper word time → 언어별 전체 SRT 생성
5. 각각 `work/ko/subtitles.srt`, `work/en/subtitles.srt` 저장

### 7-2. 자막 단위
- **줄(문장) 단위 자막 블록**, 한 블록 1~2줄
- 한국어: 한 줄 최대 25자 (가독성)
- 영어: 한 줄 최대 42자 (BBC 권장)
- 너무 긴 문장은 어절/단어 경계로 분할

### 7-3. 자막 포맷 (SRT)
```
1
00:00:00,300 --> 00:00:04,800
오늘은 휘발유 가격이
2000원을 넘어선 상황을 분석해보겠습니다.

2
00:00:04,800 --> 00:00:09,200
출처: 한국석유공사 오피넷, 산업통상자원부.
```

화자 표시 없음 (단일 내레이터).

---

## 8. 이미지 생성 (언어 공유, 1회만 생성)

### 8-1. 장면 분할 정책
- **단락(섹션) 단위로 1~2장**
- 4섹션 × 1.5장 = 약 6~8장 (영상 2~3분 기준)
- 각 장면은 약 15~25초 노출
- **한국어 narration의 단락 구조를 기준**으로 분할 (영어도 동일 단락 수 유지하므로 매핑 가능)
- 같은 이미지가 한국어/영어 영상 양쪽에서 같은 단락에 사용됨

### 8-2. 프롬프트 생성
Ollama `qwen3:32b` 호출 (대본 변환과 별도 호출):

```
시스템: 너는 한국 뉴스 분석 영상의 시각화 디자이너다. 주어진 단락에서 
핵심 시각 요소를 뽑아 영문 이미지 생성 프롬프트를 만든다.

규칙:
- 출력은 영문 1문장 (60단어 이내)
- 스타일 고정: "editorial illustration, flat vector, minimalist, 
  korean news graphic style, soft color palette, no text, no logos"
- 인물 얼굴 클로즈업 X. 실존 인물 X. 저작 캐릭터 X.
- 개념 시각화 우선 (그래프 모티프, 사물, 풍경, 추상)
- 텍스트(글자) 절대 금지

단락: <narration_paragraph>
```

### 8-3. Flux 추론 설정
- model: `black-forest-labs/FLUX.1-schnell`
- resolution: **1920x1080** (16:9)
- steps: 4
- guidance_scale: 0.0
- dtype: `bfloat16`
- 1장당 예상 ~10초 (GB10)

### 8-4. 캐시
프롬프트 SHA1 → 파일명. `work/images/{idx:02d}_{sha1[:8]}.png`

---

## 9. 영상 조립 (ffmpeg, 한/영 각각 빌드)

언어별로 단락 길이가 다름 → Ken Burns duration도 언어별로 다름. 이미지는 같지만 영상은 별개.

### 9-1. 장면 단위 영상 생성 (Ken Burns)
각 이미지를 해당 단락 길이만큼 천천히 줌인 (1.0x → 1.08x).

```bash
ffmpeg -loop 1 -i scene_01.png -t <duration> \
  -vf "zoompan=z='min(zoom+0.0006,1.08)':d=<duration*30>:s=1920x1080:fps=30" \
  -c:v libx264 -pix_fmt yuv420p -r 30 scene_01.mp4
```

### 9-2. 첫 5초 출처 워터마크 (언어별 텍스트)

**한국어판:**
```
출처: 한국석유공사 오피넷
AI 생성 영상
```

**영어판:**
```
Source: KNOC Opinet (Korea National Oil Corp.)
AI-Generated Content
```

### 9-3. 장면 연결 (concat)
```bash
ffmpeg -f concat -safe 0 -i scenes.txt -c copy slideshow.mp4
```

### 9-4. 음성 + 자막 합성

**한국어판:**
```bash
ffmpeg -i slideshow_ko.mp4 -i ko/audio.mp3 \
  -vf "subtitles=ko/subtitles.srt:force_style='FontName=NanumGothic,FontSize=22,PrimaryColour=&Hffffff&,OutlineColour=&H000000&,Outline=2,Alignment=2,MarginV=80'" \
  -c:a aac -shortest gas_price_2026_ko.mp4
```

**영어판:**
```bash
ffmpeg -i slideshow_en.mp4 -i en/audio.mp3 \
  -vf "subtitles=en/subtitles.srt:force_style='FontName=DejaVu Sans,FontSize=20,PrimaryColour=&Hffffff&,OutlineColour=&H000000&,Outline=2,Alignment=2,MarginV=80'" \
  -c:a aac -shortest gas_price_2026_en.mp4
```

### 9-5. 폰트
- 한국어: **NanumGothic** (`fc-list :lang=ko` 확인). 미설치시 사용자 보고 후 `apt install fonts-nanum`.
- 영어: **DejaVu Sans** (Ubuntu 기본 설치). 미설치시 `fonts-dejavu-core`.

---

## 10. YouTube 업로드용 메타 파일 생성 (언어별)

### 10-1. 한국어 메타 (`gas_price_2026_ko.youtube.txt`)

```
=========================================================
YouTube 업로드 메타 (한국어) — 사람 검토 필수
=========================================================

[제목 후보]
1) [분석] 휘발유 2000원 시대, 정부 5조원 보전이 답일까
2) [데이터] 5년 만의 최고가, 우리 가계에 미치는 영향은
3) 휘발유 가격 분석: 정부 정책의 실효성 점검

[설명란]
오늘 영상은 휘발유 가격 동향을 데이터로 분석합니다.
정부의 가격 통제 정책과 실제 국제 유가의 괴리를 살펴보고,
일반 가정에 미치는 영향을 추정합니다.

📌 출처:
- 한국석유공사 오피넷 (URL)
- 산업통상자원부 보도자료 (URL)
- <크롤 시점 원본 기사 URL>

⚠️ 본 영상은 AI 음성/이미지 합성을 사용했습니다.
   분석 내용은 공개 자료에 기반하며, 투자/소비 결정의 근거가 될 수 없습니다.

🌐 English version: <영어판 영상 URL을 사람이 업로드 후 채워넣기>

#뉴스분석 #휘발유 #에너지 #AI생성

[태그]
뉴스, 분석, 휘발유, 유가, 에너지정책, 가계경제

[업로드 체크리스트]
[ ] "변형 또는 합성 콘텐츠" 체크 (필수)
[ ] 카테고리: 뉴스 및 정치
[ ] 어린이용 콘텐츠 아님
[ ] 자막 언어: 한국어
[ ] 영어판 영상 URL 설명에 추가 (영어판 업로드 후)
[ ] 출처 URL 모두 유효한지 확인
[ ] 사실 오류 없는지 본인 검토
```

### 10-2. 영어 메타 (`gas_price_2026_en.youtube.txt`)

```
=========================================================
YouTube Upload Meta (English) — Human Review Required
=========================================================

[Title Candidates]
1) [Analysis] Korea's Gasoline Hits 2,000 Won — Is the Government's $3.5B Subsidy the Answer?
2) Korea Fuel Crisis: 5-Year High Prices and What It Means for Households
3) Behind Korea's Gas Price Surge: Policy, Politics, and Global Oil Markets

[Description]
This video analyzes Korea's gasoline price trends through data.
We examine the gap between government price controls and actual
international oil prices, and estimate the impact on ordinary households.

📌 Sources:
- Korea National Oil Corporation (KNOC) Opinet (URL)
- Ministry of Trade, Industry and Energy press release (URL)
- <original news articles URL>

⚠️ This video uses AI-generated voice and imagery.
   Analysis is based on public data and should not be used for
   investment or consumer decisions.

🌐 한국어판: <Korean video URL after upload>

#KoreaNews #EnergyAnalysis #FuelPrices #AIGenerated

[Tags]
korea news, energy policy, gasoline prices, oil market, economic analysis, household impact

[Upload Checklist]
[ ] Tick "Altered or synthetic content" (required)
[ ] Category: News & Politics
[ ] Not for kids
[ ] Subtitle language: English
[ ] Add Korean video URL to description (after upload)
[ ] Verify all source URLs are valid
[ ] Fact-check before publish
[ ] Watermark visible in first 5 seconds
```

제목/태그/설명은 Ollama로 자동 생성하되 후보를 여러 개 제시하여 사용자가 고르게 함.

---

## 11. 디렉토리 구조

```
/home/sddari/news_video_poc/
├── DESIGN.md
├── README.md
├── .venv/
├── requirements.txt
├── poc.py                     ← 메인 엔트리 (한/영 동시 처리)
├── lib/
│   ├── __init__.py
│   ├── parser.py              ← 대본 파싱
│   ├── transform.py           ← dialogue → ko narration → en narration
│   ├── tts.py                 ← Kokoro (lang 인자로 ko/en 처리)
│   ├── align.py               ← Whisper forced alignment (lang별)
│   ├── prompt_gen.py          ← Ollama 이미지 프롬프트 (영문)
│   ├── image_gen.py           ← Flux 추론 (1회만)
│   ├── compose.py             ← ffmpeg 조립 + 워터마크 (lang별)
│   └── meta_gen.py            ← YouTube 메타 (lang별)
└── work/
    ├── script_dialogue.md
    ├── script_narration_ko.md
    ├── script_narration_en.md
    ├── prompts.json
    ├── images/                ← 한/영 공유
    │   ├── 00_<sha>.png
    │   └── ...
    ├── ko/
    │   ├── audio.wav / audio.mp3
    │   ├── subtitles.srt
    │   ├── scenes.json
    │   └── slideshow_no_audio.mp4
    └── en/
        ├── audio.wav / audio.mp3
        ├── subtitles.srt
        ├── scenes.json
        └── slideshow_no_audio.mp4
```

---

## 12. 의존성 (requirements.txt)

```
kokoro>=0.7.0
torch>=2.4.0
torchaudio>=2.4.0
diffusers>=0.30.0
transformers>=4.45.0
accelerate>=1.0.0
sentencepiece
protobuf
pillow
soundfile
openai-whisper>=20240930   # large-v3 사용
requests
numpy<2
```

ffmpeg는 시스템 사용. Ollama는 별도 서비스(이미 실행 중) HTTP 호출.

---

## 13. 구현 순서 (Sonnet 작업 단계)

### Phase 0: 환경 준비
- [ ] `python3 -m venv /home/sddari/news_video_poc/.venv`
- [ ] `pip install -r requirements.txt`
- [ ] Kokoro 첫 실행 시 자동 다운로드 — **한국어 + 영어 보이스 둘 다 사용 가능한지 확인**
- [ ] Whisper-large-v3 첫 호출 시 자동 다운로드
- [ ] Ollama `qwen3:32b` 모델 존재 확인
- [ ] NanumGothic + DejaVu Sans 폰트 확인 (`fc-list :lang=ko`, `fc-list | grep -i dejavu`)
- [ ] `/mnt/nas/data2/mov/news_video_poc/{ko,en}/` 디렉토리 생성

### Phase 1: 파서 (`lib/parser.py`)
- dialogue 마크다운 → `[{idx, speaker, text}]`
- **단위 테스트**: gas_price_2026.md 18 발화 출력

### Phase 2: 대본 변환 (`lib/transform.py`) — 한/영 둘 다
- 2-1. Ollama → dialogue → 한국어 분석 narration (`script_narration_ko.md`)
- 2-2. Ollama → 한국어 narration → 영어 현지화 (`script_narration_en.md`)
- **PoC에서는 두 결과 모두 사용자에게 보여주고 승인 받음**
- 두 대본의 단락 수 일치 검증 (이미지 매핑 보장)

### Phase 3: TTS (`lib/tts.py`) — 한/영 각각
- 3-1. Kokoro KO 보이스로 한국어 단락별 wav → `work/ko/audio.wav` + 단락 타임스탬프
- 3-2. Kokoro EN 보이스로 영어 단락별 wav → `work/en/audio.wav` + 단락 타임스탬프
- 각각 mp3 변환

### Phase 4: 자막 정렬 (`lib/align.py`) — 한/영 각각
- 4-1. Whisper(language="ko") → `work/ko/subtitles.srt`
- 4-2. Whisper(language="en") → `work/en/subtitles.srt`
- 한국어 25자/줄, 영어 42자/줄 분할

### Phase 5: 프롬프트 생성 (`lib/prompt_gen.py`)
- **한국어 narration 단락 기준**으로 영문 이미지 프롬프트 생성
- 영어 narration도 같은 단락 수이므로 같은 이미지 사용
- `work/prompts.json` 저장

### Phase 6: 이미지 생성 (`lib/image_gen.py`)
- Flux.1-schnell **1회만** 호출하여 6~8장 생성
- 한/영 영상 양쪽에서 공유

### Phase 7: 영상 조립 (`lib/compose.py`) — 한/영 각각
- 7-1. 한국어 Ken Burns(KO 단락 길이) → 워터마크 → KO 음성/자막 합성 → `gas_price_2026_ko.mp4`
- 7-2. 영어 Ken Burns(EN 단락 길이) → 워터마크 → EN 음성/자막 합성 → `gas_price_2026_en.mp4`
- 출력 경로: `/mnt/nas/data2/mov/news_video_poc/{ko,en}/`

### Phase 8: 메타 생성 (`lib/meta_gen.py`) — 한/영 각각
- Ollama로 한국어 제목 후보 3개 / 태그 / 설명 생성 → `gas_price_2026_ko.youtube.txt`
- Ollama로 영어 제목 후보 3개 / 태그 / 설명 생성 → `gas_price_2026_en.youtube.txt`

### Phase 9: 메인 엔트리 (`poc.py`)
```bash
python poc.py --script /mnt/nas/data2/news/podcast_script_gas_price_2026.md
```
모든 단계 순차 실행, 진행률/소요시간 출력. 한/영 둘 다 산출.

### Phase 10: 보고
- NAS 영상/메타 경로 (한/영 각각) 사용자에게 제시
- 단계별 소요 시간 표 (이미지 1회 = 양쪽 공유 효과 강조)
- 품질 평가 (음성/이미지/자막 싱크/현지화 자연스러움)
- 사람이 검토 후 YouTube 업로드 (자동 X)

---

## 14. 실패시 행동 원칙

| 상황 | 행동 |
|---|---|
| Kokoro 한국어 보이스 미지원 | 사용자에게 보고. 대안: MeloTTS-KR 임시 사용 또는 PoC 보류 |
| Kokoro 영어 보이스만 가능 | 한국어 부분만 MeloTTS-KR 폴백, 영어는 Kokoro 그대로 |
| 영어 번역 결과가 부자연스러움 | 사용자에게 보여주고 보류. 모델 변경 검토 (qwen3 → claude API) |
| 한/영 단락 수 불일치 | 영어 번역 재시도 (단락 수 명시 강조). 3회 실패시 사용자 보고 |
| Flux 모델 다운로드 실패 | 사용자 보고, 진행 중지 |
| VRAM OOM | `cpu_offload` 폴백 |
| Ollama 호출 실패 | 보고. transform 단계는 폴백 없음 |
| Whisper 정렬 부정확 | 어절/단어 단위 수동 보정 폴백 (paragraph time만 사용) |
| 폰트 없음 | 보고, 사용자 확인 후 `apt install fonts-nanum fonts-dejavu-core` |
| ffmpeg 필터 에러 | 자막 burn-in 없이 외부 .srt 동봉 폴백 |
| NAS 마운트 끊김 | 로컬 저장 후 보고 |

**임의로 기존 시스템 변경하지 않음.** 의문이 생기면 사용자에게 묻는다.

---

## 14-A. 저작권 / 정책 위험 분석 (필수)

YouTube 업로드 전 반드시 점검할 항목.

### A. 뉴스 본문 — 가장 큰 위험

| 위험 | 설명 | 대응 |
|---|---|---|
| 기사 본문 복제 | 네이버 등 기사 텍스트 그대로 사용은 저작권 침해 | **원문 한 문장도 그대로 쓰지 않음.** Phase 2 LLM 프롬프트에 "원문 표현을 그대로 인용하지 말 것" 명시 |
| 기사 번역 (영어판) | 한국어 기사를 영어로 번역만 한 것 = 2차 저작물 | 분석/논평 형태로 재구성. "기사 번역"이 아닌 "기사를 소재로 한 분석" |
| 한국언론진흥재단 집중관리 | 네이버 뉴스 다수가 KPF 집중관리 대상 | 출처 명시 + 변형성 확보로 공정이용 주장 가능하게 |
| 사진 사용 | 기사 사진을 그대로 가져오면 즉시 침해 | **사진 절대 가져오지 않음.** Flux 생성 이미지만 사용 |

**Phase 2 LLM 프롬프트에 추가할 강제 규칙:**
```
- 원문 표현을 그대로 인용하지 말 것 (직접 인용 금지)
- 사실/숫자만 추출해서 본인의 말로 재구성
- 분석/논평/맥락 추가가 본문 중 60% 이상 차지해야 함
- 출처는 영상 시작과 설명란에 명시함을 전제
```

### B. 이미지 — 중간 위험

| 위험 | 대응 |
|---|---|
| Flux가 실존 인물 얼굴 생성 | 프롬프트에 사람 얼굴 클로즈업 금지 (이미 적용) |
| 브랜드 로고/제품 디자인 모방 | 프롬프트에 "no logos, no brand names, no product replicas" 명시 |
| 저작 캐릭터 (마블/디즈니/K-pop 아이돌 등) | 프롬프트에 "no copyrighted characters, no celebrities" 명시 |
| 특정 화풍 모방 (지브리/픽사 등) | 일반 명사 스타일만 사용 ("editorial illustration, flat vector") |

**프롬프트 생성 LLM에 강제 규칙 추가:**
```
금지: real people, celebrities, brand logos, copyrighted characters,
      product names, specific corporate buildings, K-pop idols,
      ghibli style, pixar style, disney style
허용: abstract concepts, generic objects, landscapes, charts/graphs as motifs,
      anonymous silhouettes, generic illustrations
```

### C. 음성 — 낮은 위험

| 항목 | 평가 |
|---|---|
| Kokoro 모델 | Apache 2.0, 학습 데이터도 라이선스 클리어 (제작자 명시) |
| 보이스 ID | 합성 캐릭터 보이스 (실존 인물 아님) |
| 결과물 상업 사용 | 가능 |

### D. 폰트

| 폰트 | 라이선스 | 결과 |
|---|---|---|
| NanumGothic | SIL Open Font License | 상업 사용 OK |
| DejaVu Sans | DejaVu License (free, commercial OK) | OK |

### E. 배경 음악 / 효과음

PoC v3에서는 **음악/효과음 모두 제외**. 추가할 경우 다음 원칙:
- YouTube 오디오 라이브러리 무료 음원만
- 또는 CC0 / Public Domain 음원 (Pixabay Music, Free Music Archive 검증된 것)
- 절대 금지: 시판 음악, "가져온" 효과음, 미검증 출처

### F. 저작권 문제 발생시 행동

1. **YouTube에서 Content ID 클레임 받음** → 문제 부분 자체 분석/제거 후 재업로드
2. **권리자가 직접 신고** → 즉시 비공개 전환, 문제 확인, 답변 후 재업로드 결정
3. **3회 이상 누적 클레임** → 채널 즉시 점검, 새 채널 검토
4. **법적 통지** → 사용자 직접 대응. 자동화는 즉시 중단.

### G. 한국 / 글로벌 다른 점

| 영역 | 한국 (KR) | 미국/유럽 (EN 시청자) |
|---|---|---|
| 뉴스 인용 fair use | 좁음 (출처+공정이용 까다로움) | 넓음 (transformative use 인정 폭 큼) |
| AI 합성 표시 의무 | YouTube 정책 적용 | YouTube 정책 + 일부 주(州) 추가 규제 |
| 정치 인물 합성 | 매우 위험 | 매우 위험 (선거 시즌 가중처벌) |
| 음악 사용 | 한국음악저작권협회(KOMCA) 별도 관리 | ASCAP/BMI/SESAC |

영어판이 한국 뉴스를 다루더라도 **YouTube 글로벌 정책이 적용됨** → 한국 기준으로 안전하게 만들면 영어판도 OK.

---

## 15. PoC 통과 후 (자동화 단계 — 별도 작업)

PoC 결과를 사용자가 검토 후 OK하면:
1. OpenClaw 크론에 신규 잡 추가 (기존 잡 수정 X)
2. 트리거: 기존 음성 생성 완료 시그널 감지 (DB의 새 episode INSERT 또는 .mp3 mtime)
3. 자동화 스크립트는 try/except로 격리. 실패해도 기존 파이프라인 영향 없음.
4. **유튜브 업로드는 끝까지 수동.** 자동 업로드는 벤 위험 매우 높음.
5. 메타 파일에 사람이 제목/설명 골라 적은 뒤 업로드.

---

**끝.** 이 설계대로 진행. 사용자 승인 후 Sonnet은 Phase 0부터 순차 실행.
