"""Build a local listening report from existing evals. No API calls or database access."""

import argparse
import html
import json
import re
import shutil
from pathlib import Path

from live_dialogue_cases import coverage

LAB = Path(__file__).resolve().parents[1] / "var/live-dialogue"


def build(labels):
    output = LAB / "listen"
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    sections = []
    for label in labels:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,60}", label):
            raise ValueError("invalid_label")
        cards = []
        for file in sorted((LAB / label).glob("*/result.json")):
            result = json.loads(file.read_text())
            checks = coverage(
                result["case"], result["turns"], result["transcripts"], result["errors"]
            )
            issues = [key for key, value in checks.items() if value is False]
            status = (
                "입력·전송 확인됨 · 내용은 별도 검토"
                if not issues
                else "평가 제한: " + ", ".join(issues)
            )
            audio_name = f"{label}-{file.parent.name}.wav"
            shutil.copyfile(file.parent / "conversation.wav", output / audio_name)
            lines = "".join(
                f"<p><strong>{'상대' if row['role'] == 'caller' else '도우미'}</strong> "
                f"{html.escape(row['text'])}</p>"
                for row in result["transcripts"]
            )
            end = result["report"].get("end_call") or {}
            cards.append(
                f"<article><h3>{html.escape(file.parent.name)}</h3>"
                f'<p class="status">{html.escape(status)}</p>'
                f"<p>평가 기준: {html.escape(result['review'])}</p>"
                f'<audio controls preload="metadata" src="{audio_name}"></audio>'
                f"<details><summary>전사와 종료 결과</summary>{lines}"
                f"<p>모사 종료: {html.escape(end.get('reason', '요청 없음'))} / "
                f"{html.escape(end.get('status', '미확인'))}</p>"
                f"<p>API 오류: {html.escape(', '.join(result['errors']) or '없음')}</p>"
                "</details></article>"
            )
        sections.append(
            f"<section><h2>{html.escape(label)}</h2>{''.join(cards)}</section>"
        )
    page = """<!doctype html><html lang="ko"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>한국어 음성 평가</title>
<style>body{max-width:940px;margin:40px auto;padding:0 20px;font:16px/1.7 system-ui;
color:#262626;background:#fff}
h1{font-size:28px}h2{margin-top:48px}h3{font-size:17px;margin:0}
article{padding:24px 0;border-top:1px solid #ddd}
p{margin:8px 0}.status{font-size:14px;color:#555}audio{display:block;width:100%;margin:16px 0}
summary{cursor:pointer;padding:8px 0}strong{display:inline-block;min-width:60px}
details p{white-space:pre-wrap}
</style><h1>한국어 음성 평가</h1><p>합성 상대 음성과 실제 gpt-live-1 · marin 응답을 비교합니다.
전화망은 연결하지 않았습니다. 음성 파일에는 합성 입력과 모사 전화 재생이 함께 들어 있습니다.</p>
<p>입력·전송 확인은 자연스러움의 합격 판정이 아닙니다. 전사 누락·화자 오류가 있을 수 있으며,
실제 DeepAgents의 판단을 사용하되 통신사 재생 확인과 종료는 모사했습니다.</p>"""
    (output / "index.html").write_text(page + "".join(sections) + "</html>")
    print(output / "index.html")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("labels", nargs="+")
    build(parser.parse_args().labels)
