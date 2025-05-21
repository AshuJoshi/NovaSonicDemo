# backend/tools/number_race_tool.py
import json
import logging
import time

logger = logging.getLogger(__name__)

def is_integer(value):
    try:
        int(value)
        return True
    except (ValueError, TypeError):
        return False

async def handle_number_race(tool_use_content: dict) -> dict:
    """Handles the 'numberRace' tool request."""
    try:
        tool_input_str = tool_use_content.get("content", "{}")
        parsed_tool_input = json.loads(tool_input_str)
        number = parsed_tool_input.get("number") # Keep as is, might be string or int from JSON

        logger.info(f"[Tool:numberRace] Called with input: {number}")

        if number is None: # Check if 'number' key was missing
             logger.warning("[Tool:numberRace] 'number' not provided in input.")
             return {"result": "No number was provided for the race.", "status": "error"}

        if is_integer(str(number)): # Convert to string for is_integer robustness
            num_val = int(number)
            logger.info(f"[Tool:numberRace] Starting sleep for {num_val} seconds.")
            # Note: time.sleep() is blocking. For a truly async app,
            # you'd use asyncio.sleep() if this were a real I/O bound wait.
            # Since this is just a demo tool, time.sleep() is fine to simulate work.
            time.sleep(num_val)
            logger.info(f"[Tool:numberRace] Finished waiting for {num_val} seconds.")
            return {
                "result": f"I am done waiting for {num_val} seconds.",
                "status": "success"
            }
        else:
            logger.warning(f"[Tool:numberRace] Invalid number provided: {number}")
            return {
                "result": f"The input '{number}' is not a valid integer for the number race.",
                "status": "error"
            }
    except json.JSONDecodeError:
        logger.error("[Tool:numberRace] Invalid JSON in tool_use_content.content: %s", tool_use_content.get("content"))
        return {"result": "Error: Invalid input format for numberRace tool.", "status": "error"}
    except Exception as e:
        logger.error(f"[Tool:numberRace] Error processing: {e}", exc_info=True)
        return {"result": "An unexpected error occurred in the numberRace tool.", "status": "error"}

def get_number_race_tool_spec() -> dict:
    """Returns the tool specification for the numberRace tool."""
    return {
        "toolSpec": {
            "name": "numberRace",
            "description": "A number, an integer to start a number race! I will wait for that many seconds.",
            "inputSchema": {
                "json": json.dumps({
                    "type": "object",
                    "properties": {
                        "number": {
                            # Nova Sonic might send it as a string if user says "five",
                            # but schema can still guide it towards expecting a number-like value.
                            # The handler `is_integer` will validate.
                            "type": "integer", # Or "number" if decimals were allowed by the tool
                            "description": "The integer number of seconds to wait."
                        }
                    },
                    "required": ["number"]
                })
            }
        }
    }