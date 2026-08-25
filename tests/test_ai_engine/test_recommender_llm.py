"""Tests for the LLM-enhanced recommender path and its failure reporting.

The point of these tests is that the LLM path must never degrade *silently*:
whenever it falls back to the rule-based scorer, the result has to say so and
carry a reason a UI can show.
"""

import json

import pytest

from aquascope.ai_engine.knowledge_base import METHODOLOGIES
from aquascope.ai_engine.recommender import (
    HOSTED_DEFAULT_HF_MODEL,
    PROVIDER_BASE_URLS,
    PROVIDER_MODELS,
    DatasetProfile,
    LLMOutputError,
    RecommendationResult,
    _coerce_items,
    _describe_llm_error,
    _detect_provider,
    _parse_llm_output,
    hosted_llm_config,
    recommend_with_llm,
    recommend_with_llm_detailed,
)

_HOSTED_ENV = (
    "AQUASCOPE_LLM_API_KEY",
    "AQUASCOPE_LLM_BASE_URL",
    "AQUASCOPE_LLM_MODEL",
    "HF_TOKEN",
    "HUGGING_FACE_HUB_TOKEN",
)


@pytest.fixture
def clean_env(monkeypatch):
    """Start from an environment with no deployment-supplied credentials."""
    for key in _HOSTED_ENV:
        monkeypatch.delenv(key, raising=False)
    return monkeypatch

PROFILE = DatasetProfile(
    parameters=["DO", "BOD5", "pH"],
    n_records=500,
    n_stations=8,
    time_span_years=6.0,
    geographic_scope="Taiwan",
    research_goal="trend analysis",
)


def _ids(n=3):
    return [m.id for m in METHODOLOGIES[:n]]


class TestProviderDetection:
    @pytest.mark.parametrize(
        ("base_url", "expected"),
        [
            (None, "openai"),
            ("https://api.openai.com/v1", "openai"),
            ("https://router.huggingface.co/v1", "huggingface"),
            # The retired host must still be recognised as HuggingFace so old
            # saved configs produce an HF-flavoured error, not an OpenAI one.
            ("https://api-inference.huggingface.co/v1/", "huggingface"),
            ("https://api.groq.com/openai/v1", "groq"),
            ("http://localhost:11434/v1", "ollama"),
            ("http://127.0.0.1:11434/v1", "ollama"),
            ("https://my-vllm.internal/v1", "openai-compatible"),
        ],
    )
    def test_detect_provider(self, base_url, expected):
        assert _detect_provider(base_url) == expected

    def test_huggingface_base_url_is_the_live_host(self):
        # api-inference.huggingface.co no longer resolves.
        assert PROVIDER_BASE_URLS["huggingface"] == "https://router.huggingface.co/v1"


class TestParsing:
    def test_parses_bare_array(self):
        raw = json.dumps([{"id": _ids(1)[0], "score": 88, "rationale": "fits"}])
        recs = _parse_llm_output(raw, top_k=5)
        assert len(recs) == 1
        assert recs[0].score == 88.0
        assert recs[0].rationale == "fits"

    def test_parses_recommendations_object(self):
        raw = json.dumps({"recommendations": [{"id": i, "score": 70} for i in _ids(3)]})
        assert len(_parse_llm_output(raw, top_k=5)) == 3

    def test_parses_object_with_renamed_wrapper_key(self):
        # json_object mode makes models invent their own wrapper key.
        raw = json.dumps({"methodologies": [{"id": i, "score": 60} for i in _ids(2)]})
        assert len(_parse_llm_output(raw, top_k=5)) == 2

    def test_parses_json_wrapped_in_code_fence_and_prose(self):
        raw = "Sure! Here you go:\n```json\n" + json.dumps(
            {"recommendations": [{"id": _ids(1)[0], "score": 50}]}
        ) + "\n```"
        assert len(_parse_llm_output(raw, top_k=5)) == 1

    def test_respects_top_k(self):
        raw = json.dumps({"recommendations": [{"id": i, "score": 50} for i in _ids(5)]})
        assert len(_parse_llm_output(raw, top_k=2)) == 2

    def test_tolerates_non_numeric_score(self):
        raw = json.dumps([{"id": _ids(1)[0], "score": "high"}])
        assert _parse_llm_output(raw, top_k=5)[0].score == 50.0

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "I'm sorry, I cannot help with that.",
            "{not json at all",
            json.dumps({"note": "no list here"}),
            json.dumps([{"id": "no_such_methodology", "score": 90}]),
        ],
    )
    def test_unusable_output_raises(self, raw):
        with pytest.raises(LLMOutputError):
            _parse_llm_output(raw, top_k=5)

    def test_missing_id_does_not_raise_keyerror(self):
        raw = json.dumps([{"score": 90, "rationale": "no id field"}])
        with pytest.raises(LLMOutputError):
            _parse_llm_output(raw, top_k=5)

    def test_coerce_items_handles_single_unwrapped_object(self):
        assert _coerce_items({"id": "x", "score": 1}) == [{"id": "x", "score": 1}]


class TestErrorMessages:
    @pytest.mark.parametrize(
        ("message", "needle"),
        [
            ("Error code: 401 - invalid_api_key", "authentication failed"),
            ("Error code: 429 - rate limit reached", "rate limit"),
            ("Error code: 404 - model_not_found", "does not serve the model"),
            ("Request timed out.", "timeout"),
            ("[Errno 8] nodename nor servname provided", "Could not reach"),
        ],
    )
    def test_maps_common_failures_to_readable_text(self, message, needle):
        described = _describe_llm_error(Exception(message), "groq", "llama-3.1-8b-instant")
        assert needle in described

    def test_output_error_is_reported_verbatim(self):
        described = _describe_llm_error(
            LLMOutputError("the model returned an empty reply"), "openai", "gpt-4o-mini"
        )
        assert "empty reply" in described


class TestFallbackReporting:
    def test_missing_openai_package_is_reported(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "openai":
                raise ImportError("No module named 'openai'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        result = recommend_with_llm_detailed(PROFILE, top_k=3, api_key="sk-test")
        assert result.mode == "rule_based"
        assert result.recommendations          # still useful output
        assert "openai" in result.error
        assert "aquascope[llm]" in result.error

    def test_transport_failure_is_reported(self, monkeypatch):
        import aquascope.ai_engine.recommender as rec

        def boom(*_args, **_kwargs):
            raise RuntimeError("Error code: 401 - invalid_api_key")

        monkeypatch.setattr(rec, "_call_openai_compatible", boom)

        result = recommend_with_llm_detailed(
            PROFILE, top_k=3, base_url="https://api.groq.com/openai/v1", api_key="bad"
        )
        assert result.mode == "rule_based"
        assert result.provider == "groq"
        assert "authentication failed" in result.error
        assert not result.used_llm

    def test_successful_llm_call_is_flagged_as_llm(self, monkeypatch):
        import aquascope.ai_engine.recommender as rec

        payload = json.dumps(
            {"recommendations": [{"id": i, "score": 91, "rationale": "r"} for i in _ids(2)]}
        )
        monkeypatch.setattr(rec, "_call_openai_compatible", lambda *a, **k: payload)

        result = recommend_with_llm_detailed(PROFILE, top_k=2, api_key="sk-test")
        assert result.used_llm
        assert result.mode == "llm"
        assert result.error == ""
        assert [r.score for r in result.recommendations] == [91.0, 91.0]

    def test_unparseable_reply_falls_back_with_reason(self, monkeypatch):
        import aquascope.ai_engine.recommender as rec

        monkeypatch.setattr(rec, "_call_openai_compatible", lambda *a, **k: "no json here")

        result = recommend_with_llm_detailed(PROFILE, top_k=3, api_key="sk-test")
        assert result.mode == "rule_based"
        assert "did not return JSON" in result.error

    def test_ollama_uses_native_endpoint(self, monkeypatch):
        import aquascope.ai_engine.recommender as rec

        seen = {}

        def fake_native(base_url, model, *_args, **_kwargs):
            seen["base_url"] = base_url
            seen["model"] = model
            return json.dumps({"recommendations": [{"id": _ids(1)[0], "score": 80}]})

        monkeypatch.setattr(rec, "_call_ollama_native", fake_native)

        result = recommend_with_llm_detailed(
            PROFILE, top_k=1, model="mistral", base_url="http://localhost:11434/v1"
        )
        assert result.used_llm
        assert result.provider == "ollama"
        assert seen == {"base_url": "http://localhost:11434/v1", "model": "mistral"}


class TestHostedConfig:
    """A deployment can supply one token so visitors need no account."""

    def test_none_without_credentials(self, clean_env):
        assert hosted_llm_config() is None

    def test_hf_token_selects_huggingface(self, clean_env):
        clean_env.setenv("HF_TOKEN", "hf_deployment_token")
        cfg = hosted_llm_config()
        assert cfg["provider"] == "huggingface"
        assert cfg["api_key"] == "hf_deployment_token"
        assert cfg["base_url"] == PROVIDER_BASE_URLS["huggingface"]
        assert cfg["model"] == HOSTED_DEFAULT_HF_MODEL
        assert cfg["hosted"] is True

    def test_legacy_hub_token_name_also_works(self, clean_env):
        clean_env.setenv("HUGGING_FACE_HUB_TOKEN", "hf_alt")
        assert hosted_llm_config()["api_key"] == "hf_alt"

    def test_model_override(self, clean_env):
        clean_env.setenv("HF_TOKEN", "hf_x")
        clean_env.setenv("AQUASCOPE_LLM_MODEL", "google/gemma-3-12b-it")
        assert hosted_llm_config()["model"] == "google/gemma-3-12b-it"

    def test_generic_key_takes_precedence_over_hf(self, clean_env):
        clean_env.setenv("HF_TOKEN", "hf_x")
        clean_env.setenv("AQUASCOPE_LLM_API_KEY", "sk-generic")
        clean_env.setenv("AQUASCOPE_LLM_BASE_URL", "https://api.groq.com/openai/v1")
        cfg = hosted_llm_config()
        assert cfg["api_key"] == "sk-generic"
        assert cfg["provider"] == "groq"

    def test_blank_token_is_ignored(self, clean_env):
        clean_env.setenv("HF_TOKEN", "   ")
        assert hosted_llm_config() is None

    def test_hosted_default_model_is_actually_served(self):
        # Guards against the class of bug where the shipped default 404s.
        assert HOSTED_DEFAULT_HF_MODEL in PROVIDER_MODELS["huggingface"]

    def test_no_credentials_are_bundled_in_the_package(self, clean_env):
        """The package must never ship a token of its own."""
        import inspect
        import re

        import aquascope.ai_engine.recommender as rec

        source = inspect.getsource(rec)
        token_like = re.findall(r"\b(?:hf_|sk-|gsk_)[A-Za-z0-9]{20,}", source)
        assert token_like == []
        assert hosted_llm_config() is None

    def test_quota_exhaustion_is_reported_clearly(self, clean_env, monkeypatch):
        import aquascope.ai_engine.recommender as rec

        def out_of_credits(*_args, **_kwargs):
            raise RuntimeError("Error code: 402 - You have exceeded your monthly included credits")

        monkeypatch.setattr(rec, "_call_openai_compatible", out_of_credits)
        clean_env.setenv("HF_TOKEN", "hf_x")
        cfg = hosted_llm_config()

        result = recommend_with_llm_detailed(
            PROFILE, top_k=3, model=cfg["model"], api_key=cfg["api_key"], base_url=cfg["base_url"]
        )
        assert result.mode == "rule_based"
        assert result.recommendations           # visitor still gets output
        assert "out of inference credits" in result.error


class TestBackwardCompatibility:
    def test_recommend_with_llm_still_returns_a_plain_list(self, monkeypatch):
        import aquascope.ai_engine.recommender as rec

        monkeypatch.setattr(
            rec, "_call_openai_compatible", lambda *a, **k: "definitely not json"
        )
        recs = recommend_with_llm(PROFILE, top_k=3, api_key="sk-test")
        assert isinstance(recs, list)
        assert recs  # fell back to rule-based rather than raising

    def test_result_is_iterable_and_sized(self):
        result = RecommendationResult(recommendations=[], mode="rule_based")
        assert len(result) == 0
        assert list(result) == []
