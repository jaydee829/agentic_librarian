"""#100: grounded-scout + embedding metering. Fake response objects — no network, no DB
(record_llm_call is monkeypatched to a recorder)."""

from types import SimpleNamespace

import pytest

from agentic_librarian.scouts import grounded_llm


class _Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, vendor, model, input_tokens, output_tokens, conversation_id=None):
        self.calls.append((vendor, model, input_tokens, output_tokens))


@pytest.mark.parametrize(
    "usage,expected",
    [
        (SimpleNamespace(prompt_token_count=120, candidates_token_count=45), [("gemini", "gemini-2.5-flash", 120, 45)]),
        (SimpleNamespace(prompt_token_count=None, candidates_token_count=None), [("gemini", "gemini-2.5-flash", 0, 0)]),
        (None, []),  # no usage_metadata -> no row, no crash
    ],
)
def test_record_gemini_usage(monkeypatch, usage, expected):
    rec = _Recorder()
    monkeypatch.setattr(grounded_llm, "record_llm_call", rec)
    response = SimpleNamespace(text="ok", usage_metadata=usage)
    grounded_llm._record_gemini_usage("gemini-2.5-flash", response)
    assert rec.calls == expected


def test_embed_metering_records_only_on_cache_miss(monkeypatch):
    from agentic_librarian.scouts import utils

    rec = _Recorder()
    monkeypatch.setattr(utils, "record_llm_call", rec)

    fake_resp = SimpleNamespace(embeddings=[SimpleNamespace(values=[0.0] * 4)])
    client = SimpleNamespace(models=SimpleNamespace(embed_content=lambda **kw: fake_resp))
    monkeypatch.setattr(utils, "get_shared_genai_client", lambda: client)

    utils.get_cached_embedding.cache_clear()
    utils.get_cached_embedding("m", "some tag text!!")  # miss -> one row
    utils.get_cached_embedding("m", "some tag text!!")  # hit -> no new row
    assert rec.calls == [("gemini", "m", len("some tag text!!") // 4, 0)]
