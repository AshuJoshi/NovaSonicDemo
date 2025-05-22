# backend/lib/image_analyzer/image_analyzer.py
import boto3
import logging
import base64
import asyncio

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

class ImageAnalyzerLLMClient: # Renamed for clarity, this is the LLM interaction part
    def __init__(self, region="us-east-1"):
        self.region = region
        self.bedrock_runtime = boto3.client('bedrock-runtime', region_name=region)

    async def describe_image_with_llm(self, base64_image_data: str, prompt_text: str) -> str:
        """
        Sends base64 image data and a prompt to a Bedrock multimodal model (e.g., Claude 3 Sonnet/Haiku, Titan)
        and returns the textual description.
        """
        logger.info(f"Sending image to LLM for description. Prompt: '{prompt_text}', Image (first 60 chars): {base64_image_data[:60]}...")
        image_bytes = base64.b64decode(base64_image_data)

        # model_id = "anthropic.claude-3-sonnet-20240229-v1:0" 
        model_id = "amazon.nova-lite-v1:0"
        
        # Use a thread pool to avoid blocking the event loop
        loop = asyncio.get_event_loop()
        try:
            response = await loop.run_in_executor(
                None,  # Uses default ThreadPoolExecutor
                lambda: self.bedrock_runtime.converse(
                    modelId=model_id,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "text": "Describe what you see in this image in two sentences, phrased as 'I see...'."
                                },
                                {
                                    "image": {
                                        "format": "jpeg",
                                        "source": {
                                            "bytes": image_bytes
                                        }
                                    }
                                }
                            ]
                        }
                    ],
                )
            )
            # Extract the text response
            description = None
            for content in response['output']['message']['content']:
                if 'text' in content:
                    description = content['text']
                    break
            
            if description:
                logger.info(f"Image analysis complete: {description[:50]}...")
                self.latest_image_description = description
                return description
            else:
                logger.warning(f"No text content in response from {model_id}")
                return "I couldn't analyze the image."

        except Exception as e:
            logger.error(f"Error during Bedrock converse call for image description: {e}", exc_info=True)
            return f"Error analyzing image with LLM: {str(e)}"