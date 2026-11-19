import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    llm_provider: str = os.getenv("LLM_PROVIDER", "gemini").lower()
    fallback_provider: str = os.getenv("FALLBACK_PROVIDER", "").lower()
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_base_url: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-5.6-luna")
    gemini_fallback_models: str = os.getenv("GEMINI_FALLBACK_MODELS", "gemini-3.1-flash-lite,gemini-3-flash-preview")
    llm_timeout_s: float = float(os.getenv("LLM_TIMEOUT_S", "40"))
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    anthropic_model: str = os.getenv("ANTHROPIC_MODEL", "claude-haiku-5-5")
    fetch_timeout_s: float = float(os.getenv("FETCH_TIMEOUT_S", "10"))
    max_html_bytes: int = int(os.getenv("MAX_HTML_BYTES", "2000000"))
    max_content_chars: int = int(os.getenv("MAX_CONTENT_CHARS", "12000"))
    cache_ttl_s: int = int(os.getenv("CACHE_TTL_S", "3600"))


def get_settings() -> Settings:
    return Settings()
