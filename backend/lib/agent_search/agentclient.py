import asyncio
import uuid
import logging
from common.client.card_resolver import A2ACardResolver
from common.client.client import A2AClient
from common.types import TaskState

# Configure logging
# Configure logging with a timestamp in the format
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

class AgentClient:
    def __init__(self,
                 agent_url: str = "http://localhost:10000",
                 push_url: str = "http://localhost:5000"):
        """
        agent_url: Base URL of the A2A agent server (e.g., http://localhost:10000)
        push_url: Base URL for push notifications (if used in future enhancements)
        """
        self.agent_url = agent_url.rstrip('/')
        self.push_url = push_url.rstrip('/')
        self.card_resolver = A2ACardResolver(self.agent_url)
        self.agent_card = None
        self.client = None

    async def discover(self):
        """
        Synchronously fetch the agent card and initialize the A2AClient.
        """
        self.agent_card = self.card_resolver.get_agent_card()
        self.client = A2AClient(agent_card=self.agent_card)
        # Log discovery details
        logger.info(f"Discovered agent: {self.agent_card.name}")
        logger.info(f"Agent URL: {self.agent_url}")
        logger.info(f"Capabilities: {self.agent_card.capabilities}")
        try:
            skills = [skill.id for skill in self.agent_card.skills]
        except Exception:
            skills = []
        logger.info(f"Skills: {skills}")
        return self.agent_card

    async def send_task(self, input_text: str, stream: bool = True):
        """
        Send a user message as a task to the agent, validate completion, and assemble final text.

        input_text: The text to send as the user's message.
        stream: Whether to use streaming if supported by the agent.
        Returns the assembled text on completion.
        """
        # Ensure the client is initialized
        if not self.client:
            self.discover()

        # Create unique identifiers for this task/session
        task_id = uuid.uuid4().hex
        session_id = uuid.uuid4().hex

        # Build the params payload including the required 'id'
        payload = {
            "id": task_id,
            "sessionId": session_id,
            "message": {
                "role": "user",
                "parts": [
                    {"type": "text", "text": input_text}
                ]
            }
        }
        logger.info(f"Sending task id={task_id} session={session_id}")

        # Collect parts for the final assembly
        parts = []

        # Streaming path
        if stream and getattr(self.agent_card.capabilities, 'streaming', False):
            logger.info("Using streaming API...")
            # Stream incremental events
            async for event in self.client.send_task_streaming(payload):
                logger.info(f"stream event => {event.model_dump_json()}")
            # Retrieve final task status when streaming completes
            response = await self.client.get_task({"id": task_id})
        else:
            # Non-streaming path
            logger.info("Using non-streaming API...")
            response = await self.client.send_task(payload)

        # The get_task and send_task responses wrap result under .result for GetTaskResponse or directly for SendTaskResponse
        result = getattr(response, 'result', response)

        # Validate completion state
        state = result.status.state
        if state != TaskState.COMPLETED:
            logger.warning(f"Task {task_id} completed with state={state}")
        else:
            logger.info(f"Task {task_id} completed successfully.")

        # Validate matching sessionId
        if getattr(result, 'sessionId', None) != session_id:
            logger.warning(f"Mismatched sessionId: sent={session_id}, received={getattr(result, 'sessionId', None)}")
        else:
            logger.info(f"SessionId verified: {session_id}")

        # Assemble all text parts from artifacts
        try:
            for artifact in result.artifacts:
                for part in artifact.parts:
                    parts.append(part.text)
        except Exception as e:
            logger.error(f"Error assembling parts: {e}")

        final_text = "".join(parts)
        logger.info(f"Final assembled text:\n{final_text}")
        return final_text

    async def close(self):
        """
        Placeholder for cleanup actions if needed.
        """
        logger.info("Closing AgentClient")
        # No explicit connections to close in current implementation
        pass
