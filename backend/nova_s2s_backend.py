import json
import logging
import os
import uuid
import warnings
import asyncio
import websockets
import base64
import time
import numpy as np

from lib.weather_tool import handle_get_weather, get_weather_tool_spec
from lib.number_race_tool import handle_number_race, get_number_race_tool_spec 
from lib.agent_search.agent_search_tool import handle_agent_search, get_agent_search_tool_spec 
from lib.image_analyzer.image_analyzer_tool import handle_imageanalyzer, get_imageanalyzer_tool_spec


# Configure logging
LOGLEVEL = os.environ.get("LOGLEVEL", "INFO").upper()
# logging.basicConfig(level=LOGLEVEL, format="%(asctime)s %(levelname)s: %(message)s")
# Configure logging with a timestamp in the format
logging.basicConfig(
    # filename='./novas2s.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    force=True,
    handlers=[
        logging.FileHandler("debug.log"),
        logging.StreamHandler()
    ]
    # filemode='w'  # 'w' to overwrite the file on each run, 'a' to append
)
logger = logging.getLogger(__name__)

# Suppress warnings
warnings.filterwarnings("ignore")
# Suppress websockets server non-critical logs that are triggered by NLB health checks (empty TCP packets)
logging.getLogger("websockets.server").setLevel(logging.CRITICAL)

from aws_sdk_bedrock_runtime.client import (
    BedrockRuntimeClient,
    InvokeModelWithBidirectionalStreamOperationInput,
)
from aws_sdk_bedrock_runtime.models import (
    InvokeModelWithBidirectionalStreamInputChunk,
    BidirectionalInputPayloadPart,
)
from aws_sdk_bedrock_runtime.config import (
    Config,
    HTTPAuthSchemeResolver,
    SigV4AuthScheme,
)
from smithy_aws_core.credentials_resolvers.environment import (
    EnvironmentCredentialsResolver,
)

class BedrockStreamManager:
    """Manages bidirectional streaming with AWS Bedrock using asyncio"""

    def __init__(self, model_id="amazon.nova-sonic-v1:0", region="us-east-1"):
        """Initialize the stream manager."""
        self.model_id = model_id
        self.region = region
        self.last_credential_refresh = 0

        # Audio and output queues
        self.audio_input_queue = asyncio.Queue()
        self.output_queue = asyncio.Queue() # For messages to frontend

        self.response_task = None
        self.stream_response = None
        self.is_active = False
        self.bedrock_client = None

        # Session information
        self.prompt_name = None  # Will be set from frontend
        self.content_name = None  # Will be set from frontend
        self.audio_content_name = None  # Will be set from frontend
        self.toolUseContent = ""
        self.toolUseId = ""
        self.toolName = ""
      
        # Speech detection
        self.speech_detected = False # Not currently used to gate sending

        self.tool_handlers = {
            "getweather": handle_get_weather,
            "numberrace": handle_number_race,
            "agentsearch": handle_agent_search,
            "imageanalyzer": handle_imageanalyzer,
            # Register other tool handlers here as they are created
            # e.g., "getbookofofferstool": handle_get_book_of_offers,
        }

        # NEW: Tool Specifications (primarily for reference or potential future use by backend)
        # The frontend will be responsible for sending the toolConfiguration to Nova Sonic.
        # However, having them here can be useful for the backend to know what it supports.
        self.tool_specs_definitions = {
            "getWeather": get_weather_tool_spec(),
            "numberRace": get_number_race_tool_spec(),
            "agentSearch": get_agent_search_tool_spec(),
            "imageAnalyzer": get_imageanalyzer_tool_spec(),
            # Add other tool spec functions here
        }
        
        # For async tool results and task management
        self.pending_tool_results = {}  # Key: toolUseId, Value: actual tool result dict
        self.active_background_tasks = {} # Key: toolUseId, Value: asyncio.Task
        self.completed_async_tool_results = {}
        self.pending_screenshot_events = {}  # Key: image_analysis_operation_id, Value: asyncio.Event
        self.received_screenshot_data = {} # Key: image_analysis_operation_id, Value: imageDataUrl (string)        

        logger.info(f"Initialized BedrockStreamManager with tool handlers: {list(self.tool_handlers.keys())}")       

    def _initialize_client(self):
        """Initialize the Bedrock client."""
        config = Config(
            endpoint_uri=f"https://bedrock-runtime.{self.region}.amazonaws.com",
            region=self.region,
            aws_credentials_identity_resolver=EnvironmentCredentialsResolver(),
            http_auth_scheme_resolver=HTTPAuthSchemeResolver(),
            http_auth_schemes={"aws.auth#sigv4": SigV4AuthScheme()},
        )
        self.bedrock_client = BedrockRuntimeClient(config=config)
        logger.info("BedrockRuntimeClient initialized.")

    async def initialize_stream(self):
        """Initialize the bidirectional stream with Bedrock."""
        if not self.bedrock_client:
            self._initialize_client()

        try:
            logger.info(f"Initializing Bedrock stream for model: {self.model_id}")
            self.stream_response = (
                await self.bedrock_client.invoke_model_with_bidirectional_stream(
                    InvokeModelWithBidirectionalStreamOperationInput(
                        model_id=self.model_id
                    )
                )
            )
            self.is_active = True # Mark active *after* successful stream creation

            # Start listening for responses
            self.response_task = asyncio.create_task(self._process_responses())

            # Start processing audio input
            asyncio.create_task(self._process_audio_input())

            # Wait a bit to ensure everything is set up
            await asyncio.sleep(0.1)

            logger.info("Bedrock stream initialized successfully")
            return self
        except Exception as e:
            self.is_active = False # Ensure not active if init fails
            error_msg = f"Failed to initialize Bedrock stream: {str(e)}"
            logger.error(error_msg, exc_info=True) # Log with exc_info
            raise ConnectionError(error_msg)

    async def send_raw_event(self, event_data):
        """Send a raw event to the Bedrock stream."""
        if not self.stream_response or not self.is_active:
            logger.warning("Bedrock stream not initialized or closed. Cannot send event.")
            return
        
        # For all other events, continue with normal processing
        # Convert to JSON string if it's a dict
        if isinstance(event_data, dict):
            event_json = json.dumps(event_data)
        else:
            event_json = event_data

        # Create the event chunk
        event = InvokeModelWithBidirectionalStreamInputChunk(
            value=BidirectionalInputPayloadPart(bytes_=event_json.encode("utf-8"))
        )

        try:
            await self.stream_response.input_stream.send(event)

            # Define event_type outside the conditional blocks
            if isinstance(event_data, dict):
                event_type = list(event_data.get("event", {}).keys())
            else:
                event_type = list(json.loads(event_json).get("event", {}).keys())

            if len(event_json) > 200:
                if (
                    "audioInput" not in event_type
                ):  # constant stream of audio inputs so we don't want to log them all
                    logger.info(f"Sent event type: {event_type}")
            else:
                if (
                    "audioInput" not in event_type
                ):  # constant stream of audio inputs so we don't want to log them all
                    logger.info(f"Sent event type: {event_type}")
        except Exception as e:
            logger.error(f"Error sending event to Bedrock: {str(e)}", exc_info=True)
            # This could be a critical error, consider how to handle upstream
            # For now, it will likely break _process_responses or other interactions

    def detect_speech_in_audio(self, audio_base64): # Not currently used for gating
        """
        Detect if audio contains speech based on amplitude level.
        Returns True if speech is detected, False otherwise.
        """
        try:
            # Decode base64 audio data
            audio_bytes = base64.b64decode(audio_base64)
            
            # Convert to numpy array, assuming PCM 16-bit audio
            # This is a simplified approach - adjust based on your actual audio format
            audio_array = np.frombuffer(audio_bytes, dtype=np.int16)
            
            # Normalize to [-1, 1]
            audio_array = audio_array.astype(np.float32) / 32768.0
            
            # Calculate RMS amplitude
            rms = np.sqrt(np.mean(np.square(audio_array)))
            
            # Define threshold for speech detection
            audio_level_threshold = 0.1  # Adjusted threshold, was 0.2 (too high for normalized)
            
            # Check if RMS is above threshold
            if rms > audio_level_threshold:
                if not getattr(self, 'speech_detected', False):
                    logger.info(f"Speech detected with RMS level: {rms}")
                    self.speech_detected = True
                    return True
            else:
                # Reset speech detected flag if level drops
                self.speech_detected = False
            
            return False
        except Exception as e:
            logger.error(f"Error detecting speech: {str(e)}")
            return False

    async def _process_audio_input(self):
        """Process audio input from the queue and send to Bedrock."""
        while self.is_active:
            try:
                # Get audio data from the queue
                data = await self.audio_input_queue.get()

                # Extract data from the queue item
                prompt_name = data.get("prompt_name")
                content_name = data.get("content_name")
                audio_bytes = data.get("audio_bytes")

                if not audio_bytes or not prompt_name or not content_name:
                    logger.info("Missing required audio data properties")
                    continue

                # Check for speech in the audio using our internal method
                if self.detect_speech_in_audio(audio_bytes):
                    now = time.time()

                # Create the audio input event
                audio_event = {
                    "event": {
                        "audioInput": {
                            "promptName": prompt_name,
                            "contentName": content_name,
                            "content": (
                                audio_bytes.decode("utf-8")
                                if isinstance(audio_bytes, bytes)
                                else audio_bytes
                            ),
                            "role": "USER",
                        }
                    }
                }

                # Send the event
                await self.send_raw_event(audio_event)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.info(f"Error processing audio: {e}", exc_info=True)

    def add_audio_chunk(self, prompt_name, content_name, audio_data):
        """Add an audio chunk to the queue."""
        # The audio_data is already a base64 string from the frontend
        self.audio_input_queue.put_nowait(
            {
                "prompt_name": prompt_name,
                "content_name": content_name,
                "audio_bytes": audio_data,
            }
        )

    async def _process_responses(self):
        logger.info(f"Bedrock response processing task started for prompt: {self.prompt_name if self.prompt_name else 'N/A'}")
        try:
            while self.is_active:
                try:
                    output_event = await self.stream_response.await_output()
                    if not output_event: # Stream might have closed
                        logger.info("Received no output_event from Bedrock, stream might be closing.")
                        break
                    
                    result = await output_event[1].receive() # output_event is a tuple
                    if result.value and result.value.bytes_:
                        response_data = result.value.bytes_.decode("utf-8")
                        try:
                            # response_data = result.value.bytes_.decode("utf-8")
                            json_data = json.loads(response_data)

                            # Handle different response types
                            if "event" in json_data:
                                event_data = json_data["event"]
                                if "contentStart" in event_data:
                                    logging.debug("Content start detected")
                                    content_start = event_data["contentStart"]
                                    # Check for speculative content
                                    if "additionalModelFields" in content_start:
                                        try:
                                            additional_fields = json.loads(
                                                content_start["additionalModelFields"]
                                            )
                                            if (
                                                additional_fields.get("generationStage")
                                                == "SPECULATIVE"
                                            ):
                                                logging.debug(
                                                    "Speculative content detected"
                                                )
                                        except json.JSONDecodeError:
                                            logging.error(
                                                "Error parsing additionalModelFields",
                                                exc_info=True,
                                            )
                                elif "textOutput" in event_data:
                                    text_content = event_data["textOutput"]["content"]
                                    role = event_data["textOutput"]["role"]
                                    if role == "ASSISTANT":
                                        logger.info(f"Assistant Message Redacted")
                                        # here you could log the message for testing
                                    elif role == "USER":
                                        logger.info(f"User Message Redacted")
                                        # here you could log the message for testing
                                # elif 'audioOutput' in event_data:
                                #     audio_content_event = event_data['audioOutput']
                                #     # Log what specific audio event this is for (e.g., based on contentId or if it's for ASSISTANT role)
                                #     # This audioOutput is Nova Sonic speaking. If it's right after a toolResult placeholder was sent,
                                #     # this is the audio for that placeholder.
                                #     logger.info(f"BEDROCK_AUDIO_OUT: Received audioOutput from Bedrock (contentId: {audio_content_event.get('contentId', 'N/A')}, role: {event_data.get('contentStart', {}).get('role', 'N/A') if 'contentStart' in event_data else audio_content_event.get('role', 'ASSISTANT?')}). About to queue for frontend.")
                                #     await self.output_queue.put(json_data) # json_data is the full event containing audioOutput
                                #     logger.info(f"BEDROCK_AUDIO_OUT: Queued audioOutput (contentId: {audio_content_event.get('contentId', 'N/A')}) onto output_queue.")
                                # Handle tool use detection
                                elif "toolUse" in event_data:
                                    self.toolUseContent = event_data["toolUse"]
                                    self.toolName = event_data["toolUse"]["toolName"]
                                    self.toolUseId = event_data["toolUse"]["toolUseId"]
                                    logger.info(
                                        f"Tool use detected: {self.toolName}, ID: {self.toolUseId}"
                                    )

                                # Process tool use when content ends
                                elif (
                                    "contentEnd" in event_data
                                    and event_data.get("contentEnd", {}).get("type")
                                    == "TOOL"
                                ):
                                    logger.info(
                                        "Processing tool use and sending result"
                                    )

                                    # Process the tool use
                                    toolResult = await self.processToolUse(
                                        self.toolName, self.toolUseContent
                                    )

                                    # Create a unique content name for this tool result
                                    toolContent = str(uuid.uuid4())

                                    logger.info(f"Tool Use Id {toolContent}")

                                    # Send tool start event
                                    tool_start_event = {
                                        "event": {
                                            "contentStart": {
                                                "interactive": True,
                                                "promptName": self.prompt_name,
                                                "contentName": toolContent,
                                                "type": "TOOL",
                                                "role": "TOOL",
                                                "toolResultInputConfiguration": {
                                                    "toolUseId": self.toolUseId,
                                                    "type": "TEXT",
                                                    "textInputConfiguration": {
                                                        "mediaType": "text/plain"
                                                    },
                                                },
                                            }
                                        }
                                    }
                                    await self.send_raw_event(
                                        json.dumps(tool_start_event)
                                    )
                                    logger.info("Sent Content Start for Tool Event")

                                    # Send tool result event
                                    if isinstance(toolResult, dict):
                                        try:
                                            content_json_string = json.dumps(toolResult)
                                            logger.info("JSON serialization successful:")
                                        except TypeError as e:
                                            logger.error(f"Error: JSON serialization failed: {e}")
                                        except Exception as e:
                                            logger.error(f"An unexpected error occurred: {e}")
                                    else:
                                        content_json_string = str(toolResult)

                                    # check if tool use resulted in an error that needs to be reported to Sonic
                                    status = (
                                        "error"
                                        if toolResult.get("status") == "error"
                                        else "success"
                                    )
                                    # logger.info(f"Tool result {toolResult} and value of status is {status}")

                                    tool_result_event = {
                                        "event": {
                                            "toolResult": {
                                                "promptName": self.prompt_name,
                                                "contentName": toolContent,
                                                "content": content_json_string,
                                                "status": status,
                                            }
                                        }
                                    }

                                    await self.send_raw_event(
                                        json.dumps(tool_result_event)
                                    )
                                    logger.info('Sent ToolResultEvent')


                                    # Send tool content end event
                                    tool_content_end_event = {
                                        "event": {
                                            "contentEnd": {
                                                "promptName": self.prompt_name,
                                                "contentName": toolContent,
                                            }
                                        }
                                    }
                                    await self.send_raw_event(
                                        json.dumps(tool_content_end_event)
                                    )

                            # Put the response in the output queue for forwarding to the frontend
                            await self.output_queue.put(json_data)
                        except json.JSONDecodeError:
                            logger.error(f"Failed to parse JSON from Bedrock: {response_data}")
                            await self.output_queue.put({"raw_data": response_data, "error": "JSONDecodeError"})
                    elif result.value is None: # Stream might have ended gracefully from Bedrock side
                        logger.info("Received null result value from Bedrock, stream may have ended.")
                        break
                except StopAsyncIteration:
                    logger.info(f"Bedrock stream ended (StopAsyncIteration) for prompt {self.prompt_name if self.prompt_name else 'N/A'}.")
                    break
                except asyncio.CancelledError:
                    logger.info(f"Bedrock response processing task cancelled for prompt {self.prompt_name if self.prompt_name else 'N/A'}.")
                    self.is_active = False # Ensure deactivated on cancellation
                    raise # Re-raise to allow task cleanup
                except Exception as e:
                    error_message_str = str(e)
                    logger.error(f"Error receiving response from Bedrock: {error_message_str} for prompt {self.prompt_name if self.prompt_name else 'N/A'}", exc_info=True)
                    is_fatal_bedrock_error = "Invalid voice ID" in error_message_str or \
                                             "ValidationException" in error_message_str or \
                                             "Error(s):" in error_message_str 
                    if is_fatal_bedrock_error:
                        error_payload = {
                            "event": {
                                "error": {
                                    "type": "BedrockStreamError",
                                    "message": f"{error_message_str.splitlines()[0]}",
                                    "fatal": True
                                }
                            }
                        }
                        try:
                            await self.output_queue.put(error_payload)
                            logger.info("Sent fatal Bedrock stream error to frontend.")
                        except Exception as q_err:
                            logger.error(f"Failed to queue error message for frontend: {q_err}")
                    self.is_active = False # Deactivate on error
                    break # Exit the loop
            logger.info(f"Bedrock response processing task finished for prompt {self.prompt_name if self.prompt_name else 'N/A'}.")
        finally:
            logger.info(f"BedrockStreamManager._process_responses finally block. is_active: {self.is_active} for prompt {self.prompt_name if self.prompt_name else 'N/A'}")
            if self.is_active: # Should be false if loop broken by error/end
                 self.is_active = False
            # Signal that there will be no more items for the output queue from this task
            await self.output_queue.put(None) 

    async def processToolUse(self, toolName, toolUseContent):
        """Process tool use requests and return results"""
        logger.info(f"Processing Tool Use: {toolName}")
        logger.debug(f"Tool Use Content: {toolUseContent}")

        tool_key = toolName.lower()
        if tool_key in self.tool_handlers:
            handler = self.tool_handlers[tool_key]
            try:
                # Pass 'self' (the BedrockStreamManager instance) to handlers that need it for async tasks
                # if tool_key == "agentsearch": # This is a simple way to identify handlers needing 'self'
                if tool_key in ["agentsearch", "imageanalyzer"]: # Add other tools that need 'self' here
                    result_payload = await handler(self, toolUseContent)
                # Add other async tools that need 'self' to this condition,
                # OR refactor to a more generic way for handlers to declare this need.
                else: 
                    # For simple, synchronous handlers that don't spawn background tasks managed by the manager
                    result_payload = await handler(toolUseContent) 
                return result_payload
            except Exception as e:
                logger.error(f"Error executing tool {toolName} via its handler: {e}", exc_info=True)
                return {
                    "result": f"An unexpected error occurred while executing tool {toolName}.",
                    "status": "error"
                }
        else:
            logger.warning(f"No handler registered for tool: {toolName}")
            return {
                "result": f"Tool {toolName} is not implemented or recognized by the backend.",
                "status": "error"
            }
        

    async def launch_background_tool_task(self, tool_use_id: str, tool_name: str, actual_tool_coroutine_factory):
        """
        Launches and manages a background task for a long-running tool.
        Notification to the frontend will be sent via self.output_queue.
        actual_tool_coroutine_factory: A function that returns the coroutine for the actual tool execution.
                                    This coroutine should return the result payload for caching.
        """
        if tool_use_id in self.active_background_tasks:
            logger.warning(f"Background task for tool {tool_name} (ID: {tool_use_id}) is already running. Ignoring new request.")
            # Or, you could decide to cancel and restart, or queue, depending on desired behavior.
            # For now, we assume one active task per toolUseId.
            return

        async def task_wrapper():
            logger.info(f"Background task started for {tool_name}, ID: {tool_use_id}")
            result_payload_for_cache = None
            notification_status = "error"
            notification_message_content = f"An unknown error occurred while processing the {tool_name}."

            try:
                actual_task_coro = actual_tool_coroutine_factory()
                result_payload_for_cache = await actual_task_coro
                
                # Cache the result using the CORRECT strategy
                cache_key = tool_name.lower() # e.g., 'agentsearch'
                self.completed_async_tool_results[cache_key] = result_payload_for_cache
                
                # Updated logging to be clear and consistent
                query_info = result_payload_for_cache.get('originalQuery', 'N/A') if isinstance(result_payload_for_cache, dict) else 'N/A'
                logger.info(f"Background task for '{tool_name}' (display name) completed. Result for query '{query_info}' cached under key '{cache_key}' in 'completed_async_tool_results'.")
                logger.info(f"Current state of 'completed_async_tool_results': {self.completed_async_tool_results}")                

                # REMOVE THE REDUNDANT/INCORRECT CACHING LINE:
                # self.pending_tool_results[tool_use_id] = result_payload_for_cache # <<< DELETE THIS LINE

                notification_status = "success"
                notification_message_content = f"The {tool_name} operation (ID: {tool_use_id}) has completed. You can now ask for the results."
                # This log can be removed or kept, but the one above is more accurate about the cache key.
                # logger.info(f"Background task for {tool_name} (ID: {tool_use_id}) completed. Result cached: {str(result_payload_for_cache)[:100]}...")


            except asyncio.CancelledError:
                logger.info(f"Background task for {tool_name} (ID: {tool_use_id}) was cancelled.")
                return 
            except Exception as e:
                logger.error(f"Error in background task for {tool_name} (ID: {tool_use_id}): {e}", exc_info=True)
                notification_message_content = f"An error occurred in the background while processing {tool_name} (ID: {tool_use_id}): {str(e)}"
            finally:
                if tool_use_id in self.active_background_tasks:
                    del self.active_background_tasks[tool_use_id]

            # ... (rest of the notification sending logic remains the same) ...
            custom_notification_to_frontend = {
                "customEvent": "toolCompletionNotification",
                "payload": {
                    "toolName": tool_name,
                    "toolUseId": tool_use_id,
                    "status": notification_status,
                    "message": notification_message_content,
                }
            }
            try:
                await self.output_queue.put(custom_notification_to_frontend)
                logger.info(f"Queued toolCompletionNotification for {tool_name} (ID: {tool_use_id}) to frontend via output_queue.")
            except Exception as q_err:
                logger.error(f"Failed to queue toolCompletionNotification for {tool_name} (ID: {tool_use_id}): {q_err}")

        # ... (task creation and storage remains the same)

        # Create and store the background task
        background_task = asyncio.create_task(task_wrapper())
        self.active_background_tasks[tool_use_id] = background_task
        logger.info(f"Scheduled background task for tool {tool_name} with ID {tool_use_id}")

    async def deliver_screenshot_data(self, analysis_id: str, image_data_url: str | None, error_message: str | None = None):
        """
        Delivers screenshot data (or error) from frontend to the waiting background task.
        """
        if analysis_id in self.pending_screenshot_events:
            if error_message:
                self.received_screenshot_data[analysis_id] = {"error": error_message} # Store error
                logger.info(f"MANAGER: Screenshot capture error for {analysis_id} delivered: {error_message}")
            elif image_data_url:
                self.received_screenshot_data[analysis_id] = {"imageDataUrl": image_data_url} # Store data
                logger.info(f"MANAGER: Screenshot data for {analysis_id} delivered and event will be set.")
            else: # Should not happen if called correctly
                self.received_screenshot_data[analysis_id] = {"error": "No image data and no error message provided."}
                logger.warning(f"MANAGER: deliver_screenshot_data called for {analysis_id} with no data and no error.")

            event_to_set = self.pending_screenshot_events[analysis_id]
            event_to_set.set() # Wake up the waiting _execute_image_analysis_remotely task
        else:
            logger.warning(f"MANAGER: Received screenshot data/error for unknown or already processed analysis ID: {analysis_id}")

async def websocket_handler(websocket, path=None):
    """Handle WebSocket connections from the frontend without authentication."""
    # Debug info
    logger.info(f"New WebSocket connection with path: {path}")

    # Send authentication success message to maintain compatibility with frontend
    try:
        await websocket.send(
            json.dumps(
                {
                    "event": {
                        "connectionStatus": {
                            "status": "authenticated",
                            "message": "Connection authenticated successfully",
                        }
                    }
                }
            )
        )
    except Exception as e:
        logger.error(f"Failed to send connection message: {e}")
        return

    # Create a new stream manager for this connection
    stream_manager = BedrockStreamManager(
        model_id="amazon.nova-sonic-v1:0", region="us-east-1"
    )

    # Initialize the Bedrock stream
    # await stream_manager.initialize_stream()
    try:
        await stream_manager.initialize_stream()
    except Exception as init_err:
        logger.error(f"Bedrock stream initialization failed for client: {init_err}")
        error_payload = {
            "event": {
                "error": {
                    "type": "BedrockInitializationError",
                    "message": f"Failed to initialize Bedrock connection: {str(init_err).splitlines()[0]}",
                    "fatal": True
                }
            }
        }
        try:
            await websocket.send(json.dumps(error_payload))
        except websockets.exceptions.ConnectionClosed:
            logger.info("Client connection closed before Bedrock init error could be sent.")
        except Exception as ws_send_err:
            logger.error(f"Failed to send Bedrock init error to client: {ws_send_err}")
        
        # Clean up the manager and close WebSocket if initialization failed
        stream_manager.is_active = False # Ensure loops in manager don't run
        if stream_manager.response_task:
            stream_manager.response_task.cancel()
        # Any other specific cleanup for stream_manager if partially initialized
        
        await websocket.close(code=1011, reason="Bedrock initialization failed") # 1011: Internal Error
        return # Exit this handler

    # Start a task to forward responses from Bedrock to the WebSocket
    forward_task = asyncio.create_task(forward_responses(websocket, stream_manager))

    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                custom_event_type = data.get("customEvent") # frontend will send a message to backend OOB
                if custom_event_type == "capturedScreenshotData":
                    logger.info("WS_HANDLER: 'capturedScreenshotData' customEvent received from frontend!") # For debugging
                    payload = data.get("payload", {})
                    analysis_id = payload.get("imageAnalysisId") # Standardized key
                    image_data_url = payload.get("imageDataUrl")
                    error_from_frontend = payload.get("error")

                    if analysis_id:
                        if error_from_frontend:
                            logger.error(f"WS_HANDLER: Frontend reported error capturing screenshot for {analysis_id}: {error_from_frontend}")
                            # Deliver None or error status to potentially unblock the waiting task with an error
                            await stream_manager.deliver_screenshot_data(analysis_id, None, error_from_frontend)
                        elif image_data_url:
                            logger.info(f"WS_HANDLER: Received screenshot data for analysis ID: {analysis_id}. Attempting to deliver.")
                            await stream_manager.deliver_screenshot_data(analysis_id, image_data_url, None)
                        else:
                            logger.warning(f"WS_HANDLER: 'capturedScreenshotData' for {analysis_id} received without imageDataUrl or error.")
                            await stream_manager.deliver_screenshot_data(analysis_id, None, "Missing image data from frontend.")
                    else:
                        logger.error(f"WS_HANDLER: Missing imageAnalysisId in capturedScreenshotData: {payload}")

                elif "event" in data:
                    event_type = list(data["event"].keys())[0]

                    # Store prompt name and content names if provided
                    if event_type == "promptStart":
                        stream_manager.prompt_name = data["event"]["promptStart"][
                            "promptName"
                        ]
                    elif (
                        event_type == "contentStart"
                        and data["event"]["contentStart"].get("type") == "AUDIO"
                    ):
                        stream_manager.audio_content_name = data["event"][
                            "contentStart"
                        ]["contentName"]

                    # Handle audio input separately
                    if event_type == "audioInput":
                        # Extract audio data
                        prompt_name = data["event"]["audioInput"]["promptName"]
                        content_name = data["event"]["audioInput"]["contentName"]
                        audio_base64 = data["event"]["audioInput"]["content"]

                        # Add to the audio queue
                        stream_manager.add_audio_chunk(
                            prompt_name, content_name, audio_base64
                        )
                    else:
                        # Send other events directly to Bedrock
                        await stream_manager.send_raw_event(data)
            except json.JSONDecodeError:
                logger.error("Invalid JSON received from WebSocket")
            except Exception as e:
                logger.error(f"Error processing WebSocket message: {e}", exc_info=True)

    except websockets.exceptions.ConnectionClosed:
        logger.info("WebSocket connection closed")
    finally:
        # Clean up the asyncio task
        forward_task.cancel()


async def forward_responses(websocket, stream_manager):
    """Forward responses from Bedrock to the WebSocket."""
    logger.debug("FORWARD_TASK: Started for a new WebSocket connection.")
    try:
        while True:
            response = await stream_manager.output_queue.get()
            if response is None: # End of stream signal
                logger.debug("FORWARD_TASK: Received None from output_queue, terminating forwarder.")
                break

            # Identify the type of message being forwarded
            message_type = "Unknown"
            log_detail = str(response)[:150] # Log a snippet

            if isinstance(response, dict):
                if response.get("customEvent") == "toolCompletionNotification":
                    message_type = "CustomToolNotification"
                    log_detail = f"Tool: {response.get('payload', {}).get('toolName')}, Status: {response.get('payload', {}).get('status')}"
                elif response.get("event"):
                    event_key = list(response["event"].keys())[0]
                    message_type = f"BedrockEvent_{event_key}"
                    if event_key == "audioOutput":
                        log_detail = f"ContentId: {response['event']['audioOutput'].get('contentId', 'N/A')}"
                    elif event_key == "textOutput":
                        log_detail = f"Role: {response['event']['textOutput'].get('role')}, Content: {str(response['event']['textOutput'].get('content'))[:50]}..."
            
            logger.debug(f"FORWARD_TASK: Dequeued '{message_type}'. Details: {log_detail}. About to send to WebSocket.")
            
            try:
                await websocket.send(json.dumps(response))
                logger.debug(f"FORWARD_TASK: Successfully sent '{message_type}' to WebSocket.")
            except websockets.exceptions.ConnectionClosed:
                logger.warning("FORWARD_TASK: WebSocket connection closed while trying to send. Terminating forwarder.")
                break
            except Exception as send_err:
                logger.error(f"FORWARD_TASK: Error sending '{message_type}' to WebSocket: {send_err}")
                # Decide if to break or continue
                break 
    except asyncio.CancelledError:
        logger.debug("FORWARD_TASK: Cancelled.")
    except Exception as e:
        logger.error(f"FORWARD_TASK: Unhandled error: {e}", exc_info=True)
    finally:
        logger.debug("FORWARD_TASK: Exiting.")
    # try:
    #     while True:
    #         # Get next response from the output queue
    #         response = await stream_manager.output_queue.get()

    #         # Send to WebSocket
    #         try:
    #             await websocket.send(json.dumps(response))
    #         except websockets.exceptions.ConnectionClosed:
    #             break
    # except asyncio.CancelledError:
    #     # Task was cancelled
    #     pass
    # except Exception as e:
    #     logger.error(f"Error forwarding responses: {e}")


async def main():
    """Main function to run the WebSocket server."""
    # Get port from environment variable or use default
    port = int(os.environ.get("PORT", 8081))

    # Use 0.0.0.0 to listen on all interfaces (required for containers)
    host = "0.0.0.0"

    # Start WebSocket server with the simplified handler
    logger.info(f"Starting WebSocket server on {host}:{port}")

    try:
        async with websockets.serve(websocket_handler, host, port):
            logger.info(f"WebSocket server started {host}:{port}")
            # Keep the server running forever
            await asyncio.Future()
    except Exception as e:
        logger.error(f"Server startup error: {e}", exc_info=True)


if __name__ == "__main__":
    # Run the main function
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Server stopped by user")
    except Exception as e:
        logger.error(f"Server error: {e}", exc_info=True)