// Voice input: the Web Speech API when the browser has it, else record and send to /api/voice/transcribe.

import { api } from './api';

interface SpeechResultList {
  length: number;
  [i: number]: { isFinal: boolean; 0: { transcript: string } };
}
interface SpeechRecognitionLike {
  lang: string;
  interimResults: boolean;
  continuous: boolean;
  onresult: ((e: { resultIndex: number; results: SpeechResultList }) => void) | null;
  onerror: ((e: { error: string }) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
}
type SpeechCtor = new () => SpeechRecognitionLike;

function speechCtor(): SpeechCtor | null {
  const w = window as unknown as { SpeechRecognition?: SpeechCtor; webkitSpeechRecognition?: SpeechCtor };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export type VoiceMode = 'speech' | 'record' | 'none';

export function voiceMode(): VoiceMode {
  if (typeof window === 'undefined') return 'none';
  if (speechCtor()) return 'speech';
  if (typeof MediaRecorder !== 'undefined' && typeof navigator.mediaDevices?.getUserMedia === 'function') return 'record';
  return 'none';
}

export interface VoiceSession {
  stop(): void;
}

export interface VoiceHandlers {
  /** text so far: `final` is settled, `interim` may still change */
  onText: (final: string, interim: string) => void;
  onState: (state: 'listening' | 'transcribing' | 'idle') => void;
  onError: (message: string) => void;
}

export async function startVoice(h: VoiceHandlers): Promise<VoiceSession | null> {
  const Ctor = speechCtor();
  if (Ctor) return startSpeech(Ctor, h);
  if (voiceMode() === 'record') return startRecording(h);
  h.onError('Voice input is not available in this browser');
  return null;
}

function startSpeech(Ctor: SpeechCtor, h: VoiceHandlers): VoiceSession {
  const rec = new Ctor();
  rec.lang = navigator.language || 'en-US';
  rec.interimResults = true;
  rec.continuous = true;
  let final = '';
  rec.onresult = (e) => {
    let interim = '';
    for (let i = e.resultIndex; i < e.results.length; i++) {
      const r = e.results[i];
      if (r.isFinal) final += r[0].transcript;
      else interim += r[0].transcript;
    }
    h.onText(final, interim);
  };
  rec.onerror = (e) => {
    if (e.error !== 'aborted' && e.error !== 'no-speech') {
      h.onError(e.error === 'not-allowed' ? 'Microphone access was denied' : `Voice input failed (${e.error})`);
    }
  };
  rec.onend = () => h.onState('idle');
  rec.start();
  h.onState('listening');
  return { stop: () => rec.stop() };
}

function pickMime(): string {
  for (const t of ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg']) {
    if (MediaRecorder.isTypeSupported?.(t)) return t;
  }
  return '';
}

async function startRecording(h: VoiceHandlers): Promise<VoiceSession | null> {
  let stream: MediaStream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch {
    h.onError('Microphone access was denied');
    return null;
  }
  const mime = pickMime();
  const rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
  const chunks: Blob[] = [];
  rec.ondataavailable = (e) => {
    if (e.data.size) chunks.push(e.data);
  };
  rec.onstop = async () => {
    stream.getTracks().forEach((t) => t.stop());
    const type = rec.mimeType || mime || 'audio/webm';
    const blob = new Blob(chunks, { type });
    if (!blob.size) {
      h.onState('idle');
      return;
    }
    h.onState('transcribing');
    try {
      const ext = type.includes('mp4') ? 'm4a' : type.includes('ogg') ? 'ogg' : 'webm';
      const { text } = await api.transcribe(blob, `voice.${ext}`);
      h.onText(text ?? '', '');
    } catch (e) {
      h.onError(`Transcription failed: ${(e as Error).message}`);
    } finally {
      h.onState('idle');
    }
  };
  rec.start();
  h.onState('listening');
  return { stop: () => rec.state !== 'inactive' && rec.stop() };
}
