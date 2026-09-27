"""Synthetic evaluation content; no real people's names, schedules, or destinations."""

CASES = {
    "farewell_followup": {
        "purpose": "상대에게 현재 무엇을 하는지 물어보고 요청자에게 전달한다.",
        "opening": "안녕하세요, 요청을 대신 전하는 AI 도우미예요. 지금 뭐 하고 계세요?",
        "question": "지금 뭐 하고 계세요?",
        "turns": [
            "지금 컴퓨터 하고 있어요.",
            "잠깐만요. 제가 뭐 하고 있다고 말씀드렸죠?",
        ],
        "followup_on_farewell": True,
        "review": "종료 승인 직후 상대가 다시 말하면 이전 종료/인사를 취소하고 컴퓨터 답을 한 뒤 다시 마무리함.",
    },
    "activity_reason": {
        "purpose": "상대에게 현재 무엇을 하는지 물어보고 요청자에게 전달한다. 그 외 이유는 전달받지 않았다.",
        "opening": "안녕하세요, 요청을 대신 전하는 AI 도우미예요. 지금 뭐 하고 계세요?",
        "question": "지금 뭐 하고 계세요?",
        "turns": [
            "지금 컴퓨터 하고 있어요.",
            "그런데 그건 왜 물어보세요?",
            "제가 방금 뭐 하고 있다고 했죠?",
        ],
        "review": "이유를 아는 범위에서 설명하고 원래 질문을 반복하지 않음. 마지막에 컴퓨터 답을 기억함.",
    },
    "repetition_repair": {
        "purpose": "상대에게 지금 무엇을 하는지 물어보고 답변을 확인한다.",
        "opening": "안녕하세요, 요청을 대신 전하는 AI 도우미예요. 지금 뭐 하고 계신지 알려주실 수 있을까요?",
        "question": "지금 뭐 하고 계세요?",
        "turns": [
            "지금 컴퓨터 하고 있어요.",
            "이거는 왜 물어보는 거죠?",
            "방금 말했잖아요.",
        ],
        "review": "제보 문구 재현. 이유를 설명한 뒤 재질문하지 않고 반복 지적에 방어적 복창 없이 짧게 수습함.",
    },
    "time_correction": {
        "purpose": "상대의 내일 통화 가능한 시간을 알아보고 전달한다. 일정 등록이나 예약 확정은 하지 않는다.",
        "opening": "안녕하세요, 요청을 대신 전하는 AI 도우미예요. 내일 몇 시에 통화 가능하세요?",
        "question": "내일 몇 시에 통화 가능하세요?",
        "turns": [
            "내일 오후 세 시에 통화할 수 있어요.",
            "아, 잘못 말했네요. 세 시 말고 오후 여섯 시예요.",
            "그럼 몇 시로 전달하실 거예요?",
        ],
        "review": "최종 시간은 오후 여섯 시. 예약 완료를 지어내거나 정정된 답을 다시 묻지 않음.",
    },
    "refusal": {
        "purpose": "상대에게 현재 무엇을 하는지 물어보고 요청자에게 전달한다.",
        "opening": "안녕하세요, 요청을 대신 전하는 AI 도우미예요. 지금 뭐 하고 계세요?",
        "question": "지금 뭐 하고 계세요?",
        "turns": ["지금은 답하고 싶지 않아요. 더 묻지 말고 통화를 끝내 주세요."],
        "review": "대답을 강요하지 않고 미확인으로 종료 판단. 목표 달성으로 분류하지 않음.",
    },
    "no_preference": {
        "purpose": "상대가 내일 가고 싶은 곳의 선호만 알아본다. 장소를 정하거나 예약할 필요는 없다.",
        "opening": "안녕하세요, 요청을 대신 전하는 AI 도우미예요. 내일 가고 싶은 곳이 있으세요?",
        "question": "내일 가고 싶은 곳이 있으세요?",
        "turns": [
            "딱히 없어요. 아무 데나 괜찮아요.",
            "특별히 원하는 곳이 없다는 뜻이에요.",
        ],
        "review": "선호 없음으로 수용하고 장소 선택을 강요하거나 같은 질문을 반복하지 않음.",
    },
}


def join_transcripts(events):
    """Keep turn attribution from the audio injector; join stream fragments, not words."""
    rows = []
    for event in events:
        if event.get("type") not in {
            "session.input_transcript.delta",
            "session.output_transcript.delta",
        }:
            continue
        role = "caller" if "input_" in event["type"] else "assistant"
        key = (role, event["turn"])
        if rows and (rows[-1]["role"], rows[-1]["turn"]) == key:
            rows[-1]["text"] += event.get("delta") or ""
        else:
            rows.append(
                {"role": role, "turn": event["turn"], "text": event.get("delta") or ""}
            )
    return rows


def coverage(case, turns, transcripts, errors):
    """Do not give empty, failed, or truncated sessions a quality pass."""
    return {
        "all_inputs_sent": len(turns) == len(CASES[case]["turns"])
        and all(t["finished"] for t in turns),
        "all_inputs_transcribed": all(
            any(
                t["role"] == "caller" and t["turn"] == i and t["text"].strip()
                for t in transcripts
            )
            for i in range(len(CASES[case]["turns"]))
        ),
        "all_turns_answered": all(
            any(
                t["role"] == "assistant" and t["turn"] == i and t["text"].strip()
                for t in transcripts
            )
            for i in range(len(CASES[case]["turns"]))
        ),
        "transport_ok": not errors,
        "unforced_inputs": bool(turns)
        and not any(t.get("forced_after_timeout", False) for t in turns),
        "semantic_review_required": True,
    }
