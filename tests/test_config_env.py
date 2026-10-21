from app import config


def test_openai_provider_reads_env_file_and_environment_takes_precedence(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ROOT", tmp_path)
    for key in ("VISUALIZE_OPENAI_KEY", "OPENAI_API_KEY", "VISUALIZE_OPENAI_BASE_URL"):
        monkeypatch.delenv(key, raising=False)
    (tmp_path / ".env").write_text('VISUALIZE_OPENAI_KEY="fixture-only"\nVISUALIZE_OPENAI_BASE_URL=https://fixture.invalid/v1/\n')
    settings = config.get_settings()
    assert settings.openai_key == "fixture-only"
    assert settings.openai_base_url == "https://fixture.invalid/v1"
    monkeypatch.setenv("VISUALIZE_OPENAI_KEY", "environment-fixture")
    assert config.get_settings().openai_key == "environment-fixture"
