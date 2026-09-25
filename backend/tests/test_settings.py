import pytest


def test_file_allowlist_overrides_and_secret_repr(tmp_path):
    from agent_service.settings import load_settings

    path = tmp_path / ".env"
    path.write_text(
        "MODEL_BASE_URL=https://model.test/v1\nMODEL_NAME=test-model\n"
        "MODEL_API_KEY=file-secret\nPROXY_FAKE_MODEL=1\nMODEL_MAX_TOKENS=\n"
    )
    settings = load_settings(path, environ={"MODEL_API_KEY": "env-secret"})
    assert settings.model_name == "test-model"
    assert settings.api_key.get_secret_value() == "env-secret"
    assert settings.max_tokens == 2048
    assert "secret" not in repr(settings)
    assert not hasattr(settings, "PROXY_FAKE_MODEL")


@pytest.mark.parametrize(
    "url",
    [
        "http://remote.test/v1",
        "https://user:pass@model.test",
        "https://model.test?key=x",
        "https://model.test/#x",
        "bad",
    ],
)
def test_invalid_model_url_is_sanitized(url):
    from agent_service.settings import SettingsError, load_settings

    with pytest.raises(SettingsError) as error:
        load_settings(
            environ={"MODEL_BASE_URL": url, "MODEL_NAME": "test", "MODEL_API_KEY": "private-key"},
            path="/nonexistent",
        )
    assert url not in str(error.value)
    assert "private-key" not in str(error.value)


def test_missing_and_invalid_config_do_not_fall_back_to_other_projects(tmp_path):
    from agent_service.settings import SettingsError, load_settings

    with pytest.raises(SettingsError):
        load_settings(tmp_path / "missing", environ={})
    for overrides in [{"MODEL_MAX_TOKENS": "0"}, {"MODEL_TRUST_ENV": "maybe"}]:
        with pytest.raises(SettingsError):
            load_settings(
                tmp_path / "missing",
                environ={
                    "MODEL_BASE_URL": "http://127.0.0.1:1234/v1",
                    "MODEL_NAME": "test",
                    "MODEL_API_KEY": "secret",
                    **overrides,
                },
            )
