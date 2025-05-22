// NovaSonicChromeExtension/js/toolConfig_extension.js
export const getToolSpecifications = () => {
    return [
        // getWeather tool
        {
            toolSpec: {
                name: "getWeather",
                description: "Get current weather for a given location",
                inputSchema: {
                    // The inputSchema.json value must be a JSON *string*
                    json: JSON.stringify({
                        type: "object",
                        properties: {
                            location: {
                                type: "string",
                                description: "Name of the city (e.g. Seattle, WA)"
                            }
                        },
                        required: ["location"]
                    })
                }
            }
        },
        {
            toolSpec: {
              name: "numberRace",
              description: "A number, an integer to start a number race! I will wait for that many seconds.",
              inputSchema: {
                json: JSON.stringify({
                  type: "object",
                  properties: {
                    number: {
                      type: "integer", 
                      description: "The integer number of seconds to wait."
                    }
                  },
                  required: ["number"]
                })
              }
            }
        },
        {
          toolSpec: {
              name: "agentSearch",
              description: "Performs a detailed search using an intelligent agent for a given query. This process may take some time. You will be notified in the chat when results are ready.",
              inputSchema: {
                json: JSON.stringify({
                  type: "object",
                  properties: {
                    query: {
                      type: "string",
                      description: "The search query, topic, or question for the agent."
                    }
                  },
                  required: ["query"]
                })
              }
            }
        },
        {
            toolSpec: {
                name: "imageAnalyzer", // Match the handler registration key in backend
                description: "Captures an image of the current web page and provides an AI-generated description. You will be notified when the analysis is complete.",
                inputSchema: {
                    json: JSON.stringify({
                        type: "object",
                        properties: {
                            "context": { // Matches the 'context' expected by handle_imageanalyzer
                                type: "string",
                                description: "Optional: Provide context or a specific question about the image (e.g., 'what are the dominant colors?' or 'summarize this screenshot')."
                            }
                        },
                        required: [] // context is optional
                    })
                }
            }
        }
    ];
};