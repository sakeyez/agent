"""Fixed Kimi provider built on its OpenAI-compatible chat API."""

from langchain_openai import ChatOpenAI

from coding_agent.config import Settings


def create_kimi_client(settings: Settings) -> ChatOpenAI:
    """Create the only model client supported in stage one."""

    return ChatOpenAI(
        api_key=settings.kimi_api_key,
        base_url=str(settings.kimi_base_url).rstrip("/"),
        model=settings.kimi_model,
        streaming=True,
        timeout=60,
        max_retries=2,
        use_responses_api=False,
    )
