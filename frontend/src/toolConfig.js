// frontend/src/toolConfig.js

/**
 * Provides the list of tool specifications to be sent to Nova Sonic.
 * Each object in the array must conform to the structure expected by
 * Nova Sonic's toolConfiguration.tools array.
 * See: https://docs.aws.amazon.com/nova/latest/userguide/input-events.html#promptstartevent
 */
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
    ];
};