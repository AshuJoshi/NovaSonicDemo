# backend/lib/image_analyzer/image_analyzer_tool.py
import json
import logging
import asyncio
import uuid

# Import the LLM client from its sibling file
from .image_analyzer_llm_client import ImageAnalyzerLLMClient

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Module-level variable to hold the initialized LLM client for image analysis
_shared_llm_client_instance = None
_llm_client_init_lock = asyncio.Lock()

async def get_llm_client(region="us-east-1") -> ImageAnalyzerLLMClient:
    global _shared_llm_client_instance
    if _shared_llm_client_instance is None:
        async with _llm_client_init_lock:
            if _shared_llm_client_instance is None:
                logger.info("[ImageAnalyzerTool] Initializing ImageAnalyzerLLMClient...")
                _shared_llm_client_instance = ImageAnalyzerLLMClient(region=region)
    return _shared_llm_client_instance

async def _execute_image_analysis_remotely(manager_instance, image_analysis_id: str, query_context: str) -> dict:
    """
    Orchestrates screenshot request from frontend, sends to LLM, and returns description.
    This is the coroutine run by launch_background_tool_task.
    """
    logger.info(f"[ImageAnalyzerTool] (ID: {image_analysis_id}) Background analysis started. Context: '{query_context}'")

    # Default result structure
    result_for_cache = {
        "error": "Image analysis failed to complete.",
        "originalContext": query_context,
        "analysisId": image_analysis_id,
        "description": None
    }

    # 1. Prepare to wait for image data from frontend
    image_event = asyncio.Event()
    manager_instance.pending_screenshot_events[image_analysis_id] = image_event
    logger.info(f"[ImageAnalyzerTool] (ID: {image_analysis_id}) Registered event wait for screenshot.")

    # 2. Request screenshot from frontend via output_queue
    request_payload_to_frontend = {
        "customEvent": "requestScreenshotForAnalysis",
        "payload": {"imageAnalysisId": image_analysis_id}
    }

    image_data_url = None # Initialize to ensure it has a value
    received_data_dict = None # Initialize
    try:
        await manager_instance.output_queue.put(request_payload_to_frontend)
        logger.info(f"[ImageAnalyzerTool] (ID: {image_analysis_id}) Queued 'requestScreenshotForAnalysis' to frontend.")
    except Exception as e:
        logger.error(f"[ImageAnalyzerTool] (ID: {image_analysis_id}) Failed to queue screenshot request: {e}")
        results_for_cache["error"] = "System error: Failed to request screenshot."
        if image_analysis_id in manager_instance.pending_screenshot_events: # Cleanup
            del manager_instance.pending_screenshot_events[image_analysis_id]
        return results_for_cache

    # 3. Wait for frontend to send screenshot data (with timeout)
    try:
        logger.info(f"[ImageAnalyzerTool] (ID: {image_analysis_id}) Waiting for screenshot data from frontend...")
        await asyncio.wait_for(image_event.wait(), timeout=30.0) # Wait up to 30s for screenshot
        # image_data_url = manager_instance.received_screenshot_data.pop(image_analysis_id, None)

        # Data is now a dict: {"imageDataUrl": "..."} or {"error": "..."} or None
        received_data_dict = manager_instance.received_screenshot_data.pop(image_analysis_id, None)
        
        if received_data_dict and received_data_dict.get("imageDataUrl"):
            image_data_url = received_data_dict["imageDataUrl"]
            logger.info(f"[ImageAnalyzerTool] (ID: {image_analysis_id}) Screenshot event received, data URL acquired.")
        elif received_data_dict and received_data_dict.get("error"):
            logger.error(f"[ImageAnalyzerTool] (ID: {image_analysis_id}) Frontend reported error during screenshot: {received_data_dict['error']}")
            results_for_cache["error"] = f"Frontend error during screenshot: {received_data_dict['error']}"
            image_data_url = None # Ensure it's None
        else:
            logger.error(f"[ImageAnalyzerTool] (ID: {image_analysis_id}) Screenshot data not found or malformed after event signal.")
            results_for_cache["error"] = "Screenshot data structure error from frontend."
            image_data_url = None # Ensure it's None

    except asyncio.TimeoutError:
        logger.error(f"[ImageAnalyzerTool] (ID: {image_analysis_id}) Timeout waiting for screenshot from frontend.")
        results_for_cache["error"] = "Timeout: Screenshot not received from the extension."
    finally: # Ensure cleanup in all cases after wait_for
        if image_analysis_id in manager_instance.pending_screenshot_events:
            del manager_instance.pending_screenshot_events[image_analysis_id]
        if image_analysis_id in manager_instance.received_screenshot_data and image_data_url is None: # If event set but data not popped
            manager_instance.received_screenshot_data.pop(image_analysis_id, None)


    if not image_data_url:
        if "error" not in results_for_cache or results_for_cache["error"] == "Image analysis failed to complete.": # Avoid overwriting specific timeout error
             results_for_cache["error"] = "Screenshot data was not available or not received."
        return results_for_cache

    # 4. Process image: extract base64 and get description from LLM
    try:
        if not isinstance(image_data_url, str) or not image_data_url.startswith("data:image"):
            raise ValueError("Received data is not a valid image data URL.")

        # Extract base64 part: e.g., "data:image/jpeg;base64,LzlqLzRBQ..." -> "LzlqLzRBQ..."
        base64_image_data = image_data_url.split(',', 1)[1]

        llm_client = await get_llm_client(region=manager_instance.region) # Pass region if needed

        # Construct a more specific prompt for the LLM if context is provided
        llm_prompt = f"Describe this image. Focus on: {query_context}" if query_context else "Describe what you see in this image in one or two sentences, phrased as 'This image shows...'."

        description = await llm_client.describe_image_with_llm(base64_image_data, llm_prompt)

        results_for_cache = {
            "description": description,
            "originalContext": query_context,
            "analysisId": image_analysis_id,
            "status": "success" # Indicate LLM call success
        }
    except ValueError as ve: # For base64 extraction error
        logger.error(f"[ImageAnalyzerTool] (ID: {image_analysis_id}) Invalid image data URL format: {ve}")
        results_for_cache["error"] = f"Invalid image data format from extension: {str(ve)}"
    except Exception as e: # For LLM call or other errors
        logger.error(f"[ImageAnalyzerTool] (ID: {image_analysis_id}) Error getting description from LLM: {e}", exc_info=True)
        results_for_cache["error"] = f"Error during image analysis: {str(e)}"

    return results_for_cache


async def handle_imageanalyzer(manager_instance, tool_use_content: dict) -> dict:
    raw_tool_name_from_event = tool_use_content.get("toolName", "imageAnalyzer") # Match spec
    cache_key_to_check = raw_tool_name_from_event.lower()
    tool_use_id = tool_use_content.get("toolUseId") # Nova Sonic's ID for this specific toolUse block

    if not tool_use_id:
        logger.error(f"[Tool:{raw_tool_name_from_event}] Critical: toolUseId missing.")
        return {"result": "Error: System error (missing toolUseId).", "status": "error"}

    try:
        tool_input_str = tool_use_content.get("content", "{}")
        parsed_tool_input = json.loads(tool_input_str)
        query_context = parsed_tool_input.get("context", "the current page content") 

        logger.info(f"[Tool:{raw_tool_name_from_event}] (ID: {tool_use_id}) Invoked. Context: '{query_context}'.")
        logger.info(f"[Tool:{raw_tool_name_from_event}] Attempting cache lookup with key: '{cache_key_to_check}'.")
        logger.info(f"[Tool:{raw_tool_name_from_event}] Available cache keys: {list(manager_instance.completed_async_tool_results.keys())}")

        if cache_key_to_check in manager_instance.completed_async_tool_results:
            logger.info(f"[Tool:{raw_tool_name_from_event}] (ID: {tool_use_id}) Cache HIT. Popping and returning.")
            cached_result_data = manager_instance.completed_async_tool_results.pop(cache_key_to_check)
            # Ensure the result being sent back is a string
            return {"result": json.dumps(cached_result_data) if isinstance(cached_result_data, dict) else str(cached_result_data), "status": "success"}
        else:
            logger.info(f"[Tool:{raw_tool_name_from_event}] (ID: {tool_use_id}) Cache MISS.")

        if tool_use_id in manager_instance.active_background_tasks:
            logger.info(f"[Tool:{raw_tool_name_from_event}] (ID: {tool_use_id}) Background task for this Nova Sonic toolUseId is already active. Returning placeholder.")
            return {"result": f"I am still processing a previous request to analyze an image. I will notify you when it's complete.", "status": "success"}

        logger.info(f"[Tool:{raw_tool_name_from_event}] (ID: {tool_use_id}) Initiating new background image analysis.")

        # Unique ID for this specific image analysis operation (frontend <-> backend coordination)
        # This ID will be used by the background task to request and receive the screenshot.
        image_analysis_operation_id = str(uuid.uuid4())

        actual_op_coro_factory = lambda: _execute_image_analysis_remotely(manager_instance, image_analysis_operation_id, query_context)

        await manager_instance.launch_background_tool_task(
            tool_use_id, # Nova Sonic's ID for this tool use block, used to track the overall task
            raw_tool_name_from_event, 
            actual_op_coro_factory
        )

        placeholder_message = f"Okay, I'll capture and analyze the image of your current page regarding '{query_context}'. I'll notify you when the description is ready."
        logger.info(f"[Tool:{raw_tool_name_from_event}] (ID: {tool_use_id}) Returning placeholder to Nova Sonic: '{placeholder_message}'")
        return {"result": placeholder_message, "status": "success"}

    except json.JSONDecodeError:
        logger.error(f"[Tool:{raw_tool_name_from_event}] (ID: {tool_use_id}) Invalid JSON in input: {tool_use_content.get('content')}")
        return {"result": f"Error: Invalid input format for {raw_tool_name_from_event} tool.", "status": "error"}
    except Exception as e:
        logger.error(f"[Tool:{raw_tool_name_from_event}] (ID: {tool_use_id}) Error in handler: {e}", exc_info=True)
        return {"result": f"An unexpected error occurred while initiating {raw_tool_name_from_event}.", "status": "error"}

def get_imageanalyzer_tool_spec() -> dict:
    """Returns the tool specification for the imageAnalyzer tool."""
    return {
        "toolSpec": {
            "name": "imageAnalyzer", # This must match the key in tool_handlers
            "description": "Captures an image of the current web page and provides an AI-generated description. The user is notified when the analysis is complete.",
            "inputSchema": {
                "json": json.dumps({
                    "type": "object",
                    "properties": {
                        "context": {
                            "type": "string",
                            "description": "Optional: Provide context or a specific question about the image to guide the analysis (e.g., 'focus on the colors' or 'what is the main subject?')."
                        }
                    },
                    "required": [] # Context is optional
                })
            }
        }
    }