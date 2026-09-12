"""
Base llm class for all llms
"""

from abc import ABC, abstractmethod
from typing import List, Optional, Dict

from .logging_setup import get_logger

logger = get_logger(__name__)

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
        logger.debug(f"BaseLLM initialized for provider={provider} model={model_name}")
        
    @abstractmethod 
    def client_init(self, **kwargs):
        raise NotImplementedError
    
    @abstractmethod
    def generate(self, messages: List[Dict]):
        raise NotImplementedError
    
    def __call__(self, *args, **kwargs):
        return self.generate(*args, **kwargs)