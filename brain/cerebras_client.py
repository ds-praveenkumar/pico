"""OpenAI-compatible client for Cerebras inference endpoints."""

from .base_llm import BaseLLM
from .logging_setup import get_logger
from typing import Dict, List, Optional
from openai import OpenAI
logger = get_logger(__name__)


class CerebrasClient(BaseLLM):
    def __init__(self, provider: str, 
                 model_name: str, 
                 api_key: Optional[str] = None,
                 tools: Optional[List] = None,
                 base_url: Optional[str] = None):
        super().__init__(provider, model_name, api_key, tools)
        self.base_url = base_url
        self.client_init()

    def client_init(self, **kwargs):
        if not self.api_key:
            raise ValueError("CEREBRAS_API_KEY is required to initialize CerebrasClient")
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            **kwargs,
        )
        logger.info(f"[bold cyan]Cerebras client initialized[/bold cyan]: provider={self.provider} model={self.model_name} base_url={self.base_url}")
        
    def generate(self, messages: List[Dict], **kwargs):
        params = {"model": self.model_name, "messages": messages}
        if self.tools:
            params["tools"] = self.tools
        logger.debug(f"generate called with messages={messages} kwargs={kwargs}")
        response = self.client.chat.completions.create(**params, **kwargs)
        self.record_usage(getattr(response, "usage", None))
        content = response.choices[0].message
        logger.info(f"[bold green]Generate ok[/bold green]: provider={self.provider} model={self.model_name}")
        return content