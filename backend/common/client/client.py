import httpx
from httpx_sse import connect_sse
from httpx_sse import aconnect_sse
from typing import Any, AsyncIterable
from common.types import (
    AgentCard,
    GetTaskRequest,
    SendTaskRequest,
    SendTaskResponse,
    JSONRPCRequest,
    GetTaskResponse,
    CancelTaskResponse,
    CancelTaskRequest,
    SetTaskPushNotificationRequest,
    SetTaskPushNotificationResponse,
    GetTaskPushNotificationRequest,
    GetTaskPushNotificationResponse,
    A2AClientHTTPError,
    A2AClientJSONError,
    SendTaskStreamingRequest,
    SendTaskStreamingResponse,
)
import os
import json
import logging
# Configure logging
LOGLEVEL = os.environ.get("LOGLEVEL", "INFO").upper()
# logging.basicConfig(level=LOGLEVEL, format="%(asctime)s %(levelname)s: %(message)s")
# Configure logging with a timestamp in the format
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

class A2AClient:
    def __init__(self, agent_card: AgentCard = None, url: str = None):
        if agent_card:
            self.url = agent_card.url
        elif url:
            self.url = url
        else:
            raise ValueError("Must provide either agent_card or url")

    async def send_task(self, payload: dict[str, Any]) -> SendTaskResponse:
        request = SendTaskRequest(params=payload)
        return SendTaskResponse(**await self._send_request(request))

    async def send_task_streaming(
        self, payload: dict[str, Any]
    ) -> AsyncIterable[SendTaskStreamingResponse]:
        request = SendTaskStreamingRequest(params=payload)
        
        # Use httpx.AsyncClient for true asynchronous operations
        async with httpx.AsyncClient(timeout=None) as client: # This is correct
            try:
                # MODIFIED: Use aconnect_sse for asynchronous context management
                async with aconnect_sse(
                    client, 
                    "POST", 
                    self.url, 
                    json=request.model_dump(by_alias=True) # Assuming by_alias is needed
                ) as event_source:
                    # Iterate asynchronously
                    async for sse in event_source.aiter_sse():
                        try:
                            # logger.debug(f"A2AClient SSE Raw Data: {sse.data}") # Optional: for debugging raw SSE
                            yield SendTaskStreamingResponse(**json.loads(sse.data))
                        except json.JSONDecodeError as e_json:
                            logger.error(f"A2AClient: JSONDecodeError for SSE data: {sse.data}, error: {e_json}")
                            # Optionally yield an error object or skip
                            continue 
            except httpx.HTTPStatusError as e_status: # Catch HTTP status errors specifically if httpx_sse raises them this way
                logger.error(f"A2AClient: HTTPStatusError during SSE streaming: {e_status.response.status_code}, response: {e_status.response.text[:200]}")
                raise A2AClientHTTPError(e_status.response.status_code, f"HTTP error during streaming: {e_status.response.status_code}") from e_status
            except httpx.RequestError as e_req: # Catch other httpx request errors (network, timeout, etc.)
                logger.error(f"A2AClient: httpx.RequestError during SSE streaming: {e_req}")
                raise A2AClientHTTPError(500, f"Request error during streaming: {str(e_req)}") from e_req
            except json.JSONDecodeError as e_json_overall: # If connect_sse or initial response is bad JSON
                logger.error(f"A2AClient: JSONDecodeError in SSE stream setup or non-event data: {e_json_overall}")
                raise A2AClientJSONError(str(e_json_overall)) from e_json_overall
            except Exception as e_general: # Catch any other unexpected errors
                logger.error(f"A2AClient: Unexpected error during SSE streaming: {e_general}", exc_info=True)
                # Re-raising with your custom error type if appropriate, or a generic one
                raise A2AClientHTTPError(500, f"Unexpected streaming error: {str(e_general)}") from e_general




    async def _send_request(self, request: JSONRPCRequest) -> dict[str, Any]:
        async with httpx.AsyncClient() as client: # This is already correct
            try:
                response = await client.post(
                    self.url, json=request.model_dump(by_alias=True), timeout=30 # Use by_alias for Pydantic models
                )
                response.raise_for_status()
                # It's good practice to check content-type before .json()
                content_type = response.headers.get("content-type", "")
                if "application/json" in content_type:
                    return response.json()
                else:
                    logger.error(f"A2AClient _send_request: Unexpected content-type: {content_type}. Response text: {response.text[:500]}")
                    raise A2AClientJSONError(f"Unexpected content-type: {content_type}. Expected application/json.")
            except httpx.HTTPStatusError as e:
                logger.error(f"A2AClient _send_request: HTTPStatusError status_code={e.response.status_code}, response_text={e.response.text[:500]}")
                raise A2AClientHTTPError(e.response.status_code, str(e)+ f" Response: {e.response.text[:200]}") from e
            except json.JSONDecodeError as e:
                logger.error(f"A2AClient _send_request: JSONDecodeError for response: {response.text[:500]}")
                raise A2AClientJSONError(str(e)) from e
            except httpx.RequestError as e: # Network errors etc.
                logger.error(f"A2AClient _send_request: httpx.RequestError: {e}")
                raise A2AClientHTTPError(500, f"Network request error: {str(e)}") from e

    async def get_task(self, payload: dict[str, Any]) -> GetTaskResponse:
        request = GetTaskRequest(params=payload)
        return GetTaskResponse(**await self._send_request(request))

    async def cancel_task(self, payload: dict[str, Any]) -> CancelTaskResponse:
        request = CancelTaskRequest(params=payload)
        return CancelTaskResponse(**await self._send_request(request))

    async def set_task_callback(
        self, payload: dict[str, Any]
    ) -> SetTaskPushNotificationResponse:
        request = SetTaskPushNotificationRequest(params=payload)
        return SetTaskPushNotificationResponse(**await self._send_request(request))

    async def get_task_callback(
        self, payload: dict[str, Any]
    ) -> GetTaskPushNotificationResponse:
        request = GetTaskPushNotificationRequest(params=payload)
        return GetTaskPushNotificationResponse(**await self._send_request(request))
