import pytest
from test_native_audio import Socket, delta

from agent_service.calls.preflight import legacy_agent_running, probe_audio
from agent_service.calls.types import ProviderFailure


def test_only_existing_experiment_runtimes_block_shared_sender():
    assert legacy_agent_running("123 /x/calling-agent/.venv/bin/python -m calling_agent.cli")
    assert legacy_agent_running("123 python -m calling_agent.cli talk")
    assert not legacy_agent_running("123 /x/agent-service/backend/.venv/bin/python dev.py")
    assert not legacy_agent_running("123 /bin/zsh -c cat /x/calling-agent/README.md")


@pytest.mark.asyncio
async def test_audio_probe_requires_actual_pcmu_audio_and_completed_response():
    socket = Socket()
    await socket.input.put(delta())
    await socket.input.put({"type": "response.done", "response": {"status": "completed"}})
    assert await probe_audio(socket) == 160
    socket = Socket()
    await socket.input.put({"type": "response.done", "response": {"status": "completed"}})
    with pytest.raises(ProviderFailure):
        await probe_audio(socket)
