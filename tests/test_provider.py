from coding_agent.config import Settings
from coding_agent.providers.kimi import create_kimi_client


def test_kimi_client_uses_fixed_chat_completions_configuration(settings: Settings) -> None:
    client = create_kimi_client(settings)

    assert client.model_name == "test-kimi-model"
    assert str(client.openai_api_base).rstrip("/") == "https://api.moonshot.cn/v1"
    assert client.streaming is True
    assert client.max_retries == 2
    assert client.request_timeout == 60
    assert client.use_responses_api is False
    assert client.openai_api_key.get_secret_value() == "test-secret-key"
