from common.server import A2AServer
from common.types import AgentCard, AgentCapabilities, AgentSkill, MissingAPIKeyError
from common.utils.push_notification_auth import PushNotificationSenderAuth
from agents.bedrock.task_manager import AgentTaskManager
from agents.bedrock.agent import BedrockInlineAgent
import click
import os
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@click.command()
@click.option("--host", "host", default="localhost")
@click.option("--port", "port", default=10000)
def main(host, port):
    """Starts the Bedrock Inline Agent server."""
    try:
        if not os.getenv("TAVILY_API_KEY"):
            raise MissingAPIKeyError("TAVILY_API_KEY environment variable not set.")

        capabilities = AgentCapabilities(streaming=True, pushNotifications=True)
        skill = AgentSkill(
            id="web_and_wiki_search",
            name="Web and Wiki Search Tool",
            description="Helps with searching the web and wikipedia",
            tags=["web search", "wikipedia search"],
            examples=["Tell me about the Cinco de Mayo holiday"],
        )
        agent_card = AgentCard(
            name="Bedrock Inline Agent",
            description="Helps with searching the web and wikipedia",
            url=f"http://{host}:{port}/",
            version="1.0.0",
            defaultInputModes=BedrockInlineAgent.SUPPORTED_CONTENT_TYPES,
            defaultOutputModes=BedrockInlineAgent.SUPPORTED_CONTENT_TYPES,
            capabilities=capabilities,
            skills=[skill],
        )

        notification_sender_auth = PushNotificationSenderAuth()
        notification_sender_auth.generate_jwk()
        server = A2AServer(
            agent_card=agent_card,
            task_manager=AgentTaskManager(agent=BedrockInlineAgent(), notification_sender_auth=notification_sender_auth),
            host=host,
            port=port,
        )

        server.app.add_route(
            "/.well-known/jwks.json", notification_sender_auth.handle_jwks_endpoint, methods=["GET"]
        )

        logger.info(f"Starting server on {host}:{port}")
        server.start()
    except MissingAPIKeyError as e:
        logger.error(f"Error: {e}")
        exit(1)
    except Exception as e:
        logger.error(f"An error occurred during server startup: {e}")
        exit(1)


if __name__ == "__main__":
    main()
