/**
 * PCM Audio Worklet Processor
 *
 * Runs in the AudioWorklet thread (isolated from the main UI thread).
 * Receives raw PCM float32 samples from the microphone, converts them to
 * Int16, and posts them back to the main thread via MessagePort.
 *
 * Why AudioWorkletNode instead of ScriptProcessorNode?
 * - ScriptProcessorNode runs on the main thread (blocks UI)
 * - AudioWorkletNode runs on a dedicated audio rendering thread
 * - ScriptProcessorNode is deprecated in all modern browser specs
 *
 * Message protocol (worklet → main thread):
 *   { type: "pcm-chunk", buffer: Int16Array }
 *
 * Control messages (main thread → worklet):
 *   { type: "mute" }    — stop sending chunks
 *   { type: "unmute" }  — resume sending chunks
 */

class PCMProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._muted = false;
    // Accumulate samples until we have a full frame to send.
    // 2048 samples @ 16kHz ≈ 128ms per chunk — smaller for lower VAD latency.
    this._bufferSize = 2048;
    this._buffer = new Float32Array(this._bufferSize);
    this._bufferFill = 0;

    this.port.onmessage = (event) => {
      if (event.data?.type === "mute") this._muted = true;
      if (event.data?.type === "unmute") this._muted = false;
    };
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || !input[0]) return true; // keep processor alive

    const channelData = input[0]; // mono channel

    for (let i = 0; i < channelData.length; i++) {
      this._buffer[this._bufferFill++] = channelData[i];

      if (this._bufferFill >= this._bufferSize) {
        if (!this._muted) {
          // Convert Float32 → Int16
          const int16 = new Int16Array(this._bufferSize);
          for (let j = 0; j < this._bufferSize; j++) {
            const s = Math.max(-1, Math.min(1, this._buffer[j]));
            int16[j] = s < 0 ? s * 0x8000 : s * 0x7fff;
          }
          // Transfer ownership of the buffer (zero-copy)
          this.port.postMessage({ type: "pcm-chunk", buffer: int16 }, [
            int16.buffer,
          ]);
        }
        this._bufferFill = 0;
      }
    }

    return true; // returning false would remove the processor
  }
}

registerProcessor("pcm-processor", PCMProcessor);
