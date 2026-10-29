"""Whisper 창 나누기 규칙(app/web/whisper-window.mjs)을 node로 확인한다."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1] / "app" / "web" / "whisper-window.mjs"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node 없음")


def plan(chunks, take, final, overlap=5):
    script = (f"import {{ planWindow }} from {json.dumps(MODULE.as_uri())};"
              f"console.log(JSON.stringify(planWindow({json.dumps(chunks)}, {take}, {json.dumps(final)}, {overlap})));")
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def seg(start, end, text):
    return {"timestamp": [start, end], "text": text}


def test_sentence_cut_at_window_end_is_left_for_next_window():
    # 27~30초 문장은 창 끝에서 잘렸다(end=None). 다음 창이 24초부터 다시 듣고 온전히 낸다.
    p = plan([seg(0, 10, " 첫 문장"), seg(10, 24, "둘째 문장"), seg(27, None, "잘린 문")], 30, False)
    assert [s["text"] for s in p["segments"]] == ["첫 문장", "둘째 문장"]
    assert p["advanceSeconds"] == 24


def test_segment_ending_inside_overlap_is_deferred():
    p = plan([seg(0, 20, "가"), seg(20, 28, "나")], 30, False)
    assert [s["text"] for s in p["segments"]] == ["가"] and p["advanceSeconds"] == 20


def test_final_window_emits_everything():
    p = plan([seg(0, 4, "가"), seg(4, None, "나"), seg(6, 7, "  ")], 8, True)
    assert p["segments"] == [{"start": 0, "end": 4, "text": "가"}, {"start": 4, "end": 8, "text": "나"}]
    assert p["advanceSeconds"] == 8


def test_one_long_segment_still_makes_progress():
    p = plan([seg(0, None, "쉬지 않고 이어지는 말")], 30, False)
    assert p["advanceSeconds"] == 25 and p["segments"][0]["end"] == 25
