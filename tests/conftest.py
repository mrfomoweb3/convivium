import os

import pytest

for key in ("QLOO_API_KEY", "GROQ_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "LLM_MODEL", "MEM0_API_KEY", "RESEND_API_KEY",
            "WEBHOOK_URL"):
    os.environ[key] = ""
os.environ.update(QLOO_FIXTURES="1", WATCHER_ENABLED="0", SEED_DEMO="0")


@pytest.fixture(autouse=True)
def offline(tmp_path, monkeypatch):
    monkeypatch.setenv("CONVIVIUM_DB", str(tmp_path / "test.db"))
    from app import config, llm, memory, qloo, store

    config.reload_settings()
    llm.get_llm.cache_clear()
    qloo.set_client(None)
    memory.reset_memory()
    store.init_db()
    yield
    qloo.set_client(None)
    config.reload_settings()
