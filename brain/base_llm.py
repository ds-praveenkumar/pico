"""
Base llm class for all llms
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional

from .logging_setup import get_logger

logger = get_logger(__name__)

_ZERO_USAGE: Dict[str, int] = {"prompt": 0, "completion": 0, "total": 0}


def _coerce_usage(usage: Optional[object]) -> Dict[str, int]:
    """Normalize an OpenAI-style usage payload to plain counts (never raises)."""
    if usage is None:
        return dict(_ZERO_USAGE)

    def get(key: str) -> int:
        if isinstance(usage, dict):
            value = usage.get(key, 0)
        elif hasattr(usage, key):
            value = getattr(usage, key)
        else:
            value = 0
        return int(value) if value else 0

    prompt = get("prompt_tokens")
    completion = get("completion_tokens")
    total = get("total_tokens") or (prompt + completion)
    return {"prompt": prompt, "completion": completion, "total": total}


class BaseLLM(ABC):
    """
    Base LLM class
    """
    def __init__(self, provider: str, 
                 model_name: str, 
                 api_key: Optional[str] = None,
                 tools: Optional[List] = None):
        self.provider = provider
        self.model_name = model_name
        self.api_key = api_key
        self.tools = tools
        self._usage: Dict[str, int] = dict(_ZERO_USAGE)
        self._last_generation: Dict[str, int] = dict(_ZERO_USAGE)
        logger.debug(f"BaseLLM initialized for provider={provider} model={model_name}")
        
    @abstractmethod 
    def client_init(self, **kwargs):
        raise NotImplementedError
    
    @abstractmethod
    def generate(self, messages: List[Dict]):
        raise NotImplementedError
    
    def record_usage(self, usage: Optional[object]) -> Dict[str, int]:
        """Accumulate one generation's token usage and remember it as last."""
        counts = _coerce_usage(usage)
        self._last_generation = counts
        for key in counts:
            self._usage[key] += counts[key]
        return counts

    def reset_usage(self) -> None:
        """Zero out the accumulated usage counters."""
        self._usage = dict(_ZERO_USAGE)

    @property
    def usage(self) -> Dict[str, int]:
        """Cumulative tokens across every generation on this client."""
        return dict(self._usage)

    @property
    def last_generation(self) -> Dict[str, int]:
        """Token counts from the most recent :meth:`generate` call."""
        return dict(self._last_generation)

    def __call__(self, *args, **kwargs):
        return self.generate(*args, **kwargs)