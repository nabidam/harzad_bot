// The 30s preview audio (v1: one Audio element, global). Kept outside
// React — it outlives re-renders; React mirrors it as (track, playing).
let audio: HTMLAudioElement | null = null;
let endedCb: (() => void) | null = null;

export function onEnded(cb: () => void): void {
  endedCb = cb;
}

export function previewAudio(): HTMLAudioElement | null {
  return audio;
}

export function startPreview(url: string): Promise<void> {
  if (audio) {
    audio.pause();
    audio = null;
  }
  audio = new Audio(url);
  audio.onended = () => {
    const cb = endedCb;
    audio = null;
    if (cb) cb();
  };
  return audio.play();
}

export function pausePreview(): void {
  if (audio) audio.pause();
}

export function resumePreview(): Promise<void> {
  if (audio) return audio.play();
  return Promise.resolve();
}

export function stopPreview(): void {
  if (audio) {
    audio.pause();
    audio = null;
  }
}
