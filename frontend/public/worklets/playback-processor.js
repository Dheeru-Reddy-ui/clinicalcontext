/**
 * Playback worklet (Phase 11A.3, 11F.3).
 *
 * A queue of Int16 PCM chunks tagged with (turn, sentence). Playback starts
 * once a small jitter buffer (2 frames = 40 ms) is in hand or 60 ms have
 * passed; a `flush` message empties the queue on the audio thread itself
 * (barge-in: nothing else is quicker), and the worklet reports when each
 * sentence's first sample plays and when its last sample is consumed so the
 * transcript chips and the waterfall are synchronized to real playback.
 */
const JITTER_SAMPLES = 640; // 2 × 20 ms at 16 kHz
const JITTER_WAIT_SECONDS = 0.06;

class PlaybackProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.queue = []; // {samples: Float32Array, turn, sentence, offset}
    this.queued = 0;
    this.primed = false;
    this.firstQueuedAt = null;
    this.current = null; // sentence being played: {turn, sentence}
    this.levelAcc = 0;
    this.levelCount = 0;
    this.port.onmessage = (event) => {
      const data = event.data || {};
      if (data.type === "chunk") {
        const int16 = new Int16Array(data.pcm);
        const samples = new Float32Array(int16.length);
        for (let i = 0; i < int16.length; i += 1) samples[i] = int16[i] / 32768;
        this.queue.push({ samples, turn: data.turn, sentence: data.sentence, offset: 0 });
        this.queued += samples.length;
        if (this.firstQueuedAt === null) this.firstQueuedAt = currentTime;
      } else if (data.type === "flush") {
        this.queue = [];
        this.queued = 0;
        this.primed = false;
        this.firstQueuedAt = null;
        const interrupted = this.current;
        this.current = null;
        this.port.postMessage({ type: "flushed", at: currentTime, interrupted });
      } else if (data.type === "end") {
        // Marks the end of a sentence with no more audio: nothing to do, the
        // last chunk's consumption already reports `ended`.
      }
    };
  }

  process(_inputs, outputs) {
    const output = outputs[0];
    if (!output || !output[0]) return true;
    const out = output[0];
    out.fill(0);
    if (!this.primed) {
      const waited = this.firstQueuedAt !== null && currentTime - this.firstQueuedAt >= JITTER_WAIT_SECONDS;
      if (this.queued >= JITTER_SAMPLES || waited) this.primed = true;
      else return true;
    }
    let written = 0;
    while (written < out.length && this.queue.length > 0) {
      const head = this.queue[0];
      if (this.current === null || this.current.turn !== head.turn || this.current.sentence !== head.sentence) {
        if (this.current !== null) {
          this.port.postMessage({ type: "ended", turn: this.current.turn, sentence: this.current.sentence, at: currentTime });
        }
        this.current = { turn: head.turn, sentence: head.sentence };
        this.port.postMessage({ type: "started", turn: head.turn, sentence: head.sentence, at: currentTime });
      }
      const take = Math.min(out.length - written, head.samples.length - head.offset);
      out.set(head.samples.subarray(head.offset, head.offset + take), written);
      head.offset += take;
      written += take;
      this.queued -= take;
      if (head.offset >= head.samples.length) this.queue.shift();
    }
    if (this.queue.length === 0 && this.current !== null && written < out.length) {
      this.port.postMessage({ type: "ended", turn: this.current.turn, sentence: this.current.sentence, at: currentTime });
      this.current = null;
      this.primed = false;
      this.firstQueuedAt = null;
    }
    let energy = 0;
    for (let i = 0; i < written; i += 1) energy += out[i] * out[i];
    this.levelAcc += energy;
    this.levelCount += out.length;
    if (this.levelCount >= 320) {
      this.port.postMessage({ type: "level", rms: Math.sqrt(this.levelAcc / this.levelCount) });
      this.levelAcc = 0;
      this.levelCount = 0;
    }
    return true;
  }
}

registerProcessor("playback-processor", PlaybackProcessor);
