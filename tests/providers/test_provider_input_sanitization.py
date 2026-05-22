"""Tests for sanitizing pasted provider credentials and endpoints."""

from nanobot.providers.base import normalize_provider_input
from nanobot.providers.openai_compat_provider import OpenAICompatProvider
from nanobot.providers.registry import find_by_name


def test_normalize_provider_input_strips_wrapping_quotes_and_whitespace():
    value = "  ’sk-test-key’  "
    assert normalize_provider_input(value, field_name="api_key") == "sk-test-key"


def test_openai_compat_provider_sanitizes_api_key_and_base():
    provider = OpenAICompatProvider(
        api_key=" ’sk-test-key’ ",
        api_base=" “https://api.deepseek.com” ",
        default_model="deepseek-v4-pro",
        spec=find_by_name("deepseek"),
    )

    assert provider.api_key == "sk-test-key"
    assert provider._api_key_for_client == "sk-test-key"
    assert provider.api_base == "https://api.deepseek.com"
    assert provider._effective_base == "https://api.deepseek.com"
