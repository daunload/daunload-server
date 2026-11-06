# 오디오 추출 테스트용 mp4

`tests/test_audio_extract_web.py`가 쓰는 픽스처. 이 디렉터리에서 다시 만들 때:

```bash
ffmpeg -f lavfi -i "sine=frequency=440:duration=3:sample_rate=48000" -f lavfi -i "color=size=64x64:duration=3" \
  -ac 2 -c:a aac -c:v libx264 -movflags +faststart tone-faststart.mp4
ffmpeg -f lavfi -i "sine=frequency=440:duration=3:sample_rate=48000" -f lavfi -i "color=size=64x64:duration=3" \
  -ac 2 -c:a aac -c:v libx264 tone-moov-end.mp4
ffmpeg -f lavfi -i "sine=frequency=440:duration=3:sample_rate=48000" -f lavfi -i "color=size=64x64:duration=3" \
  -ac 2 -c:a libopus -c:v libx264 -movflags +faststart tone-opus.mp4
ffmpeg -f lavfi -i "color=size=64x64:duration=1" -c:v libx264 video-only.mp4
ffmpeg -f lavfi -i "sine=frequency=440:duration=12:sample_rate=48000" -f lavfi -i "color=size=64x64:duration=12" \
  -ac 2 -c:a libopus -c:v libx264 -movflags +faststart tone-opus-12s.mp4
```

- `tone-moov-end.mp4`는 `moov`가 `mdat` 뒤에 있다: `ffprobe -v trace tone-moov-end.mp4 2>&1 | grep -E "type:'(moov|mdat)'"`
- `tone-opus.mp4`는 AAC를 디코딩하지 못하는 Playwright Chromium에서 확장 프로그램을 시험할 때 쓴다.
- `tone-opus-12s.mp4`(12초)는 `tests/test_download_extension.py`의 흐름 제어 시험용이다. 5초 상한, 남은 조각 이월, 닫은 뒤 요청 중단, 서비스 워커 재시작 뒤 이어 읽기를 확인한다. 테스트는 `audio-open`의 `chunkBytes`를 16 KiB로 낮춰 여러 Range로 나눠 읽는다.
