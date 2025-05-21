
## Bedrock

This sample uses [Bedrock Inline Agents](https://langchain-ai.github.io/langgraph/) to build a simple web and wikipedia agent and host it as an A2A server.

## Prerequisites

- Python 3.12 or higher
- UV
- Access to a Bedrock LLM and Tavily API Key

## Running the Sample

1. Navigate to the samples directory:
    ```bash
    cd samples/python/agents/bedrock
    ```
2. Set your AWS CLI profile
3. Set `TAVILY_API_KEY` in your environment
4. Run an agent:
    ```bash
    uv run .
    ```
5. Invoke with a `POST` or using the CLI client.

### POST

Send a `POST` request to `http://localhost:10000` with this body:

```
{
  "jsonrpc": "2.0",
  "id": 11,
  "method": "tasks/send",
  "params": {
    "id": "129",
    "sessionId": "8f01f3d172cd4396a0e535ae8aec6687",
    "acceptedOutputModes": [
      "text"
    ],
    "message": {
      "role": "user",
      "parts": [
        {
          "type": "text",
          "text": "Tell me about the Cinco de Mayo holiday"
        }
      ]
    }
  }
}
```

### CLI client

In another terminal, go to `samples/python` and run `uv run hosts/cli`. Then you can type in a query.