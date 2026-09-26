import asyncio

import pytest
from test_live_store import setup_live
from test_native_audio import until


@pytest.mark.asyncio
async def test_new_conditions_reject_old_tools_and_discard_old_end_request(tmp_path):
    from test_call_instructions import setup_instructions

    from agent_service.calls.delegation import DelegationCoordinator
    from agent_service.storage import StoreError

    _, calls, _, _, ctx, call = setup_instructions(tmp_path)
    ready = asyncio.Event()
    rejected = []

    class Bridge:
        transcripts = [{"role": "caller", "text": "문의"}]
        ending = None

        async def command(self, *args):
            pass

        async def end_call(self, *args, **kwargs):
            pytest.fail("old end request applied")

        async def send_dtmf(self, *args):
            pytest.fail("old DTMF applied")

    async def run(context, tools):
        by_name = {t.name: t for t in tools}
        end = {"reason": "goal_achieved", "summary": "이전 조건 완료"}
        await by_name["end_call"].ainvoke(end)
        ready.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            for name, args in [("end_call", end), ("send_dtmf", {"digit": "4"})]:
                with pytest.raises(StoreError):
                    await by_name[name].ainvoke(args)
                rejected.append(name)
            return "오래된 완료 안내"

    coordinator = DelegationCoordinator(calls, call, Bridge(), runner=run)
    await coordinator.request("old")
    await ready.wait()
    await coordinator.submit(ctx, "가격도 추가로 확인")
    await until(lambda: len(rejected) == 2)
    await until(lambda: calls.get(ctx.owner, call)["instructions"][0]["status"] == "delivered")
    await coordinator.close()


@pytest.mark.asyncio
async def test_voice_delegation_waits_for_user_then_returns_verified_answer(tmp_path):
    from agent_service.calls.delegation import DelegationCoordinator

    _, calls, store, owner, call = setup_live(tmp_path)
    output = []

    async def run(context, tools):
        question = next(t for t in tools if t.name == "ask_user")
        answer = await question.ainvoke(
            {"question": "화요일 3시 가능한가요?", "options": ["가능", "불가"]}
        )
        assert answer["source"] == "requesting_user"
        return f"사용자가 답했습니다: {answer['answer']}"

    class Bridge:
        transcripts = [{"role": "caller", "text": "화요일 세 시는 어떠세요?"}]

        async def command(self, kind, content, delegation_id=None):
            output.append((kind, content, delegation_id))

        async def deliver_result(self, content, delegation_id):
            await self.command("session.commentary.append", content, delegation_id)

        async def send_dtmf(self, digit):
            pytest.fail("unexpected DTMF")

        async def end_call(self, reason, summary):
            pytest.fail("unexpected hangup")

    coordinator = DelegationCoordinator(calls, call, Bridge(), runner=run)
    await coordinator.request("d1")
    await until(lambda: bool(store.activity(owner, call)["questions"]))
    q = store.activity(owner, call)["questions"][0]
    assert q["status"] == "pending"
    store.answer(owner, call, q["id"], "가능", q["revision"], "r1")
    await until(lambda: store.activity(owner, call)["questions"][0]["status"] == "applied")
    assert any("가능" in text and did == "d1" for _, text, did in output)
    await coordinator.close()


@pytest.mark.asyncio
async def test_stop_cancels_question_and_drops_late_result(tmp_path):
    from agent_service.calls.delegation import DelegationCoordinator

    _, calls, store, owner, call = setup_live(tmp_path)
    sent = []
    gate = asyncio.Event()
    started = asyncio.Event()

    async def run(context, tools):
        started.set()
        await gate.wait()
        return "늦은 결과"

    class Bridge:
        transcripts = [{"role": "caller", "text": "질문"}]

        async def command(self, *args):
            sent.append(args)

    coordinator = DelegationCoordinator(calls, call, Bridge(), runner=run)
    await coordinator.request("d1")
    await started.wait()
    calls.request_stop(owner, call)
    gate.set()
    await asyncio.sleep(0.03)
    await coordinator.close()
    assert not sent
    assert not store.current(call, "d1", 1)


@pytest.mark.asyncio
async def test_end_request_includes_backend_answer_before_farewell(tmp_path):
    from agent_service.calls.delegation import DelegationCoordinator

    _, calls, store, _, call = setup_live(tmp_path)
    sent = []

    class Bridge:
        transcripts = [{"role": "caller", "text": "예약 시간은 4시입니다"}]
        ending = None

        async def end_call(self, reason, summary, *, spoken_result=""):
            sent.append((reason, summary, spoken_result))

        async def command(self, *args):
            pass

    async def run(context, tools):
        assert context["timezone"] == "Asia/Seoul"
        await next(t for t in tools if t.name == "end_call").ainvoke(
            {"reason": "goal_achieved", "summary": "4시 확인"}
        )
        assert not sent
        return "오후 4시로 확인했습니다."

    coordinator = DelegationCoordinator(calls, call, Bridge(), runner=run)
    await coordinator.request("d1")
    await until(lambda: bool(sent))
    assert sent == [("goal_achieved", "4시 확인", "오후 4시로 확인했습니다.")]
    await coordinator.close()


@pytest.mark.asyncio
async def test_instruction_ack_is_independent_and_next_delegation_keeps_conditions(tmp_path):
    import json

    from test_call_instructions import setup_instructions
    from test_native_audio import Socket

    from agent_service.calls.delegation import DelegationCoordinator
    from agent_service.calls.live_bridge import LiveBridge

    _, calls, _, store, ctx, call = setup_instructions(tmp_path)
    model = Socket()
    bridge = LiveBridge(model)
    bridge.transcripts = [{"role": "caller", "text": "일정을 확인해주세요"}]
    contexts = []

    async def run(context, tools):
        contexts.append(context)
        return "새 조건을 확인합니다"

    coordinator = DelegationCoordinator(calls, call, bridge, runner=run)
    bridge.coordinator = coordinator
    receive = asyncio.create_task(bridge.receive())
    try:
        item = await coordinator.submit(ctx, "오후 6시로 변경")
        await until(lambda: bool(model.sent))
        assert calls.get(ctx.owner, call)["instructions"][0]["status"] == "sending"
        # The receive loop must not wait for the lock held by instruction delivery.
        await model.input.put(
            {
                "type": "session.delegation.created",
                "delegation": {"id": "during-update", "target": "client"},
            }
        )
        cursor = 0

        async def acknowledge_until(predicate):
            nonlocal cursor
            async with asyncio.timeout(2):
                while not predicate():
                    while cursor < len(model.sent):
                        event = model.sent[cursor]
                        cursor += 1
                        if event["type"].endswith(".append"):
                            await model.input.put(
                                {"type": event["type"] + "ed", "client_event_id": event["event_id"]}
                            )
                    await asyncio.sleep(0.001)

        await acknowledge_until(lambda: bool(contexts))
        assert store.delivered(call)[0]["id"] == item["id"]
        assert contexts[0]["requesting_user_instructions"] == ["오후 6시로 변경"]
        assert any("오후 6시" in json.dumps(e, ensure_ascii=False) for e in model.sent)
        assert (await coordinator.submit(ctx, "오후 6시로 변경"))["id"] == item["id"]
        await acknowledge_until(lambda: coordinator.task.done())
    finally:
        receive.cancel()
        await asyncio.gather(receive, return_exceptions=True)
        await coordinator.close()


@pytest.mark.asyncio
async def test_new_instruction_cancels_old_question_and_old_runner_output(tmp_path):
    from test_call_instructions import setup_instructions

    from agent_service.calls.delegation import DelegationCoordinator

    _, calls, live, _, ctx, call = setup_instructions(tmp_path)
    output = []

    class Bridge:
        transcripts = [{"role": "caller", "text": "3시 가능?"}]
        ending = None

        async def command(self, *args):
            output.append(args)

        async def deliver_result(self, *args):
            output.append(("OLD_RESULT", *args))

    async def run(context, tools):
        try:
            await next(t for t in tools if t.name == "ask_user").ainvoke(
                {"question": "3시?", "options": []}
            )
        except asyncio.CancelledError:
            return "취소를 무시한 오래된 결과"
        return "이전 조건 결과"

    coordinator = DelegationCoordinator(calls, call, Bridge(), runner=run)
    await coordinator.request("old")
    await until(lambda: bool(live.activity(ctx.owner, call)["questions"]))
    await coordinator.submit(ctx, "6시로 변경")
    await until(lambda: calls.get(ctx.owner, call)["instructions"][0]["status"] == "delivered")
    assert live.activity(ctx.owner, call)["questions"][0]["status"] == "canceled"
    assert not any(e[0] == "OLD_RESULT" for e in output)
    await coordinator.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("close", [True, False])
async def test_instruction_missing_ack_is_unknown_and_never_replayed(tmp_path, close):
    from test_call_instructions import setup_instructions

    from agent_service.calls.delegation import DelegationCoordinator

    _, calls, live, _, ctx, call = setup_instructions(tmp_path)
    sent = []
    gate = asyncio.Event()

    class Bridge:
        ending = None
        transcripts = [{"role": "caller", "text": "문의"}]

        async def command(self, *args):
            sent.append(args)
            await gate.wait()
            raise TimeoutError

    coordinator = DelegationCoordinator(calls, call, Bridge())
    item = await coordinator.submit(ctx, "새 조건")
    await until(lambda: bool(sent))
    if close:
        calls.request_stop(ctx.owner, call)
        await coordinator.close()
    else:
        gate.set()
    await until(
        lambda: calls.get(ctx.owner, call)["instructions"][0]["status"] == "delivery_unknown"
    )
    assert live.begin(call, "no_old_conditions") is None
    assert (await coordinator.submit(ctx, "새 조건"))["id"] == item["id"]
    assert len(sent) == 1
    await coordinator.close()


@pytest.mark.asyncio
async def test_instruction_rejected_when_farewell_is_pending(tmp_path):
    from test_call_instructions import setup_instructions

    from agent_service.calls.delegation import DelegationCoordinator
    from agent_service.storage import StoreError

    _, calls, _, _, ctx, call = setup_instructions(tmp_path)

    class Bridge:
        ending = {"status": "waiting_for_playback"}

    coordinator = DelegationCoordinator(calls, call, Bridge())
    with pytest.raises(StoreError, match="call_instruction_inactive"):
        await coordinator.submit(ctx, "새 조건")
    assert calls.get(ctx.owner, call)["instructions"] == []
    await coordinator.close()


@pytest.mark.asyncio
async def test_canceled_answer_is_not_presented_as_confirmed_in_future_context(tmp_path):
    from test_call_instructions import setup_instructions

    from agent_service.calls.delegation import DelegationCoordinator

    _, calls, live, instructions, ctx, call = setup_instructions(tmp_path)
    revision = live.begin(call, "old")
    question = live.ask(call, "old", revision, "3시 가능한가요?", [])
    live.answer(ctx.owner, call, question["id"], "3시 가능", revision, "answer-1")
    item = instructions.submit(ctx, call, "6시로 변경")
    instructions.transition(call, item["id"], "pending", "sending")
    instructions.transition(call, item["id"], "sending", "delivered")
    contexts = []

    class Bridge:
        transcripts = [{"role": "caller", "text": "시간 확인"}]

        async def deliver_result(self, *args):
            pass

    async def run(context, tools):
        contexts.append(context)
        return "새 시간 확인"

    coordinator = DelegationCoordinator(calls, call, Bridge(), runner=run)
    await coordinator.request("new")
    await until(lambda: bool(contexts))
    await coordinator.close()
    assert contexts[0]["requesting_user_answers"] == []
    assert contexts[0]["requesting_user_instructions"] == ["6시로 변경"]


@pytest.mark.asyncio
async def test_connected_calendar_delegation_shares_only_busy_intervals(tmp_path):
    import time

    import httpx

    from agent_service.calls.delegation import DelegationCoordinator
    from agent_service.integrations.google import GoogleManager
    from agent_service.integrations.settings import READ_SCOPES

    db, calls, live, owner, call = setup_live(tmp_path)

    def provider(request):
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "summary": "CONFIDENTIAL",
                        "description": "PRIVATE",
                        "start": {"dateTime": "2026-09-27T14:00:00+09:00"},
                        "end": {"dateTime": "2026-09-27T15:00:00+09:00"},
                    },
                    {
                        "summary": "FREE",
                        "transparency": "transparent",
                        "start": {"date": "2026-09-27"},
                        "end": {"date": "2026-09-28"},
                    },
                ]
            },
        )

    google = GoogleManager(db, transport=httpx.MockTransport(provider))
    google.store.save(
        owner,
        0,
        {"access_token": "fake", "refresh_token": "fake", "expires_at": time.time() + 3600},
        READ_SCOPES,
        "person@example.test",
    )
    results = []
    outputs = []

    class Bridge:
        transcripts = [{"role": "caller", "text": "두 시는 가능한가요?"}]

        async def deliver_result(self, *args):
            outputs.append(args)

    async def runner(context, tools):
        assert "read_email" not in {t.name for t in tools}
        t = next(t for t in tools if t.name == "check_calendar_availability")
        result = await t.ainvoke(
            {"start": "2026-09-27T14:00:00+09:00", "end": "2026-09-27T15:00:00+09:00"}
        )
        results.append(result)
        return "해당 시간에 일정이 있습니다."

    coordinator = DelegationCoordinator(calls, call, Bridge(), runner=runner, integrations=google)
    await coordinator.request("calendar")
    await until(lambda: bool(outputs))
    await coordinator.close()
    assert len(results[0]["busy"]) == 1 and results[0]["available"] is False
    assert "CONFIDENTIAL" not in str(results) and "PRIVATE" not in str(results)


@pytest.mark.asyncio
async def test_quiet_completion_review_has_only_end_tool_and_drops_stale_speech(tmp_path):
    from agent_service.calls.delegation import DelegationCoordinator

    _, calls, store, owner, call = setup_live(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    ended = []

    class Bridge:
        transcripts = [
            {"role": "caller", "text": "네 확인했습니다."},
            {"role": "assistant", "text": "감사합니다."},
        ]
        transcript_version = 2
        input_revision = 1
        ending = None

        async def end_call(self, *args, **kwargs):
            ended.append(args)

    async def run(context, tools):
        assert context["completion_review"] is True
        assert [t.name for t in tools] == ["end_call"]
        await tools[0].ainvoke({"reason": "goal_achieved", "summary": "확인 완료"})
        entered.set()
        await release.wait()
        return "확인했습니다."

    bridge = Bridge()
    coordinator = DelegationCoordinator(calls, call, bridge, runner=run)
    assert await coordinator.review_completion(2) is True
    await entered.wait()
    bridge.input_revision += 1  # Caller resumed while backend judgment was running.
    release.set()
    await coordinator.task
    assert not ended
    assert any(
        e["kind"] == "completion_review_discarded" for e in store.activity(owner, call)["events"]
    )
    bridge.transcript_version += 1
    assert await coordinator.review_completion(3) is True
    await coordinator.task
    assert len(ended) == 1
    await coordinator.close()


@pytest.mark.asyncio
async def test_completion_review_does_not_replace_pending_user_confirmation(tmp_path):
    from agent_service.calls.delegation import DelegationCoordinator

    _, calls, store, owner, call = setup_live(tmp_path)

    class Bridge:
        transcripts = [{"role": "caller", "text": "가능한가요?"}]
        ending = None

        async def command(self, *args):
            pass

    async def run(context, tools):
        await next(t for t in tools if t.name == "ask_user").ainvoke(
            {"question": "가능한가요?", "options": ["네", "아니요"]}
        )
        return "확인"

    c = DelegationCoordinator(calls, call, Bridge(), runner=run)
    await c.request("d-question")
    await until(lambda: bool(store.activity(owner, call)["questions"]))
    assert await c.review_completion(1) is False
    assert store.activity(owner, call)["questions"][0]["status"] == "pending"
    await c.close()
