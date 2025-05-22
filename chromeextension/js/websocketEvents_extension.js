import AudioPlayer from "./lib/play/AudioPlayer_extension.js";
import ChatHistoryManager from "./lib/util/ChatHistoryManager_extension.js";
import { getToolSpecifications } from './toolConfig_extension.js';

const audioPlayer = new AudioPlayer(); // Single instance of AudioPlayer

// const SYSTEM_PROMPT = `You are a friendly assistant.
// You and the user will engage in a spoken dialog exchanging the transcripts of a natural real-time conversation. Keep your responses short, generally two or three sentences for chatty scenarios.
// `

const SYSTEM_PROMPT = `You are a friendly assistant with access to the following external tools:

- \`imageAnalyzer\` tool is to capture the screenshot of the active tab, and request an image analysis. Use this tool when the user asks to "capture image," "analyze page," or similar. It takes time, so it will give a placeholder and then notify. **When the user asks for "image analysis results" or "what the image shows" after a notification, invoke the \`imageAnalyzer\` tool again to retrieve the description.** Do NOT use your internal memory for this.
- \`agentSearch\` tool is to perform a Web Search. Use this when the user asks for an external search. It also takes time and will notify. **When the user asks for "search results" or "web search findings" after a notification, invoke the \`agentSearch\` tool again.**
- \`getWeather\` tool is to get weather for a city.

You and the user will engage in a spoken dialog exchanging the transcripts of a natural real-time conversation. Keep your responses short, generally two or three sentences for chatty scenarios.
`
export class WebSocketEventManager {
  constructor(wsUrl, selectedVoiceId = "matthew") { // Default to "matthew" or your verified default
    console.log("WebSocketEventManager constructor called with voiceId:", selectedVoiceId);
    this.wsUrl = wsUrl;
    this.selectedVoiceId = selectedVoiceId;
    this.promptName = null;
    this.audioContentName = null;
  
    this.currentAudioConfig = null;
    this.isProcessing = false; // Manages if an active session is ongoing
    this.isCleaningUp = false;

    this.chat = { history: [] };
    this.chatRef = { current: this.chat };

    this.chatHistoryManager = ChatHistoryManager.getInstance(
      this.chatRef,
      (newChat) => {
        this.chat = { ...newChat };
        this.chatRef.current = this.chat;
        this.updateChatUI();
      }
    );

    // Create and connect WebSocket
    this.socket = new WebSocket(this.wsUrl);
    this.setupSocketListeners();
  }

  updateChatUI() {
    const chatContainer = document.getElementById("chat-container");
    if (!chatContainer) {
      console.error("Chat container not found");
      return;
    }
    chatContainer.innerHTML = ""; // Clear existing chat messages
    this.chat.history.forEach((item) => {
      if (item.endOfConversation) {
        const endDiv = document.createElement("div");
        endDiv.className = "message system";
        endDiv.textContent = "Conversation ended";
        chatContainer.appendChild(endDiv);
        return;
      }
      if (item.role) {
        const messageDiv = document.createElement("div");
        const roleLowerCase = item.role.toLowerCase();
        messageDiv.className = `message ${roleLowerCase}`;
        const roleLabel = document.createElement("div");
        roleLabel.className = "role-label";
        roleLabel.textContent = item.role;
        messageDiv.appendChild(roleLabel);
        const content = document.createElement("div");
        content.textContent = item.message || "No content";
        messageDiv.appendChild(content);
        chatContainer.appendChild(messageDiv);
      }
    });
    chatContainer.scrollTop = chatContainer.scrollHeight;
  }

  handleRequestScreenshot(payload) {
    console.log("Received requestScreenshotForAnalysis from backend:", payload);
    const analysisId = payload.imageAnalysisId;
    if (analysisId && typeof window.captureAndSendScreenshotForAnalysis === 'function') {
        // Call the global function defined in main_extension.js
        window.captureAndSendScreenshotForAnalysis(analysisId);
    } else {
        console.error("Missing imageAnalysisId or captureAndSendScreenshotForAnalysis function not found. Payload:", payload);
    }
  }
  
  sendScreenshotDataToBackend(analysisId, imageDataUrl, error = null) {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
        console.error("Cannot send screenshot data: WebSocket not open or not processing.");
        return;
    }
    const payload = {
        customEvent: "capturedScreenshotData",
        payload: {
            imageAnalysisId: analysisId,
            imageDataUrl: imageDataUrl, // Can be null if there was an error
            error: error // Include error message if any
        }
    };
    this.sendEvent(payload); // sendEvent already does JSON.stringify
    if (error) {
        console.log(`Sent error for screenshot capture to backend for analysisId: ${analysisId}, Error: ${error}`);
    } else {
        console.log(`Sent capturedScreenshotData to backend for analysisId: ${analysisId} (image data truncated for log)`);
    }
  }
  setupSocketListeners() {
    this.socket.onopen = () => {
      console.log("WebSocket Connected");
      this.updateStatus("Connected", "connected");
      // isProcessing should be set by start/stop actions rather than purely by connection open/close
      // this.isProcessing = true; 
      this.startSession(); // Automatically start session on connection
      
      // AudioPlayer.start() creates its own AudioContext.
      // It's async, so we can call it and let it manage its lifecycle.
      audioPlayer.start().catch(err => {
        console.error("Error starting AudioPlayer:", err);
        this.updateStatus("Audio player error", "error");
      });
    };

    this.socket.onmessage = (event) => {
      // console.log("🔍 RAW WEBSOCKET MESSAGE:", event.data); // Can be very verbose
      try {
        const data = JSON.parse(event.data);
        // // console.log("📦 PARSED MESSAGE:", JSON.stringify(data, null, 2)); // Verbose
        // if (data.event) {
        //   // console.log("🔔 EVENT TYPE:", Object.keys(data.event)[0]); // Verbose
        // } else {
        //   console.log("⚠️ NO EVENT OBJECT FOUND IN MESSAGE. Keys:", Object.keys(data));
        // }
        // this.handleMessage(data);
          if (data.customEvent && data.customEvent === "toolCompletionNotification") {
              this.handleToolCompletionNotification(data.payload);
          } else if (data.customEvent === "requestScreenshotForAnalysis") { 
              this.handleRequestScreenshot(data.payload);
          } else if (data.event) {
            // Assuming original logic for Bedrock events is here or in a separate method
            this.handleBedrockEvent(data.event); // Example: renamed original handler
          } else if (data.raw_data && data.error === "JSONDecodeError") { // Handle raw data pass-through from backend
            console.error("Received raw, unparseable data from backend (likely Bedrock error):", data.raw_data);
            this.chatHistoryManager.addTextMessage({
              role: "SYSTEM",
              message: `Error: Received unparseable data from the server. Details might be in backend logs.`,
              isError: true
            });
          }
          else {
              console.warn("Received WebSocket message of unknown structure:", data);
          }
        } catch (e) {
          console.error("❌ Error parsing WebSocket message:", e);
          console.error("📄 Raw message data:", event.data);
        }
    };

    this.socket.onerror = (error) => {
      console.error("WebSocket Error:", error);
      this.updateStatus("Connection error", "error");
      this.cleanup(true, "WebSocket connection error"); // Treat WebSocket errors as fatal for the session
      // this.isProcessing = false; // Ensure isProcessing is false on error
    };

    this.socket.onclose = (event) => {
      console.log("WebSocket Disconnected. Code:", event.code, "Reason:", event.reason);
      // If cleanup hasn't been called already (e.g. by a fatal error handler or user stop)
      if (this.isProcessing || audioPlayer.initialized) { // If we thought we were still active
            // The status might have been set by a fatal error.
            // If not, set it to a generic disconnected message.
            const statusDiv = document.getElementById("status");
            if (statusDiv && !statusDiv.classList.contains("error")) {
                this.updateStatus("Disconnected", "disconnected");
            }
      }
      // Ensure cleanup runs to reset state and notify UI, if not already run by fatal error path
      this.cleanup(false, "WebSocket closed"); // Not necessarily fatal from app logic, but session is over
   
    };
  }

  async sendEvent(event) {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
      console.error(
        "WebSocket is not open. Cannot send event. Current state:",
        this.socket?.readyState
      );
      return;
    }
    try {
      // console.log("Sending event:", JSON.stringify(event, null, 2)); // Verbose
      this.socket.send(JSON.stringify(event));
    } catch (error) {
      console.error("Error sending event:", error);
      this.updateStatus("Error sending message", "error");
    }
  }

  handleMessage(data) {
    if (!data.event) {
      console.error("Received message without event object:", data);
      return;
    }
    const event = data.event;
    try {
      if (event.error) { // New: Handle custom error event from backend
        console.error("Error event received from backend:", event.error);
        this.updateStatus(`Error: ${event.error.message}`, "error");
        if (event.error.fatal) {
            console.log("Fatal error received, initiating cleanup.");
            // Cleanup will set isProcessing to false and dispatch 'sessionEndedForUI'
            this.cleanup(true, event.error.message); // Pass fatal flag and error message
        } 
      } else if (event.completionStart) {
        this.promptName = event.completionStart.promptName;
      } else if (event.contentStart) {
        if (event.contentStart.type === "AUDIO") {
          this.currentAudioConfig = event.contentStart.audioOutputConfiguration;
        }
      } else if (event.textOutput) {
        const content = event.textOutput.content;
        const role = event.textOutput.role;
        const messageData = { role: role, message: content };
        this.chatHistoryManager.addTextMessage(messageData);
      } else if (event.audioOutput) {
        if (this.currentAudioConfig && audioPlayer.initialized) {
          audioPlayer.playAudio(
            this.base64ToFloat32Array(event.audioOutput.content)
          );
        }
      } else if (event.toolUse) {
        console.log("TOOL USE received:", event.toolUse);
        // event.toolUse from Nova Sonic typically looks like:
        // { toolUseId: "...", name: "getWeather", input: { location: "Seattle" } }
        // The 'input' here is already parsed JSON by Nova Sonic, not a string like in the backend's toolUseContent.content
        console.log("TOOL USE event received from Nova Sonic:", JSON.stringify(event.toolUse, null, 2));

        const toolName = event.toolUse.name;
        const toolInput = event.toolUse.input; // This 'input' is already a JS object

        if (toolName === "getWeather") {
            const location = toolInput.location; // Access directly
            console.log(`Frontend observed: Nova Sonic wants to call getWeather for location: ${location}`);
        } else if (toolName === "agentSearch") {
            console.log("Frontend observed: Nova Sonic wants to call agentSearch with input:", toolInput.query);
        } else if (toolName === "numberRace") {
            console.log("Frontend observed: Nova Sonic wants to call numberRace with input:", toolInput.number);
        }
      } else if (event.contentEnd) {
        switch (event.contentEnd.type) {
          case "TEXT":
            if (event.contentEnd.stopReason.toUpperCase() === "END_TURN") {
              this.chatHistoryManager.endTurn();
            } else if (event.contentEnd.stopReason.toUpperCase() === "INTERRUPTED") {
              if (audioPlayer.initialized) audioPlayer.bargeIn();
            }
            break;
          default:
            console.log("Received content end for type:", event.contentEnd.type);
        }
      } else if (event.completionEnd) {
        // console.log("Completion end received:", event.completionEnd);
      } else if (event.connectionStatus) { // Handle backend's auth success msg
        console.log("Connection Status from backend:", event.connectionStatus);
        if (event.connectionStatus.status === "authenticated") {
            // This is just an ack from backend, actual session starts with startSession()
        }
      }
      else {
        console.warn("Unknown event type received:", Object.keys(event)[0]);
        console.warn("Full unknown event data:", JSON.stringify(event, null, 2));
      }
    } catch (error) {
      console.error("Error processing message in handleMessage:", error);
      console.error("Original event data from backend:", event);
    }
  }

  base64ToFloat32Array(base64String) {
    try {
      const binaryString = window.atob(base64String);
      const bytes = new Uint8Array(binaryString.length);
      for (let i = 0; i < binaryString.length; i++) {
        bytes[i] = binaryString.charCodeAt(i);
      }
      // Assuming the server sends 16-bit PCM audio
      const int16Array = new Int16Array(bytes.buffer);
      const float32Array = new Float32Array(int16Array.length);
      for (let i = 0; i < int16Array.length; i++) {
        float32Array[i] = int16Array[i] / 32768.0; // Normalize to [-1.0, 1.0)
      }
      return float32Array;
    } catch (e) {
      console.error("Error decoding base64 audio:", e);
      return new Float32Array(0); // Return empty array on error
    }
  }

  updateStatus(message, className) {
    const statusDiv = document.getElementById("status");
    if (statusDiv) {
      statusDiv.textContent = message;
      statusDiv.className = `status ${className}`;
    }
  }

  startSession() {
    console.log("Starting Nova Sonic session...");
    this.isProcessing = true; // Indicate that a session is now active
    const sessionStartEvent = {
      event: {
        sessionStart: {
          inferenceConfiguration: { maxTokens: 10000, topP: 0.95, temperature: 0.9 },
        },
      },
    };
    this.sendEvent(sessionStartEvent);
    this.startPrompt();
  }

  startPrompt() {
    this.promptName = crypto.randomUUID();
    const configuredTools = getToolSpecifications(); 
    const promptStartEvent = {
      event: {
        promptStart: {
          promptName: this.promptName,
          textOutputConfiguration: { mediaType: "text/plain" },
          audioOutputConfiguration: {
            mediaType: "audio/lpcm",
            sampleRateHertz: 24000, // Nova Sonic supports 8k, 16k, 24k for LPCM
            sampleSizeBits: 16,
            channelCount: 1,
            voiceId: this.selectedVoiceId, // Uses the voiceId passed during construction
            encoding: "base64",
            audioType: "SPEECH",
          },
          toolUseOutputConfiguration: { mediaType: "application/json" },
          // toolConfiguration: { tools: [] },
          toolConfiguration: {
            tools: configuredTools, // MODIFIED: Use the dynamically retrieved tool specs
          },
        },
      },
    };
    console.log("Sending promptStart with voiceId:", this.selectedVoiceId);
    console.log(
      "Tool configuration being sent to Nova Sonic:", // Log just the tool config part
      JSON.stringify(promptStartEvent.event.promptStart.toolConfiguration, null, 2)
    );
    this.sendEvent(promptStartEvent);
    this.sendSystemPrompt();
  }

  sendSystemPrompt() { // Called by startPrompt
    const systemContentName = crypto.randomUUID();
    this.sendEvent({
      event: {
        contentStart: {
          promptName: this.promptName,
          contentName: systemContentName,
          type: "TEXT",
          interactive: true, // Should be true for system prompt before user input
          textInputConfiguration: { mediaType: "text/plain" },
          role: "SYSTEM" // Role should be in contentStart for TEXT
        },
      },
    });
    
    this.sendEvent({
      event: {
        textInput: {
          promptName: this.promptName,
          contentName: systemContentName,
          content: SYSTEM_PROMPT,
          // role: "SYSTEM", // Role is defined in contentStart for TEXT
        },
      },
    });
    this.sendEvent({
      event: {
        contentEnd: { promptName: this.promptName, contentName: systemContentName },
      },
    });
    this.startAudioContent();
  }

  startAudioContent() {
    this.audioContentName = crypto.randomUUID();
    this.sendEvent({
      event: {
        contentStart: {
          promptName: this.promptName,
          contentName: this.audioContentName,
          type: "AUDIO",
          interactive: true,
          role: "USER", // This is for user's audio input
          audioInputConfiguration: {
            mediaType: "audio/lpcm",
            sampleRateHertz: 16000, // User microphone capture rate
            sampleSizeBits: 16,
            channelCount: 1,
            audioType: "SPEECH",
            encoding: "base64",
          },
        },
      },
    });
  }

  sendAudioChunk(base64AudioData) {
    if (!this.promptName || !this.audioContentName) {
      console.error("Cannot send audio chunk - missing promptName or audioContentName");
      return;
    }
    if (!this.isProcessing || this.socket.readyState !== WebSocket.OPEN) {
        // console.log("Attempted to send audio chunk but not processing or socket not open."); // Can be verbose
        return;
    }
    this.sendEvent({
      event: {
        audioInput: {
          promptName: this.promptName,
          contentName: this.audioContentName,
          content: base64AudioData,
          role: "USER", // Redundant if already in contentStart, but often included
        },
      },
    });
  }

  endContent() { // Call this when user stops talking (if implementing VAD) or before ending prompt
    if (this.promptName && this.audioContentName) {
        console.log("Sending contentEnd for user audio");
        this.sendEvent({
            event: {
                contentEnd: { promptName: this.promptName, contentName: this.audioContentName },
            },
        });
        this.audioContentName = null; // Reset for next user utterance in same prompt if any
    }
  }

  endPrompt() { // Call this when a full turn of interaction for a given prompt is over
    if (this.promptName) {
        console.log("Sending promptEnd");
        this.sendEvent({
            event: {
                promptEnd: { promptName: this.promptName },
            },
        });
        this.promptName = null; // Reset for a completely new prompt
    }
  }

  endSession() { // Call this to terminate the entire Bedrock session
    console.log("Sending sessionEnd");
    this.sendEvent({ event: { sessionEnd: {} } });
  }

  cleanup(isFatalError = false, statusMessage = "Disconnected by user") {
    if (this.isCleaningUp) { 
      console.log("Cleanup already in progress or completed, skipping redundant call."); 
      return; 
    }
    this.isCleaningUp = true;

    console.log("WebSocketEventManager cleanup initiated. Fatal:", isFatalError, "Message:", statusMessage);
    
    const wasProcessing = this.isProcessing; // Store state before changing
    this.isProcessing = false; // Mark as not processing FIRST

    if (wasProcessing || isFatalError) {
        if (this.socket && this.socket.readyState === WebSocket.OPEN) {
            if (this.audioContentName && this.promptName) this.endContent();
            if (this.promptName) this.endPrompt();
            this.endSession(); // Attempt to gracefully end Bedrock session
        } else {
            console.warn("Socket not open during cleanup, Bedrock session-end commands not sent.");
        }
    }
    
    if (this.socket && (this.socket.readyState === WebSocket.OPEN || this.socket.readyState === WebSocket.CONNECTING)) {
        this.socket.close(1000, isFatalError ? "Fatal error occurred" : "Client requested cleanup");
    }
    
    if (audioPlayer && audioPlayer.initialized) {
        audioPlayer.stop();
    }
    
    this.chatHistoryManager.endConversation();

    // Update status based on what triggered cleanup, avoid overwriting specific fatal error messages
    // if a more generic one is passed.
    const statusDiv = document.getElementById("status");
    if (isFatalError) {
        // If it's a fatal error, the status message passed to cleanup (from handleMessage or onerror) is likely more specific.
        if (statusDiv && statusDiv.textContent !== statusMessage && !statusDiv.classList.contains("error") ) {
            this.updateStatus(statusMessage, "error");
        } else if (!statusDiv || !statusDiv.classList.contains("error")) {
            this.updateStatus(statusMessage, "error");
        }
    } else if (statusDiv && !statusDiv.classList.contains("error")) {
         // Only update if not already an error, and not a fatal error path
         this.updateStatus(statusMessage, "disconnected");
    }


    this.promptName = null;
    this.audioContentName = null;
    this.currentAudioConfig = null;

    // Dispatch an event for main.js to reset UI controls
    // This ensures UI reset happens regardless of how cleanup was triggered
    console.log("Dispatching sessionEndedForUI from cleanup");
    window.dispatchEvent(new CustomEvent('sessionEndedForUI', { detail: { isFatal: isFatalError } }));

    // Reset state
    this.promptName = null;
    this.audioContentName = null;
    this.currentAudioConfig = null;
  }

  // Inside WebSocketEventManager class:

handleBedrockEvent(eventData) { // This would be your original handleMessage logic, now specific to Bedrock events
    // All the existing 'if (event.completionStart)' etc. logic goes here, using eventData
    // For example:
    if (eventData.completionStart) {
      this.promptName = eventData.completionStart.promptName;
    } else if (eventData.contentStart) {
      // ... and so on for textOutput, audioOutput, toolUse, contentEnd, completionEnd
      if (eventData.contentStart.type === "AUDIO") {
        this.currentAudioConfig = eventData.contentStart.audioOutputConfiguration;
      }
    } else if (eventData.textOutput) {
      const content = eventData.textOutput.content;
      const role = eventData.textOutput.role;
      const messageData = { role: role, message: content };
      this.chatHistoryManager.addTextMessage(messageData);
    } else if (eventData.audioOutput) {
      if (this.currentAudioConfig && audioPlayer.initialized) {
        audioPlayer.playAudio(
          this.base64ToFloat32Array(eventData.audioOutput.content)
        );
      }
    } else if (eventData.toolUse) { // This is Nova Sonic telling us IT wants to use a tool
        console.log("TOOL USE event received from Nova Sonic (Bedrock Event):", JSON.stringify(eventData.toolUse, null, 2));
        const toolName = eventData.toolUse.name;
        const toolInput = eventData.toolUse.input;
        if (toolName === "agentSearch") {
            console.log(`Frontend observed: Nova Sonic wants to call agentSearch for query: '${toolInput.query}'`);
        } // Add other tool logs if needed
    } else if (eventData.contentEnd) {
        // ... your existing contentEnd logic ...
        if (eventData.contentEnd.type === "TEXT" && eventData.contentEnd.stopReason.toUpperCase() === "INTERRUPTED" ) {
             if (audioPlayer.initialized) audioPlayer.bargeIn();
        }
    } else if (eventData.completionEnd) {
        // console.log("Bedrock completionEnd received:", eventData.completionEnd);
    } else if (eventData.connectionStatus) {
        console.log("Connection Status from backend (Bedrock Event):", eventData.connectionStatus);
    } else if (eventData.error) { // Bedrock stream error forwarded by backend
         console.error("Error event received from backend (Bedrock Stream Error):", eventData.error);
         this.updateStatus(`Bedrock Error: ${eventData.error.message}`, "error");
         if (eventData.error.fatal) {
             this.cleanup(true, eventData.error.message);
         }
    }
     else {
      console.warn("Unknown Bedrock event type received:", Object.keys(eventData)[0]);
    }
}

handleToolCompletionNotification(payload) {
    console.log("Received toolCompletionNotification from backend:", payload);
    // Example: payload = { toolName: "agentSearch", toolUseId: "...", status: "success", message: "..." }

    let messageToDisplay = payload.message || `Tool '${payload.toolName}' has finished.`;
    if (payload.status === "error") {
        messageToDisplay = `Tool '${payload.toolName}' encountered an error: ${payload.message}`;
    }

    this.chatHistoryManager.addTextMessage({
        role: "SYSTEM", // Or a new role like "TOOL_NOTIFICATION" for distinct styling
        message: messageToDisplay,
        isNotification: true, // Add this if you want to style it differently
        status: payload.status // 'success' or 'error'
    });

    // Optional: Desktop notification
    if (typeof Notification !== 'undefined' && Notification.permission === "granted") {
        new Notification("Tool Update", { body: messageToDisplay, tag: payload.toolUseId });
    } else if (typeof Notification !== 'undefined' && Notification.permission !== "denied") {
        Notification.requestPermission().then(permission => {
            if (permission === "granted") {
                new Notification("Tool Update", { body: messageToDisplay, tag: payload.toolUseId });
            }
        });
    }
  }
}