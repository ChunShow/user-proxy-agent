import asyncio

import pytest
from test_live_store import setup_live
from test_native_audio import until


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
