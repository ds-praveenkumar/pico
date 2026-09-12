"""
Add open ai client along and any other clients supporting openai compatibility
"""

from .base_llm import BaseLLM
from .logging_setup import get_logger
from typing import Dict, List, Optional

from openai import OpenAI

logger = get_logger(__name__)

class OpenAIClient(BaseLLM):
    """
    call llms with openai compatible apis 
    """
    def __init__(self, provider: str, 
                 model_name: str, 
                 api_key: Optional[str] = None,
                 tools: Optional[List] = None,
                 base_url: Optional[str] = None):
        super().__init__(provider, model_name, api_key, tools)
        self.base_url = base_url
        self.client_init()

    def client_init(self, **kwargs):
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            **kwargs,
        )
        logger.info(f"[bold cyan]OpenAI client initialized[/bold cyan]: provider={self.provider} model={self.model_name} base_url={self.base_url}")

    def generate(self, messages: List[Dict], **kwargs):
        params = {"model": self.model_name, "messages": messages}
        if self.tools:
            params["tools"] = self.tools
        logger.debug(f"generate called with messages={messages} kwargs={kwargs}")
        response = self.client.chat.completions.create(**params, **kwargs)
        content = response.choices[0].message
        logger.info(f"[bold green]Generate ok[/bold green]: provider={self.provider} model={self.model_name}")
        return content   