from typing import Any, Dict, AsyncIterable
from langchain_community.tools.tavily_search import TavilySearchResults
from langchain_community.tools import WikipediaQueryRun
from langchain_community.utilities import WikipediaAPIWrapper
from utility import (
    create_parameters,
    invoke_agent_with_roc,
)

class BedrockInlineAgent:
    def __init__(self):
        self.tavily_search = TavilySearchResults()
        self.wikipedia_query_runner = WikipediaQueryRun(
            api_wrapper=WikipediaAPIWrapper(top_k_results=1, doc_content_chars_max=100)
        )
        self.tool_list = {
            self.tavily_search.get_name(): self.tavily_search,
            self.wikipedia_query_runner.get_name(): self.wikipedia_query_runner,
        }
        self.actionGroups = [
            {
                "actionGroupExecutor": {
                    "customControl": "RETURN_CONTROL",  # configure roc
                },
                "actionGroupName": "WebSearchActionGroup",
                "functionSchema": {
                    "functions": [
                        {
                            "description": self.tavily_search.description,
                            "name": self.tavily_search.get_name(),
                            "parameters": create_parameters(self.tavily_search),
                            "requireConfirmation": "DISABLED",
                        },
                        {
                            "description": self.wikipedia_query_runner.description,
                            "name": self.wikipedia_query_runner.get_name(),
                            "parameters": create_parameters(self.wikipedia_query_runner),
                            "requireConfirmation": "DISABLED",
                        },
                    ]
                },
            }
        ]
        self.model_id = "us.anthropic.claude-3-5-haiku-20241022-v1:0"
        self.agent_instruction = """You are a helpful AI assistant that provides users with latest updates in Generative Ai."""


    def invoke(self, query, sessionId) -> str:
        try:
            agent_reply = invoke_agent_with_roc(
                self.actionGroups,
                self.agent_instruction,
                self.model_id,
                query,
                self.tool_list,
            )
            return {
                "is_task_complete": True,
                "require_user_input": False,
                "content": agent_reply
            }
        except Exception as e:
            agent_reply = f"Bedrock - Error invoking agent: {e}"
            return {
                "is_task_complete": True,
                "require_user_input": False,
                "content": agent_reply
            }

    async def stream(self, query, sessionId) -> AsyncIterable[Dict[str, Any]]:
        yield self.invoke(query, sessionId)

    SUPPORTED_CONTENT_TYPES = ["text", "text/plain"]
