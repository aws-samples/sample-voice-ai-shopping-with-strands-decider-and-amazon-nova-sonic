/**
 * AudioWorklet that turns microphone audio into 16 kHz mono PCM16 frames for
 * Nova Sonic. Runs on the audio thread; downsamples by averaging from the
 * context's sample rate and posts one Int16Array every ~64 ms.
 */
const TARGET_RATE = 16000;
const FRAME_SAMPLES = 1024; // 64 ms at 16 kHz, ~2 KB per frame

class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / TARGET_RATE;
    this.carry = 0;
    this.acc = 0;
    this.accCount = 0;
    this.frame = new Int16Array(FRAME_SAMPLES);
    this.frameLength = 0;
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel) return true;
    for (let i = 0; i < channel.length; i++) {
      this.acc += channel[i];
      this.accCount += 1;
      this.carry += 1;
      if (this.carry >= this.ratio) {
        this.carry -= this.ratio;
        const sample = Math.max(-1, Math.min(1, this.acc / this.accCount));
        this.frame[this.frameLength++] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
        this.acc = 0;
        this.accCount = 0;
        if (this.frameLength === FRAME_SAMPLES) {
          // Level for the mic meter, then hand the frame to the main thread.
          let peak = 0;
          for (let j = 0; j < FRAME_SAMPLES; j++) peak = Math.max(peak, Math.abs(this.frame[j]));
          this.port.postMessage({ pcm: this.frame.buffer, level: peak / 0x7fff }, [this.frame.buffer]);
          this.frame = new Int16Array(FRAME_SAMPLES);
          this.frameLength = 0;
        }
      }
    }
    return true;
  }
}

registerProcessor('pcm-capture', PcmCaptureProcessor);
