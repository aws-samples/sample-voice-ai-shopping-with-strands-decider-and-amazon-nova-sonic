/**
 * Microphone capture (16 kHz PCM16 via an AudioWorklet) and Sonic playback
 * (24 kHz PCM16, gap-free scheduling, flushed on barge-in).
 */
// Served as a real file from public/: an inlined data: URL is rejected by some
// browsers' audioWorklet.addModule.
const WORKLET_URL = '/pcm-capture-worklet.js';

export class MicrophoneCapture {
  private context: AudioContext | null = null;
  private stream: MediaStream | null = null;

  /** Throws a DOMException named NotAllowedError when the shopper denies the mic. */
  async start(onFrame: (pcm: ArrayBuffer) => void, onLevel: (level: number) => void): Promise<void> {
    // Create and resume the context inside the click, before any await: Safari keeps
    // a context created later suspended, and a suspended graph produces no frames.
    this.context = new AudioContext();
    const resumed = this.context.resume();
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    } catch (err) {
      void this.context.close();   // denied or no microphone: release the context
      this.context = null;
      throw err;
    }
    await resumed;
    await this.context.audioWorklet.addModule(WORKLET_URL);
    const source = this.context.createMediaStreamSource(this.stream);
    const node = new AudioWorkletNode(this.context, 'pcm-capture');
    node.port.onmessage = (event: MessageEvent<{ pcm: ArrayBuffer; level: number }>) => {
      onFrame(event.data.pcm);
      onLevel(event.data.level);
    };
    // Browsers only run nodes that lead to the output: route through a muted gain.
    const mute = this.context.createGain();
    mute.gain.value = 0;
    source.connect(node);
    node.connect(mute);
    mute.connect(this.context.destination);
  }

  stop(): void {
    this.stream?.getTracks().forEach((t) => t.stop());
    void this.context?.close();
    this.stream = null;
    this.context = null;
  }
}

export class SpeechPlayer {
  private context: AudioContext | null = null;
  private playHead = 0;
  private sources: AudioBufferSourceNode[] = [];

  /** Called inside a click or key press: browsers (Safari above all) only let a context started there play. */
  unlock(sampleRate = 24000): void {
    if (!this.context) this.context = new AudioContext({ sampleRate });
    void this.context.resume();
  }

  play(pcm16: ArrayBuffer, sampleRate = 24000): void {
    if (!this.context) this.context = new AudioContext({ sampleRate });
    const samples = new Int16Array(pcm16);
    const buffer = this.context.createBuffer(1, samples.length, sampleRate);
    const channel = buffer.getChannelData(0);
    for (let i = 0; i < samples.length; i++) channel[i] = samples[i] / 0x8000;
    const source = this.context.createBufferSource();
    source.buffer = buffer;
    source.connect(this.context.destination);
    this.playHead = Math.max(this.playHead, this.context.currentTime);
    source.start(this.playHead);
    this.playHead += buffer.duration;
    this.sources.push(source);
    source.onended = () => {
      this.sources = this.sources.filter((s) => s !== source);
    };
  }

  /** Barge-in: drop everything queued. */
  flush(): void {
    this.sources.forEach((s) => s.stop());
    this.sources = [];
    this.playHead = 0;
  }
}

export function base64FromBuffer(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = '';
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary);
}

export function bufferFromBase64(base64: string): ArrayBuffer {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes.buffer;
}
