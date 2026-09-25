import pytest

from agent_service.calls.settings import CallSettings, normalize_number
from agent_service.calls.types import ProviderFailure


def values():
    return {
        "CALLS_ENABLED": "1",
        "CLAWOPS_ACCOUNT_ID": "ACtest",
        "CLAWOPS_API_KEY": "secret",
        "CLAWOPS_FROM_NUMBER": "07011112222",
        "CALL_REALTIME_BASE_URL": "https://example.openai.azure.com",
        "CALL_REALTIME_API_KEY": "audiosecret",
        "CALL_REALTIME_MODEL": "test-deployment",
        "CALL_ALLOWED_NUMBERS": "01000000001",
        "CALL_MAX_SECONDS": "180",
    }


def test_separate_settings_private_and_constrained(tmp_path):
    s = CallSettings.load(tmp_path / "missing", environ=values())
    assert s.max_seconds == 180 and s.allowed_numbers == ("01000000001",)
    assert "secret" not in repr(s) and "secret" not in repr(s.carrier)
    assert normalize_number("+82 10-0000-0001") == "01000000001"
    assert normalize_number("1588-5700") == "15885700"


@pytest.mark.parametrize(
    "key,value",
    [
        ("CALLS_ENABLED", "0"),
        ("CALL_MAX_SECONDS", "181"),
        ("CALL_MAX_SECONDS", "0"),
        ("CALL_REALTIME_BASE_URL", "https://evil.test"),
        ("CALL_ALLOWED_NUMBERS", ""),
        ("CLAWOPS_API_KEY", ""),
    ],
)
def test_invalid_call_configuration_cannot_enable_dial(tmp_path, key, value):
    env = values()
    env[key] = value
    with pytest.raises(ProviderFailure):
        CallSettings.load(tmp_path / "missing", environ=env)


@pytest.mark.parametrize("number", ["abc01000000001", "01000000001/evil", "+12025550123"])
def test_normalization_does_not_strip_arbitrary_text(number):
    with pytest.raises(ValueError):
        normalize_number(number)
