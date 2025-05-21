# backend/lib/agent_search/agent_search_tool.py
import json
import logging
import asyncio # For the lock

# Import AgentClient from its sibling file agentclient.py
from .agentclient import AgentClient 
# Configure logging with a timestamp in the format
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Module-level (or class-level) variable to hold the initialized A2A client instance
# This ensures AgentClient is initialized and discover() is called only once per server process.
_shared_a2a_client_instance: AgentClient | None | str = None # Can be Client, None, or "ERROR_DURING_INIT"
_a2a_client_init_lock = asyncio.Lock() # Lock to prevent multiple initializations/discoveries concurrently

async def get_initialized_a2a_client() -> AgentClient:
    """
    Helper function to get a shared, initialized (discovered) AgentClient instance.
    Handles one-time initialization and discovery.
    """
    global _shared_a2a_client_instance
    if _shared_a2a_client_instance is None or _shared_a2a_client_instance == "ERROR_DURING_INIT":
        async with _a2a_client_init_lock:
            # Double check after acquiring lock
            if _shared_a2a_client_instance is None or _shared_a2a_client_instance == "ERROR_DURING_INIT":
                logger.info("[AgentSearchTool] Initializing AgentClient for the first time and discovering agent...")
                try:
                    # Configuration for agent_url could come from environment variables or a config file
                    client = AgentClient(agent_url="http://localhost:10000") # Or your configurable URL
                    await client.discover() # Perform the async discovery
                    
                    if not client.client: # Check if A2AClient's internal client got set by discover()
                        logger.error("[AgentSearchTool] AgentClient internal client (A2AClient) was not initialized after discover call.")
                        _shared_a2a_client_instance = "ERROR_DURING_INIT"
                    else:
                        _shared_a2a_client_instance = client
                        logger.info("[AgentSearchTool] AgentClient initialized and agent discovered successfully.")
                except Exception as e:
                    logger.error(f"[AgentSearchTool] CRITICAL: Failed to initialize or discover AgentClient: {e}", exc_info=True)
                    _shared_a2a_client_instance = "ERROR_DURING_INIT"
    
    if isinstance(_shared_a2a_client_instance, str) and _shared_a2a_client_instance == "ERROR_DURING_INIT":
        raise ConnectionError("AgentClient could not be initialized/discovered. Check A2A server and configurations.")
    if not isinstance(_shared_a2a_client_instance, AgentClient): # Should not happen if lock works
        raise ConnectionError("AgentClient not available or initialization failed unexpectedly.")
        
    return _shared_a2a_client_instance

async def _execute_agent_search_remotely(query: str, tool_use_id: str) -> dict:
    """
    The actual long-running search operation using the shared A2A client.
    This function will be the target for the background task.
    """
    logger.info(f"[AgentSearchTool] (ID: {tool_use_id}) Preparing to send query '{query}' using A2A client.")
    
    # Default error structure in case of failure
    results_for_cache = {
        "error": "A2A client operation failed.",
        "originalQuery": query,
        "searchId": tool_use_id,
        "details": None # Or a more specific error message
    }

    try:
        a2a_client = await get_initialized_a2a_client() # Get the shared, discovered client instance

        logger.info(f"[AgentSearchTool] (ID: {tool_use_id}) Sending task to A2A server with query: '{query}'")
        final_text_from_a2a = await a2a_client.send_task(input_text=query, stream=True) # Assuming stream=True is desired

        # Debugging Async
        # logger.info("[AgentSearchTool] Before mock A2A delay inside task_wrapper...")
        # await asyncio.sleep(10) # Simulate the 10-15s duration of the A2A call
        # logger.info("[AgentSearchTool] After mock A2A delay inside task_wrapper.")
        # final_text_from_a2a = "Mock result after asyncio.sleep"


        logger.info(f"[AgentSearchTool] (ID: {tool_use_id}) Successfully received response from A2A server for query: '{query}'.")
        # logger.debug(f"[AgentSearchTool] (ID: {tool_use_id}) Full A2A response text: {final_text_from_a2a}")

        # Format results for caching
        # This structure will be json.dumps'd when retrieved from cache for Nova Sonic
        results_for_cache = {
            "summary": f"Agent search completed for: '{query}'.", # You might generate a better summary
            "details": final_text_from_a2a, # This is the text response from your agent
            "originalQuery": query,
            "searchId": tool_use_id # Good for reference
        }
        
    except ConnectionError as ce: # Handles failure from get_initialized_a2a_client()
        logger.error(f"[AgentSearchTool] (ID: {tool_use_id}) ConnectionError for query '{query}': {ce}", exc_info=False) # No need for full trace for this one
        results_for_cache["error"] = f"Could not connect to or initialize the search agent: {str(ce)}"
    except Exception as e:
        logger.error(f"[AgentSearchTool] (ID: {tool_use_id}) Error during A2A client task for query '{query}': {e}", exc_info=True)
        results_for_cache["error"] = f"An error occurred during the agent search: {str(e)}"
    
    return results_for_cache


async def handle_agent_search(manager_instance, tool_use_content: dict) -> dict:
    """
    Handles the 'agentSearch' tool request asynchronously.
    manager_instance is used for launching background tasks and accessing the shared cache.
    """
    raw_tool_name_from_event = tool_use_content.get("toolName", "agentSearch_fallback") 
    cache_key_to_check = raw_tool_name_from_event.lower() 
    tool_use_id = tool_use_content.get("toolUseId")

    if not tool_use_id:
        logger.error(f"[Tool:{raw_tool_name_from_event}] Critical: toolUseId missing.")
        return {"result": f"Error: Missing toolUseId for {raw_tool_name_from_event}.", "status": "error"}

    try:
        tool_input_str = tool_use_content.get("content", "{}")
        parsed_tool_input = json.loads(tool_input_str)
        query = parsed_tool_input.get("query", "")

        if not query:
            logger.warning(f"[Tool:{raw_tool_name_from_event}] (ID: {tool_use_id}) Empty query.")
            return {"result": f"Please provide a query for {raw_tool_name_from_event}.", "status": "error"}

        logger.info(f"[Tool:{raw_tool_name_from_event}] (ID: {tool_use_id}) Invoked for query: '{query}'.")
        logger.info(f"[Tool:{raw_tool_name_from_event}] Attempting cache lookup with key: '{cache_key_to_check}'.")
        logger.info(f"[Tool:{raw_tool_name_from_event}] Available cache keys in 'completed_async_tool_results': {list(manager_instance.completed_async_tool_results.keys())}")

        if cache_key_to_check in manager_instance.completed_async_tool_results:
            logger.info(f"[Tool:{raw_tool_name_from_event}] (ID: {tool_use_id}) Cache HIT for key '{cache_key_to_check}'. Popping.")
            cached_result_data = manager_instance.completed_async_tool_results.pop(cache_key_to_check)
            return {"result": json.dumps(cached_result_data), "status": "success"}
        else:
            logger.info(f"[Tool:{raw_tool_name_from_event}] (ID: {tool_use_id}) Cache MISS for key '{cache_key_to_check}'.")

        if tool_use_id in manager_instance.active_background_tasks:
            logger.info(f"[Tool:{raw_tool_name_from_event}] (ID: {tool_use_id}) Background task for this toolUseId already active. Returning placeholder.")
            return {"result": f"I am still working on the {raw_tool_name_from_event} for '{query}'. I will notify you.", "status": "success"}

        logger.info(f"[Tool:{raw_tool_name_from_event}] (ID: {tool_use_id}) Initiating new background search.")
        
        # The coroutine factory now points to the local _execute_agent_search_remotely
        actual_search_coro_factory = lambda: _execute_agent_search_remotely(query, tool_use_id)

        logger.info(f"Launching Background Tool Task")            
        await manager_instance.launch_background_tool_task(
            tool_use_id,
            raw_tool_name_from_event, 
            actual_search_coro_factory
        )
        logger.info(f"Returned from launching Background Tool Task")            
        
        placeholder_message = f"Okay, I'm starting the {raw_tool_name_from_event} for '{query}'. This may take a moment. I'll notify you in the chat when it's complete."
        logger.info(f"[Tool:{raw_tool_name_from_event}] (ID: {tool_use_id}) Returning placeholder to Nova Sonic: '{placeholder_message}'")
        return {"result": placeholder_message, "status": "success"}

    except json.JSONDecodeError:
        logger.error(f"[Tool:{raw_tool_name_from_event}] (ID: {tool_use_id}) Invalid JSON in input: {tool_use_content.get('content')}")
        return {"result": f"Error: Invalid input format for {raw_tool_name_from_event} tool.", "status": "error"}
    except ConnectionError as ce: # Catch ConnectionError from get_initialized_a2a_client if it propagates
        logger.error(f"[Tool:{raw_tool_name_from_event}] (ID: {tool_use_id}) Could not initialize connection for search: {ce}")
        return {"result": f"The search agent is currently unavailable: {ce}", "status": "error"}
    except Exception as e:
        logger.error(f"[Tool:{raw_tool_name_from_event}] (ID: {tool_use_id}) Error in handler: {e}", exc_info=True)
        return {"result": f"An unexpected error occurred while initiating {raw_tool_name_from_event}.", "status": "error"}

# get_agent_search_tool_spec() function remains the same as before.
# Ensure it's correctly defined in this file.
def get_agent_search_tool_spec() -> dict:
    """Returns the tool specification for the agentSearch tool."""
    return {
        "toolSpec": {
            "name": "agentSearch",
            "description": "Performs a search using an intelligent agent for a given query. This process typically takes time. Tool will start the search, and return with wait for result message. User will ask to check on the results of the agent search after they have been informed by an out of band notifcation.",
            "inputSchema": {
                "json": json.dumps({
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The search query, topic, or question for the agent."
                        }
                    },
                    "required": ["query"]
                })
            }
        }
    }