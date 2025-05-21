import { WebSocketEventManager } from "./websocketEvents.js";

let wsManager;
let connectionReady = false; // Flag to track WebSocket readiness

// Get UI elements
const startButton = document.getElementById("start");
const stopButton = document.getElementById("stop");
const voiceIdSelect = document.getElementById("voiceIdSelect");

async function startStreaming() {
  console.log("startStreaming function called!"); // For debugging
  if (!voiceIdSelect) {
    console.error("Voice ID select element not found!");
    return;
  }
  const selectedVoiceId = voiceIdSelect.value;
  console.log("Selected Voice ID:", selectedVoiceId); // For debugging

  // Pass the selectedVoiceId to WebSocketEventManager
  wsManager = new WebSocketEventManager("ws://localhost:8081", selectedVoiceId);
  console.log("wsManager instantiated:", wsManager); // For debugging

  wsManager.socket.addEventListener("open", () => {
    console.log("WebSocket connection is now ready");
    connectionReady = true;
  });

  // It's good practice to also handle potential errors during WebSocket connection
  wsManager.socket.addEventListener("error", (error) => {
    console.error("WebSocket connection error in main.js:", error);
    // alert("WebSocket connection error. Please ensure the backend server is running.");
    // Re-enable controls if WebSocket connection fails before streaming starts
    startButton.disabled = false;
    stopButton.disabled = true;
    voiceIdSelect.disabled = false;
  });


  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        sampleRate: 16000,
        sampleSize: 16,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });

    const audioContext = new AudioContext({
      sampleRate: 16000,
      latencyHint: "interactive",
    });

    const source = audioContext.createMediaStreamSource(stream);
    const processor = audioContext.createScriptProcessor(1024, 1, 1); // bufferSize, inputChannels, outputChannels

    source.connect(processor);
    processor.connect(audioContext.destination); // Connect processor to destination to enable onaudioprocess

    processor.onaudioprocess = (e) => {
      const inputData = e.inputBuffer.getChannelData(0);
      const pcmData = new Int16Array(inputData.length);
      for (let i = 0; i < inputData.length; i++) {
        const s = Math.max(-1, Math.min(1, inputData[i]));
        pcmData[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
      }
      const base64data = btoa(
        String.fromCharCode.apply(null, new Uint8Array(pcmData.buffer))
      );
      if (wsManager && connectionReady) {
        wsManager.sendAudioChunk(base64data);
      }
    };

    startButton.disabled = true;
    stopButton.disabled = false;
    voiceIdSelect.disabled = true; // Disable voice selection when streaming

    window.audioCleanup = () => {
      console.log("Cleaning up audio resources...");
      if (processor) processor.disconnect();
      if (source) source.disconnect();
      if (audioContext && audioContext.state !== "closed") audioContext.close();
      stream.getTracks().forEach((track) => track.stop());
      console.log("Audio resources cleaned up.");
    };
  } catch (error) {
    console.error("Error accessing microphone or setting up audio processing:", error);
    // Re-enable controls if there's an error starting
    startButton.disabled = false;
    stopButton.disabled = true;
    if (voiceIdSelect) voiceIdSelect.disabled = false;
  }
}

// Listen for sessionEndedForUI event from wsManager to reset UI
window.addEventListener('sessionEndedForUI', (e) => {
    const isFatal = e.detail ? e.detail.isFatal : false;
    console.log("Event 'sessionEndedForUI' received. Fatal:", isFatal, "Resetting UI controls.");
    
    startButton.disabled = false;
    stopButton.disabled = true;
    voiceIdSelect.disabled = false;
    connectionReady = false; // WebSocket is closed or connection attempt failed
    
    if (wsManager) {
        wsManager = null; // Nullify to allow re-creation on next start
    }
    // Status div should have been updated by wsManager.cleanup or error handler
});

function stopStreaming() {
  console.log("stopStreaming function called in main.js (user clicked stop)");
    if (window.audioCleanup) {
      window.audioCleanup();
      window.audioCleanup = null;
    }

    if (wsManager) {
      // cleanup will set its own isProcessing to false and dispatch 'sessionEndedForUI'
      wsManager.cleanup(false, "Disconnected by user"); 
      // wsManager will be nulled by the 'sessionEndedForUI' event listener
    } else {
      // If wsManager is already null (e.g., due to an earlier error that called cleanup),
      // ensure UI is in a consistent stopped state.
      // The 'sessionEndedForUI' should ideally handle this, but as a fallback:
      console.log("stopStreaming called but wsManager was already null. Ensuring UI is reset.");
      startButton.disabled = false;
      stopButton.disabled = true;
      voiceIdSelect.disabled = false;
      connectionReady = false;
      const statusDiv = document.getElementById("status");
      if (statusDiv && !statusDiv.classList.contains("error")) {
          statusDiv.textContent = "Disconnected";
          statusDiv.className = "status disconnected";
      }
    }
}

document.addEventListener("DOMContentLoaded", () => {
  if (startButton && stopButton && voiceIdSelect) { // Ensure elements exist
    startButton.addEventListener("click", startStreaming);
    stopButton.addEventListener("click", stopStreaming);
  } else {
    console.error("One or more control elements (start, stop, voiceIdSelect) not found on DOMContentLoaded!");
  }
});

// Ensure audio context is resumed after user interaction, if wsManager and its audioContext exist
document.addEventListener(
  "click",
  () => {
    if (wsManager && wsManager.audioContext && wsManager.audioContext.state === "suspended") {
      console.log("Resuming AudioContext due to user interaction.");
      wsManager.audioContext.resume().catch(e => console.error("Error resuming AudioContext:", e));
    }
  },
  { once: true } // This listener will only run once
);