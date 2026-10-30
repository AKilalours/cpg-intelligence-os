"""Model providers behind one interface, with record and replay."""

from cpgos.llm.base import LLMError, LLMRequest, LLMResponse, Recorder, ReplayMiss  # noqa: F401


def provider(name: str, model: str | None = None, mode: str | None = None):
    """A recorded provider by name: 'ollama' or 'anthropic'."""
    if name == "ollama":
        from cpgos.llm.ollama import Ollama
        return Recorder(Ollama(model), mode)
    if name == "anthropic":
        from cpgos.llm.anthropic_provider import Anthropic
        return Recorder(Anthropic(model), mode)
    raise ValueError(f"unknown provider {name!r}")
