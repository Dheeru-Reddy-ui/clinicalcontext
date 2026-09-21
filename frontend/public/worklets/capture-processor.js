/**
 * Microphone capture worklet (Phase 11A.3).
 *
 * Runs on the audio rendering thread. Takes the mic's float samples at the
 * context sample rate, downsamples to 16 kHz if the context could not be
 * opened at 16 kHz, packs 20 ms frames (320 samples) as Int16 PCM, and posts
 * each frame to the main thread with its RMS level (the waveform and the
 * energy VAD use it). No ScriptProcessorNode — it is deprecated and adds
 * a full block of latency.
 */
class CaptureProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const opts = (options && options.processorOptions) || {};
    this.targetRate = opts.targetRate || 16000;
    this.frameSamples = Math.round(this.targetRate * 0.02);
    this.ratio = sampleRate / this.targetRate; // sampleRate: worklet global
    this.pending = new Float32Array(this.frameSamples);
    this.pendingLength = 0;
    this.acc = 0; // fractional read position for downsampling
    this.muted = false;
    this.port.onmessage = (event) => {
      if (event.data && event.data.type === "mute") this.muted = Boolean(event.data.muted);
    };
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || !input[0]) return true;
    const channel = input[0];
    if (this.ratio === 1) {
      for (let i = 0; i < channel.length; i += 1) this.push(channel[i]);
    } else {
      // Linear-interpolation downsample: adequate for speech, cheap enough
      // to never miss a render quantum.
      for (let pos = this.acc; pos < channel.length - 1; pos += this.ratio) {
        const index = Math.floor(pos);
        const frac = pos - index;
        this.push(channel[index] * (1 - frac) + channel[index + 1] * frac);
        this.acc = pos + this.ratio - channel.length;
      }
      if (this.acc < 0) this.acc = 0;
    }
    return true;
  }

  push(sample) {
    this.pending[this.pendingLength] = sample;
    this.pendingLength += 1;
    if (this.pendingLength < this.frameSamples) return;
    const pcm = new Int16Array(this.frameSamples);
    let energy = 0;
    for (let i = 0; i < this.frameSamples; i += 1) {
      const s = this.muted ? 0 : Math.max(-1, Math.min(1, this.pending[i]));
      pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
      energy += s * s;
    }
    const rms = Math.sqrt(energy / this.frameSamples);
    this.pendingLength = 0;
    this.port.postMessage({ type: "frame", pcm: pcm.buffer, rms, at: currentTime }, [pcm.buffer]);
  }
}

registerProcessor("capture-processor", CaptureProcessor);
