from __future__ import annotations

import os

import pytest
from langchain_core.messages import HumanMessage

from coding_agent.config import load_settings
from coding_agent.providers.kimi import create_kimi_client


@pytest.mark.live
def test_kimi_returns_a_real_streamed_response() -> None:
    if os.getenv("RUN_KIMI_LIVE_TEST") != "1":
        pytest.skip("set RUN_KIMI_LIVE_TEST=1 to send a real Kimi request")

    client = create_kimi_client(load_settings())
    chunks = client.stream([HumanMessage(content="只回复四个字：连接成功")])
    response = "".join(
        chunk.content for chunk in chunks if isinstance(chunk.content, str)
    )

    assert response.strip()
