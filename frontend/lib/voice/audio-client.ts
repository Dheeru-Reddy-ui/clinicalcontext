/**
 * Browser audio for voice mode (11A.2, 11A.3, 11F.3, 11F.4).
 *
 * One AudioContext at 16 kHz carries both directions: the microphone
 * (browser AEC/NS/AGC on — the echo cancellation is what makes barge-in over
 * speakers possible) through the capture worklet, and TTS audio through the
 * playback worklet with a 2–3 frame jitter buffer and an instant flush.
 *
 * Every measurement the session reports — the client-side barge-in stop
 * latency, the first-audio playback time — comes from timestamps taken here.
 */

import { describeMicError } from "@/components/voice/mic-check";

export interface CapturedFrame {
  pcm: ArrayBuffer;
  rms: number;
  /** performance.now() when the frame reached the main thread. */
  at: number;
}

export interface PlaybackMilestone {
  turn: number;
  sentence: number;
  at: number;
}

export interface VoiceAudioHandlers {
  onFrame: (frame: CapturedFrame) => void;
  onPlaybackStarted: (milestone: PlaybackMilestone) => void;
  onPlaybackEnded: (milestone: PlaybackMilestone) => void;
  onAgentLevel: (rms: number) => void;
}

export class VoiceAudio {
  private context: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private capture: AudioWorkletNode | null = null;
  private playback: AudioWorkletNode | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private earconTimer: number | null = null;
  readonly sampleRate = 16_000;
  echoCancellation = false;

  constructor(private readonly handlers: VoiceAudioHandlers) {}

  /** True when a microphone stream is live; false in listen-only mode. */
  micAvailable = false;
  micError: string | null = null;

  /** `deviceId`: the microphone chosen in the mic check, if any. */
  async start(deviceId?: string): Promise<void> {
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          // `ideal`, not `exact`: a remembered device that has since been
          // unplugged falls back to the default instead of failing.
          deviceId: deviceId ? { ideal: deviceId } : undefined,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          channelCount: 1,
          sampleRate: this.sampleRate,
        },
        video: false,
      });
      this.micAvailable = true;
    } catch (error) {
      // No microphone or permission denied: voice mode still plays answers
      // to typed turns, and says so, rather than refusing to open.
      this.stream = null;
      this.micAvailable = false;
      this.micError = describeMicError(error);
    }
    const track = this.stream?.getAudioTracks()[0];
    const settings = track?.getSettings();
    this.echoCancellation = Boolean(settings?.echoCancellation);
    // Some browsers refuse a 16 kHz context; the capture worklet resamples.
    let context: AudioContext;
    try {
      context = new AudioContext({ sampleRate: this.sampleRate, latencyHint: "interactive" });
    } catch {
      context = new AudioContext({ latencyHint: "interactive" });
    }
    this.context = context;
    await context.audioWorklet.addModule("/worklets/capture-processor.js");
    await context.audioWorklet.addModule("/worklets/playback-processor.js");
    if (this.stream) {
      this.source = context.createMediaStreamSource(this.stream);
      this.capture = new AudioWorkletNode(context, "capture-processor", {
        numberOfInputs: 1,
        numberOfOutputs: 0,
        processorOptions: { targetRate: this.sampleRate },
      });
      this.capture.port.onmessage = (event: MessageEvent<{ type: string; pcm: ArrayBuffer; rms: number }>) => {
        if (event.data.type === "frame") {
          this.handlers.onFrame({ pcm: event.data.pcm, rms: event.data.rms, at: performance.now() });
        }
      };
      this.source.connect(this.capture);
    }
    this.playback = new AudioWorkletNode(context, "playback-processor", {
      numberOfInputs: 0,
      numberOfOutputs: 1,
      outputChannelCount: [1],
    });
    this.playback.port.onmessage = (
      event: MessageEvent<{ type: string; turn?: number; sentence?: number; rms?: number }>,
    ) => {
      const data = event.data;
      if (data.type === "started" && data.turn !== undefined && data.sentence !== undefined) {
        this.handlers.onPlaybackStarted({ turn: data.turn, sentence: data.sentence, at: performance.now() });
      } else if (data.type === "ended" && data.turn !== undefined && data.sentence !== undefined) {
        this.handlers.onPlaybackEnded({ turn: data.turn, sentence: data.sentence, at: performance.now() });
      } else if (data.type === "level" && typeof data.rms === "number") {
        this.handlers.onAgentLevel(data.rms);
      }
    };
    this.playback.connect(context.destination);
    if (context.state !== "running") await context.resume();
  }

  /** Playback audio must be resampled by the caller if the server rate differs. */
  enqueue(turn: number, sentence: number, pcm: ArrayBuffer): void {
    this.playback?.port.postMessage({ type: "chunk", turn, sentence, pcm }, [pcm]);
  }

  /** Barge-in: drop everything queued. Returns the time the flush was posted. */
  flush(): number {
    this.playback?.port.postMessage({ type: "flush" });
    return performance.now();
  }

  setMuted(muted: boolean): void {
    this.capture?.port.postMessage({ type: "mute", muted });
  }

  async resume(): Promise<void> {
    if (this.context && this.context.state !== "running") await this.context.resume();
  }

  /** A subtle "thinking" tick — an earcon, never fake verbal filler (11F.4). */
  earcon(): void {
    const context = this.context;
    if (!context) return;
    const now = context.currentTime;
    const osc = context.createOscillator();
    const gain = context.createGain();
    osc.type = "sine";
    osc.frequency.setValueAtTime(660, now);
    osc.frequency.exponentialRampToValueAtTime(880, now + 0.09);
    gain.gain.setValueAtTime(0.0001, now);
    gain.gain.exponentialRampToValueAtTime(0.05, now + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.12);
    osc.connect(gain).connect(context.destination);
    osc.start(now);
    osc.stop(now + 0.13);
  }

  startEarcons(intervalMs = 800): void {
    this.stopEarcons();
    this.earcon();
    this.earconTimer = window.setInterval(() => this.earcon(), intervalMs);
  }

  stopEarcons(): void {
    if (this.earconTimer !== null) {
      window.clearInterval(this.earconTimer);
      this.earconTimer = null;
    }
  }

  async close(): Promise<void> {
    this.stopEarcons();
    this.capture?.disconnect();
    this.playback?.disconnect();
    this.source?.disconnect();
    this.stream?.getTracks().forEach((track) => track.stop());
    if (this.context) await this.context.close();
    this.context = null;
    this.stream = null;
    this.capture = null;
    this.playback = null;
    this.source = null;
  }
}

export const BARGE_IN_LEVEL_FACTOR = 0.7;

/**
 * The same adaptive-floor energy VAD the server runs, in the browser: the
 * barge-in decision must be local (nothing over the network is ≤150 ms).
 */
export class EnergyVad {
  private floor = 0.004;
  private run = 0;
  inSpeech = false;

  constructor(
    private readonly ratio = 3.5,
    private readonly absoluteMin = 0.008,
    private readonly onsetFrames = 2,
  ) {}

  get level(): number {
    return this.floor;
  }

  threshold(userLevel: number | null): number {
    const base = Math.max(this.floor * this.ratio, this.absoluteMin);
    // Backchannels are murmured: during playback a frame must also clear
    // BARGE_IN_LEVEL_FACTOR of the user's own median speaking level to count
    // as an interruption (the eval harness applies the same rule).
    return userLevel === null ? base : Math.max(base, BARGE_IN_LEVEL_FACTOR * userLevel);
  }

  process(rms: number, userLevel: number | null = null): boolean {
    const loud = rms > this.threshold(userLevel);
    if (loud) {
      this.floor = Math.min(this.floor * 1.002 + 1e-6, rms / this.ratio);
      this.run += 1;
      if (this.run >= this.onsetFrames) this.inSpeech = true;
    } else {
      this.floor = rms < this.floor ? 0.8 * this.floor + 0.2 * rms : 0.95 * this.floor + 0.05 * rms;
      this.run = 0;
      this.inSpeech = false;
    }
    return this.inSpeech;
  }

  reset(): void {
    this.run = 0;
    this.inSpeech = false;
  }
}
