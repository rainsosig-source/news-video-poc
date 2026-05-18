#!/usr/bin/env python3
"""Zonos 폴백 — Edge TTS 503 시 단락 단위 호출.

Stdin (JSON): {"sentences": [...], "out_dir": "/path/to/work/zonos_fallback_<i>"}
Stdout (JSON): {"paths": [wav_path_per_sentence], "sample_rate": 44100}

venv: /home/sddari/tts_eval/venv_zonos/bin/python
참조: /home/sddari/tts_eval/refs/male_ref.mp3 (Edge TTS InJoonNeural과 동일 male 톤)
"""
import json
import os
import sys
import torch
import torchaudio
from zonos.model import Zonos
from zonos.conditioning import make_cond_dict

REF_MP3 = "/home/sddari/tts_eval/refs/male_ref.mp3"


def main() -> None:
    spec = json.loads(sys.stdin.read())
    sentences: list[str] = spec["sentences"]
    out_dir: str = spec["out_dir"]
    os.makedirs(out_dir, exist_ok=True)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"[zonos] device={device}, sentences={len(sentences)}", file=sys.stderr, flush=True)

    model = Zonos.from_pretrained("Zyphra/Zonos-v0.1-transformer", device=device)
    wav, sr = torchaudio.load(REF_MP3)
    with torch.device("cpu"):
        speaker = model.make_speaker_embedding(wav, sr)

    paths: list[str] = []
    for i, text in enumerate(sentences):
        cond = make_cond_dict(text=text, speaker=speaker, language="ko")
        conditioning = model.prepare_conditioning(cond)
        with torch.no_grad():
            codes = model.generate(conditioning, disable_torch_compile=True)
            audio = model.autoencoder.decode(codes).cpu()
        out_path = os.path.join(out_dir, f"zonos_{i:03d}.wav")
        torchaudio.save(out_path, audio[0], model.autoencoder.sampling_rate)
        paths.append(out_path)
        print(f"[zonos] {i+1}/{len(sentences)} → {out_path}", file=sys.stderr, flush=True)

    print(json.dumps({"paths": paths, "sample_rate": int(model.autoencoder.sampling_rate)}))


if __name__ == "__main__":
    main()
