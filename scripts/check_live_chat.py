"""Explicit, opt-in live smoke test. Uses synthetic text, never reads model credentials."""

import argparse
import json
import re
import time
from uuid import uuid4

import httpx


def ask(client, base_url, messages, *, stop_after=None):
    started = time.monotonic()
    text, chunks, first = "", 0, None
    with client.stream(
        "POST",
        f"{base_url}/api/chat",
        json={
            "request_id": str(uuid4()),
            "messages": messages,
        },
    ) as response:
        if response.status_code != 200:
            raise RuntimeError(f"Chat HTTP {response.status_code}")
        event = ""
        for line in response.iter_lines():
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
                if event == "error":
                    raise RuntimeError(f"Chat error: {data['code']}")
                if event == "delta":
                    chunks += 1
                    text += data["text"]
                    if first is None:
                        first = round(time.monotonic() - started, 2)
                    if stop_after and chunks >= stop_after:
                        return text, {
                            "chunks": chunks,
                            "first_text_seconds": first,
                            "stopped": True,
                        }
                if event == "done":
                    return text, {
                        "chunks": chunks,
                        "first_text_seconds": first,
                        "duration_seconds": round(time.monotonic() - started, 2),
                    }
    raise RuntimeError("Missing terminal event")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Confirm live, billable model requests")
    parser.add_argument("--base-url", default="http://127.0.0.1:9010")
    args = parser.parse_args()
    if not args.run:
        parser.error("Pass --run to make live model requests")
    with httpx.Client(trust_env=False, timeout=130) as client:
        first = {
            "role": "user",
            "content": "테스트 식별 단어는 푸른솔482입니다. "
            "이 단어를 기억했다고 한국어로 한 문장만 답하세요.",
        }
        answer, metrics = ask(client, args.base_url, [first])
        assert re.search("[가-힣]", answer), "No Korean response"
        print("Korean response:", metrics, flush=True)
        answer2, metrics = ask(
            client,
            args.base_url,
            [
                first,
                {"role": "assistant", "content": answer},
                {"role": "user", "content": "앞서 알려준 식별 단어만 답하세요."},
            ],
        )
        assert "푸른솔482" in answer2, "Context was not retained"
        print("Context retained:", metrics, flush=True)
        _, metrics = ask(
            client,
            args.base_url,
            [
                {
                    "role": "user",
                    "content": ("1부터 100까지 각각 일상에서 할 수 있는 일을 "
                                "한 문장씩 길게 써 주세요."),
                }
            ],
            stop_after=3,
        )
        print("Client stopped response:", metrics, flush=True)
        recovery, metrics = ask(
            client,
            args.base_url,
            [{"role": "user", "content": "새 요청입니다. 한국어로 짧게 인사해 주세요."}],
        )
        assert re.search("[가-힣]", recovery), "No response after stop"
        print("New request after stop:", metrics, flush=True)


if __name__ == "__main__":
    main()
