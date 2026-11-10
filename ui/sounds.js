/* Meeting Transcriber — UI sounds, synthesized with the Web Audio API.
   No audio files: every sound is generated, so it works offline and stays tiny.

   Sounds.play(name)   one-shot: welcome, ready, notready, start, stop, done, warning, error
   Sounds.loop(true)   soft continuous pad while a file is transcribing; loop(false) fades it out
   Sounds.configure({volume: 0..100, muted: bool})                                       */
"use strict";

const Sounds = (() => {
  let ctx = null, master = null, pad = null;
  let volume = 0.7, muted = false;

  function audio() {
    if (!ctx) {
      ctx = new (window.AudioContext || window.webkitAudioContext)();
      master = ctx.createGain();
      // gentle high cut so nothing sounds harsh through laptop speakers
      const lp = ctx.createBiquadFilter();
      lp.type = "lowpass";
      lp.frequency.value = 6000;
      master.connect(lp).connect(ctx.destination);
      applyGain();
    }
    if (ctx.state === "suspended") ctx.resume();
    return ctx;
  }
  function level() { return muted ? 0 : Math.pow(volume, 2) * 0.55; }   // perceptual curve
  function applyGain() {
    if (master) master.gain.setTargetAtTime(level(), ctx.currentTime, 0.03);
  }

  /* One enveloped note. t = start offset (s), d = length (s). */
  function note(freq, t, d, { type = "sine", gain = 0.5, to = null, attack = 0.008 } = {}) {
    const c = audio(), now = c.currentTime + t;
    const o = c.createOscillator(), g = c.createGain();
    o.type = type;
    o.frequency.setValueAtTime(freq, now);
    if (to) o.frequency.exponentialRampToValueAtTime(to, now + d * 0.85);
    g.gain.setValueAtTime(0.0001, now);
    g.gain.exponentialRampToValueAtTime(gain, now + attack);
    g.gain.exponentialRampToValueAtTime(0.0001, now + d);
    o.connect(g).connect(master);
    o.start(now);
    o.stop(now + d + 0.05);
  }
  /* A soft "bell": sine plus a quiet octave partial. */
  function bell(freq, t, d, gain = 0.4) {
    note(freq, t, d, { gain });
    note(freq * 2, t, d * 0.6, { gain: gain * 0.18 });
  }

  const N = { A3: 220, E4: 329.6, A4: 440, B4: 493.9, C5: 523.3, E5: 659.3,
              G5: 784, A5: 880, B5: 987.8, C6: 1046.5, E6: 1318.5 };

  const library = {
    // app opened: a warm rising arpeggio
    welcome: () => { [N.C5, N.E5, N.G5, N.C6].forEach((f, i) => bell(f, i * 0.11, 1.1 - i * 0.12, 0.32)); return 900; },
    // model loaded: bright two-note lift
    ready: () => { bell(N.E5, 0, 0.5, 0.35); bell(N.B5, 0.12, 0.8, 0.35); return 650; },
    // model still loading: soft falling pair
    notready: () => { note(N.A4, 0, 0.18, { type: "triangle", gain: 0.3 }); note(N.E4, 0.13, 0.32, { type: "triangle", gain: 0.3 }); return 450; },
    // recording on: quick upward sweep + tick
    start: () => { note(440, 0, 0.22, { to: 880, gain: 0.35 }); bell(N.A5, 0.16, 0.35, 0.25); return 450; },
    // recording off: downward sweep
    stop: () => { bell(N.A5, 0, 0.18, 0.22); note(880, 0.08, 0.3, { to: 440, gain: 0.32 }); return 420; },
    // file finished
    done: () => { [N.C5, N.E5, N.G5].forEach((f, i) => bell(f, i * 0.09, 0.55, 0.3)); bell(N.C6, 0.27, 0.9, 0.3); return 1100; },
    // something needs attention but nothing broke
    warning: () => { note(N.B4, 0, 0.16, { type: "triangle", gain: 0.32 }); note(N.B4, 0.2, 0.22, { type: "triangle", gain: 0.32 }); return 450; },
    // something broke: low, rough double buzz
    error: () => {
      note(N.A3, 0, 0.2, { type: "square", gain: 0.14 }); note(N.A3 * 1.01, 0, 0.2, { type: "sawtooth", gain: 0.08 });
      note(196, 0.24, 0.32, { type: "square", gain: 0.14 }); note(196 * 1.01, 0.24, 0.32, { type: "sawtooth", gain: 0.08 });
      return 600;
    },
  };

  /* Returns a promise that resolves when the sound has finished
     (immediately when muted) so callers can wait before recording. */
  function play(name) {
    if (muted || volume <= 0 || !library[name]) return Promise.resolve();
    try {
      const ms = library[name]();
      return new Promise(r => setTimeout(r, ms));
    } catch (e) {
      console.warn("sound failed", e);
      return Promise.resolve();
    }
  }

  /* Continuous, quiet "working" pad: two detuned sines breathing slowly. */
  function loop(on) {
    if (on && !pad) {
      const c = audio(), now = c.currentTime;
      const out = c.createGain();
      out.gain.setValueAtTime(0.0001, now);
      out.gain.exponentialRampToValueAtTime(0.09, now + 1.2);
      const lfo = c.createOscillator(), depth = c.createGain();
      lfo.frequency.value = 0.35;          // one slow breath every ~3 s
      depth.gain.value = 0.045;
      lfo.connect(depth).connect(out.gain);
      const oscs = [[N.A3, 0], [N.E4, 3], [N.A4, -4]].map(([f, cents]) => {
        const o = c.createOscillator();
        o.type = "sine";
        o.frequency.value = f;
        o.detune.value = cents;
        o.connect(out);
        o.start();
        return o;
      });
      lfo.start();
      out.connect(master);
      pad = { out, oscs: [...oscs, lfo] };
    } else if (!on && pad) {
      const p = pad, now = ctx.currentTime;
      pad = null;
      p.out.gain.cancelScheduledValues(now);
      p.out.gain.setTargetAtTime(0.0001, now, 0.25);
      p.oscs.forEach(o => o.stop(now + 1.2));
    }
  }

  function configure(opts) {
    if (opts.volume != null) volume = Math.max(0, Math.min(100, +opts.volume)) / 100;
    if (opts.muted != null) muted = !!opts.muted;
    applyGain();
  }

  return { play, loop, configure, get muted() { return muted; } };
})();
