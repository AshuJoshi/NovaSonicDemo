// Audio sample buffer to minimize reallocations
class ExpandableBuffer {

    constructor() {
        // Start with one second's worth of buffered audio capacity before needing to expand
        this.buffer = new Float32Array(24000);
        this.readIndex = 0;
        this.writeIndex = 0;
        this.underflowedSamples = 0;
        this.isInitialBuffering = true;
        this.initialBufferLength = 24000;  // One second
        this.lastWriteTime = 0;
    }

    logTimeElapsedSinceLastWrite() {
        const now = Date.now();
        if (this.lastWriteTime !== 0) {
            const elapsed = now - this.lastWriteTime;
            console.log(`Elapsed time since last audio buffer write: ${elapsed} ms`);
        }
        this.lastWriteTime = now;
    }

    write(samples) {
        // this.logTimeElapsedSinceLastWrite();
        if (this.writeIndex + samples.length <= this.buffer.length) {
            // Enough space to append the new samples
        }
        else {
            // Not enough space ...
            if (samples.length <= this.readIndex) {
                // ... but we can shift samples to the beginning of the buffer
                const subarray = this.buffer.subarray(this.readIndex, this.writeIndex);
                console.log(`Shifting the audio buffer of length ${subarray.length} by ${this.readIndex}`);
                this.buffer.set(subarray);
            }
            else {
                // ... and we need to grow the buffer capacity to make room for more audio
                const newLength = (samples.length + this.writeIndex - this.readIndex) * 2;
                const newBuffer = new Float32Array(newLength);
                console.log(`Expanding the audio buffer from ${this.buffer.length} to ${newLength}`);
                newBuffer.set(this.buffer.subarray(this.readIndex, this.writeIndex));
                this.buffer = newBuffer;
            }
            this.writeIndex -= this.readIndex;
            this.readIndex = 0;
        }
        this.buffer.set(samples, this.writeIndex);
        this.writeIndex += samples.length;
        
        // The check for initialBufferLength and setting isInitialBuffering = false
        // is now handled more effectively within the read() method right before playback starts.
        // if (this.writeIndex - this.readIndex >= this.initialBufferLength) {
        //     // Filled the initial buffer length, so we can start playback with some cushion
        //     this.isInitialBuffering = false;
        //     console.log("Initial audio buffer filled");
        // }
    }

    // read(destination) {
    //     let copyLength = 0;
    //     if (!this.isInitialBuffering) {
    //         // Only start to play audio after we've built up some initial cushion
    //         copyLength = Math.min(destination.length, this.writeIndex - this.readIndex);
    //     }
    //     destination.set(this.buffer.subarray(this.readIndex, this.readIndex + copyLength));
    //     this.readIndex += copyLength;
    //     if (copyLength > 0 && this.underflowedSamples > 0) {
    //         console.log(`Detected audio buffer underflow of ${this.underflowedSamples} samples`);
    //         this.underflowedSamples = 0;
    //     }
    //     if (copyLength < destination.length) {
    //         // Not enough samples (buffer underflow). Fill the rest with silence.
    //         destination.fill(0, copyLength);
    //         this.underflowedSamples += destination.length - copyLength;
    //     }
    //     if (copyLength === 0) {
    //         // Ran out of audio, so refill the buffer to the initial length before playing more
    //         this.isInitialBuffering = true;
    //     }
    // }
    read(destination) {
        let availableSamples = this.writeIndex - this.readIndex;
        let copyLength = 0;

        if (this.isInitialBuffering) {
            // Still in initial buffering phase
            if (availableSamples >= this.initialBufferLength) {
                // Enough data has accumulated to start playback
                console.log("AudioWorklet: Initial audio buffer filled, starting playback. Available:", availableSamples);
                this.isInitialBuffering = false; // Transition out of initial buffering
                copyLength = Math.min(destination.length, availableSamples);
            } else {
                // Not enough for initial buffer yet, play silence
                copyLength = 0;
            }
        } else {
            // Already playing or ready to play (initial buffer was met)
            copyLength = Math.min(destination.length, availableSamples);
        }

        if (copyLength > 0) {
            destination.set(this.buffer.subarray(this.readIndex, this.readIndex + copyLength));
            this.readIndex += copyLength;
            // if (this.underflowedSamples > 0) {
            //     // This log indicates we previously underflowed but have now received some data
            //     console.log(`AudioWorklet: Buffer recovered. Previously underflowed by: ${this.underflowedSamples} samples.`);
            //     this.underflowedSamples = 0; // Reset counter as we are providing data
            // }
            if (this.underflowedSamples > 0) { // If we were underflowing
                console.log(`AudioWorklet: Buffer recovered (got ${copyLength} samples). Total previous underflow was: ${this.underflowedSamples} samples.`);
                this.underflowedSamples = 0; // Reset counter as we are providing data
            }
        }
        
        if (copyLength < destination.length) {
            // Not enough samples to fill the destination buffer for this block.
            // Fill the remainder of the destination with silence.
            destination.fill(0, copyLength);
            // if (!this.isInitialBuffering) { 
            //     // Only count as an "underflow" if we are supposed to be playing (i.e., not initial buffering phase)
            //     // and still couldn't provide a full block.
            //     this.underflowedSamples += (destination.length - copyLength);
            //     if(this.underflowedSamples > 0 && (this.underflowedSamples % (destination.length * 10) === 0) ){ // Log periodically if underflowing
            //         console.warn(`AudioWorklet: Buffer underflowing. Total underflow now: ${this.underflowedSamples} samples. Available in buffer: ${availableSamples - copyLength}`);
            //     }
            // }
            if (!this.isInitialBuffering) { // Only count as underflow if we *should* be playing
                const newlyUnderflowed = destination.length - copyLength;
                if (newlyUnderflowed > 0) {
                    if (this.underflowedSamples === 0) { // Log only at the start of an underflow period
                        console.warn(`AudioWorklet: Buffer underflow started. Missing ${newlyUnderflowed} samples this block. Available in buffer: ${availableSamples - copyLength}`);
                    }
                    this.underflowedSamples += newlyUnderflowed;
                }
            }
        }
        // CRITICAL CHANGE: Do NOT reset this.isInitialBuffering = true here if copyLength is 0.
        // If the buffer momentarily empties after playback has started, we output silence for that block
        // but remain ready to play immediately when new data arrives, without requiring a full re-buffer
        // of initialBufferLength. isInitialBuffering is only reset by clearBuffer().
    }

    clearBuffer() {
        this.readIndex = 0;
        this.writeIndex = 0;
        // Critical Change to for the buffer issue
        this.underflowedSamples = 0; // <<< Added THIS to reset the counter
        this.isInitialBuffering = true; // <<< Added THIS to reset to initial buffering state
        console.log("AudioWorklet: Playback buffer cleared and reset to initial buffering state.");
    }
}

class AudioPlayerProcessor extends AudioWorkletProcessor {
    constructor() {
        super();
        this.playbackBuffer = new ExpandableBuffer();
        this.port.onmessage = (event) => {
            if (event.data.type === "audio") {
                this.playbackBuffer.write(event.data.audioData);
            }
            else if (event.data.type === "initial-buffer-length") {
                // Override the current playback initial buffer length
                const newLength = event.data.bufferLength;
                this.playbackBuffer.initialBufferLength = newLength;
                console.log(`Changed initial audio buffer length to: ${newLength}`)
            }
            else if (event.data.type === "barge-in") {
                this.playbackBuffer.clearBuffer();
            }
        };
    }

    process(inputs, outputs, parameters) {
        const output = outputs[0][0]; // Assume one output with one channel
        this.playbackBuffer.read(output);
        return true; // True to continue processing
    }
}

registerProcessor("audio-player-processor", AudioPlayerProcessor);
