/* Web Audio piano.
 *
 * Prefers the sampled Salamander piano in /static/audio (fetched by
 * scripts/fetch_samples.py). If those files aren't there, it falls back to a
 * synthesised tone so playback always works -- the Pi may well be offline, and
 * an app that silently refuses to play would be useless.
 */
(function () {
  'use strict';

  const SAMPLE_WAIT_MS = 12000;  // give up waiting and use the synth rather than hang
  const DAMPER_RELEASE = 0.28;   // seconds for the damper to stop a string
  const SYNTH_RELEASE = 0.22;
  const MAX_VOICES = 64;

  class PianoEngine {
    constructor() {
      this.ctx = null;
      this.master = null;
      this.buffers = new Map();   // midi note -> AudioBuffer
      this.sampled = false;
      this.voices = new Set();
      this._loading = null;
      this._prefetching = null;
      this._useSamples = false;
      this.samplesUnavailable = false;
    }

    /** True once the sampled piano is decoded and ready to play. */
    get samplesReady() {
      return this.sampled;
    }

    /* Download the sample bytes.
     *
     * Deliberately separate from decoding: fetching needs no AudioContext, so it
     * can start at page load, while decoding must wait for the user gesture that
     * opens audio. By the time play is pressed the bytes are usually already
     * here, so waiting for the real piano costs a decode rather than a download.
     */
    prefetch() {
      if (this._prefetching) return this._prefetching;
      this._prefetching = (async () => {
        let manifest;
        try {
          const res = await fetch('/static/audio/manifest.json');
          if (!res.ok) throw new Error('no manifest');
          manifest = await res.json();
        } catch (_) {
          this.samplesUnavailable = true;
          return null;                       // samples were never installed
        }
        const raw = new Map();
        await Promise.all(Object.entries(manifest.samples || {}).map(
          async ([midi, file]) => {
            try {
              const res = await fetch('/static/audio/' + file);
              if (!res.ok) return;
              raw.set(Number(midi), await res.arrayBuffer());
            } catch (_) { /* one missing sample just widens the pitch-shift gap */ }
          }));
        if (!raw.size) this.samplesUnavailable = true;
        return raw;
      })();
      return this._prefetching;
    }

    /** True until the sample set has been fetched and decoded. */
    get loadPending() {
      return this._loading === null || !this.sampled;
    }

    /* Start audio from inside a user gesture.
     *
     * iOS is strict in three ways that desktop browsers are not, and all three
     * have to be handled here or playback is simply silent on an iPhone:
     *
     *  1. The context only really starts if something is played synchronously
     *     within the gesture, so a one-sample silent buffer is fired before any
     *     `await` can yield control.
     *  2. It suspends contexts that go quiet or get backgrounded, so state is
     *     rechecked before every playback rather than only on the first.
     *  3. It will re-suspend a context that produces nothing for seconds, which
     *     is exactly what waiting on ~2 MB of samples used to do. Sample loading
     *     is therefore kicked off here but deliberately NOT awaited.
     */
    async unlock() {
      if (!this.ctx) {
        const Ctx = window.AudioContext || window.webkitAudioContext;
        if (!Ctx) throw new Error('This browser has no Web Audio support');
        this.ctx = new Ctx();
        this.master = this.ctx.createGain();
        this.master.gain.value = 0.85;
        this.master.connect(this.ctx.destination);
      }

      this._nudge();                                   // must precede any await
      const resuming = this.ctx.resume ? this.ctx.resume() : Promise.resolve();
      this.prefetch();                                 // no-op if already running
      try { await resuming; } catch (_) { /* already running */ }

      if (this.ctx.state !== 'running') {
        throw new Error('Audio is blocked. On iPhone, check the silent switch.');
      }

      // Wait for the real piano. The synth is a fallback for when the samples
      // are genuinely absent, not something to settle for because decoding had
      // not finished yet. The cap only stops a stalled network wedging playback.
      if (!this._loading) this._loading = this._loadSamples();
      await Promise.race([
        this._loading,
        new Promise((resolve) => setTimeout(resolve, SAMPLE_WAIT_MS)),
      ]);
      return true;
    }

    /** A one-sample silent buffer: the canonical way to open iOS audio. */
    _nudge() {
      try {
        const source = this.ctx.createBufferSource();
        source.buffer = this.ctx.createBuffer(1, 1, this.ctx.sampleRate);
        source.connect(this.ctx.destination);
        source.start(0);
      } catch (_) { /* not fatal; the resume below may still be enough */ }
    }

    /** Called as playback starts, so the timbre cannot change mid-phrase. */
    beginPlayback() {
      this._useSamples = this.sampled;
      return this._useSamples;
    }

    async _loadSamples() {
      const raw = await this.prefetch();
      if (!raw || !raw.size) {
        this.sampled = false;
        return false;                        // genuine synth fallback
      }
      await Promise.all([...raw.entries()].map(async ([midi, bytes]) => {
        try {
          // decodeAudioData detaches the buffer, so hand it a copy and keep the
          // original in case a decode has to be retried.
          this.buffers.set(midi, await this._decode(bytes.slice(0)));
        } catch (_) { /* skip this pitch; neighbours cover it */ }
      }));
      this.sampled = this.buffers.size > 0;
      return this.sampled;
    }

    /** Safari only gained promise-based decodeAudioData late; support both. */
    _decode(bytes) {
      return new Promise((resolve, reject) => {
        const result = this.ctx.decodeAudioData(bytes, resolve, reject);
        if (result && typeof result.then === 'function') result.then(resolve, reject);
      });
    }

    _nearestSample(midi) {
      let best = null, distance = Infinity;
      for (const sampled of this.buffers.keys()) {
        const d = Math.abs(sampled - midi);
        if (d < distance) { distance = d; best = sampled; }
      }
      return best;
    }

    /** Schedule one note. `when` and `duration` are in AudioContext seconds. */
    play(midi, velocity, when, duration) {
      if (!this.ctx) return;
      if (this.voices.size > MAX_VOICES) return;   // protect a small Pi-side CPU

      const level = Math.pow(Math.max(1, velocity) / 127, 1.6) * 0.9;
      // Fixed when playback began: if the samples arrive mid-phrase, finishing
      // on the synth is less jarring than switching instruments halfway.
      const node = this._useSamples
        ? this._playSampled(midi, level, when, duration)
        : this._playSynth(midi, level, when, duration);

      if (node) {
        this.voices.add(node);
        node.onended = () => this.voices.delete(node);
      }
    }

    _playSampled(midi, level, when, duration) {
      const sampleMidi = this._nearestSample(midi);
      if (sampleMidi == null) return null;

      const source = this.ctx.createBufferSource();
      source.buffer = this.buffers.get(sampleMidi);
      source.playbackRate.value = Math.pow(2, (midi - sampleMidi) / 12);

      const gain = this.ctx.createGain();
      gain.gain.setValueAtTime(level, when);

      // Let the sample ring for the held duration, then drop the damper.
      const releaseAt = when + Math.max(0.05, duration);
      gain.gain.setValueAtTime(level, releaseAt);
      gain.gain.exponentialRampToValueAtTime(0.0001, releaseAt + DAMPER_RELEASE);

      source.connect(gain).connect(this.master);
      source.start(when);
      source.stop(releaseAt + DAMPER_RELEASE + 0.02);
      return source;
    }

    _playSynth(midi, level, when, duration) {
      const freq = 440 * Math.pow(2, (midi - 69) / 12);
      const gain = this.ctx.createGain();
      gain.connect(this.master);

      // A struck-string-ish timbre: a few inharmonic partials that decay at
      // different rates, brighter the harder you hit it.
      const partials = [
        { ratio: 1,    gain: 1.0,  decay: 1.0 },
        { ratio: 2.01, gain: 0.42, decay: 0.62 },
        { ratio: 3.02, gain: 0.18, decay: 0.42 },
        { ratio: 4.05, gain: 0.08, decay: 0.28 },
      ];
      const hold = Math.max(0.08, duration);
      const end = when + hold + SYNTH_RELEASE;

      const nodes = [];
      for (const p of partials) {
        if (freq * p.ratio > 16000) continue;
        const osc = this.ctx.createOscillator();
        osc.type = 'sine';
        osc.frequency.value = freq * p.ratio;

        const pg = this.ctx.createGain();
        const peak = level * p.gain * (0.5 + level * 0.5);
        pg.gain.setValueAtTime(0.0001, when);
        pg.gain.exponentialRampToValueAtTime(Math.max(0.0002, peak), when + 0.006);
        pg.gain.exponentialRampToValueAtTime(
          Math.max(0.0001, peak * 0.25), when + 0.25 * p.decay + 0.05);
        pg.gain.setValueAtTime(
          Math.max(0.0001, peak * 0.18), Math.max(when + 0.01, when + hold));
        pg.gain.exponentialRampToValueAtTime(0.0001, end);

        osc.connect(pg).connect(gain);
        osc.start(when);
        osc.stop(end + 0.01);
        nodes.push(osc);
      }
      return nodes[0] || null;
    }

    /** Cut everything immediately -- used on pause, seek and navigation. */
    panic() {
      if (!this.ctx) return;
      for (const node of this.voices) {
        try { node.stop(); } catch (_) {}
      }
      this.voices.clear();
    }

    get time() { return this.ctx ? this.ctx.currentTime : 0; }
  }

  window.PianoEngine = PianoEngine;
})();
