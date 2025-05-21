# backend/tools/weather_tool.py
import re
import json
import logging
from strands import Agent
from strands_tools import http_request
from strands.models import BedrockModel
from strands.handlers.callback_handler import null_callback_handler 

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Define a weather-focused system prompt
WEATHER_SYSTEM_PROMPT = """You are a weather assistant with HTTP capabilities. You can:

1. Make HTTP requests to the National Weather Service API
2. Get forecast for today and tomorrow, and remove rest of the days from the forecast.
2. Process and display weather forecast data
3. Provide weather information for locations in the United States

When retrieving weather information:
1. First get the coordinates or grid information using https://api.weather.gov/points/{latitude},{longitude} or https://api.weather.gov/points/{zipcode}
2. Then use the returned forecast URL to get the weather
3. THEN ONLY KEEP the short forecast for today and tomorrow - for the next 48 hours, remove the forecast for all other days.

When displaying responses:
- Format weather data without any markdown or non-alphanumeric characters. Do not use characters that CANNOT be JSON serialized.
- Highlight important information like temperature, precipitation, and alerts
- Handle errors appropriately
- Convert technical terms to user-friendly language

Always explain the weather conditions clearly and provide context for the forecast.
"""

# Create a Bedrock model instance
bedrock_model = BedrockModel(
    # model_id="us.amazon.nova-lite-v1:0",
    model_id="us.amazon.nova-micro-v1:0",
    temperature=0.2,
    top_p=0.9,
)

# Create an agent with HTTP capabilities
weather_agent = Agent(
    model=bedrock_model,
    system_prompt=WEATHER_SYSTEM_PROMPT,
    tools=[http_request],  # Explicitly enable http_request tool
    callback_handler=null_callback_handler
)

async def handle_get_weather(tool_use_content: dict) -> dict:
    """
    Handles the 'getWeather' tool request.
    The input tool_use_content is the full 'toolUse' event content from Nova Sonic.
    For now, returns a static weather report.
    """
    try:
        # The 'content' field within tool_use_content is a JSON string, parse it
        tool_input_str = tool_use_content.get("content", "{}")
        parsed_tool_input = json.loads(tool_input_str)
        location = parsed_tool_input.get("location", "an unknown place")
        logger.info(f"[Tool:getWeather] Called for location: {location}")
        response = weather_agent(f"Get on the current weather for {location}")

        logger.info(response.message)
        logger.info(response.metrics)        
        result = response.message['content'][0]
        
        output_text = re.sub(r"<thinking>.*?</thinking>", "", result['text'])
        return {
            # "result": result['text'],
            "result": output_text,
            "status": "success" # It's good practice to include a status
        }
    except json.JSONDecodeError:
        logger.error("[Tool:getWeather] Invalid JSON in tool_use_content.content: %s", tool_input_str)
        return {
            "result": "Error: Invalid input format for getWeather tool.",
            "status": "error"
        }
    except Exception as e:
        logger.error(f"[Tool:getWeather] Error processing tool_use_content {tool_use_content}: {e}", exc_info=True)
        # Try to extract location even in case of other errors for a more informative message
        location_for_error = "the specified location"
        try:
            tool_input_str_err = tool_use_content.get("content", "{}")
            parsed_tool_input_err = json.loads(tool_input_str_err)
            location_for_error = parsed_tool_input_err.get("location", "the specified location")
        except:
            pass # Ignore if parsing fails again

        return {
            "result": f"Error getting weather for {location_for_error}.",
            "status": "error"
        }

def get_weather_tool_spec() -> dict:
    """Returns the tool specification for the getWeather tool for Nova Sonic."""
    return {
        # This is the outer structure Nova Sonic expects for each tool in the toolConfiguration.tools array
        "toolSpec": {
            "name": "getWeather",
            "description": "Get current weather for a given location",
            "inputSchema": {
                # The inputSchema.json value must be a JSON *string*
                "json": json.dumps({
                    "type": "object",
                    "properties": {
                        "location": {
                            "type": "string",
                            "description": "Name of the city (e.g. Seattle, WA)"
                        }
                    },
                    "required": ["location"]
                })
            }
        }
    }