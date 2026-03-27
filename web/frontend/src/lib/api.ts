import { sessionsStore, segmentsStore, documentsStore } from './db';
import type { Session, Segment, Document as Doc } from './types';

// Authentication & Config Storage (localStorage for simple key/values)
export const getApiKey = () => localStorage.getItem("OPENAI_API_KEY") || "";
export const setApiKey = (key: string) => localStorage.setItem("OPENAI_API_KEY", key);

export const getAnthropicKey = () => localStorage.getItem("ANTHROPIC_API_KEY") || "";
export const setAnthropicKey = (key: string) => localStorage.setItem("ANTHROPIC_API_KEY", key);

export const getTargetEmail = () => localStorage.getItem("TARGET_EMAIL") || "";
export const setTargetEmail = (email: string) => localStorage.setItem("TARGET_EMAIL", email);

const DEFAULT_CONFIG = {
  models: {
    stt: "whisper-1",
    gpt_model: "gpt-4o-mini",
    claude_model: "claude-3-5-sonnet-20241022",
    translate_model: "gpt-4o-mini",
  },
  realtime: {
    ws_vad_type: "server_vad",
    ws_noise_reduction: "near_field"
  }
};

export const getConfig = async () => {
  const local = localStorage.getItem("APP_CONFIG");
  if (local) return JSON.parse(local);
  return DEFAULT_CONFIG;
};

export const updateConfig = async (data: any) => {
  localStorage.setItem("APP_CONFIG", JSON.stringify(data));
  return { success: true };
};

// Profiles Management
const DEFAULT_PROFILES = [
  { name: "General Meeting", description: "Standard dual language", type: "meeting", language: "ko", translate: false, source: "builtin" },
];

export const getProfiles = async () => {
  const local = localStorage.getItem("APP_PROFILES");
  if (local) return JSON.parse(local);
  return DEFAULT_PROFILES;
};

export const createProfile = async (data: any) => {
  const profiles = await getProfiles();
  profiles.push({...data, source: "mobile"});
  localStorage.setItem("APP_PROFILES", JSON.stringify(profiles));
  return { success: true };
};

export const deleteProfile = async (name: string) => {
  let profiles = await getProfiles();
  profiles = profiles.filter((p: any) => p.name !== name);
  localStorage.setItem("APP_PROFILES", JSON.stringify(profiles));
  return { success: true };
};

// Sessions (IndexedDB via localforage)
export const getSessions = async (search?: string, type?: string) => {
  const sessions: any[] = [];
  await sessionsStore.iterate((value) => {
    sessions.push(value);
  });
  
  // Sort by latest first
  let filtered = sessions.sort((a,b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
  
  if (search) {
    const sl = search.toLowerCase();
    filtered = filtered.filter(s => s.title?.toLowerCase().includes(sl) || s.type?.toLowerCase().includes(sl));
  }
  if (type) filtered = filtered.filter(s => s.type === type);
  
  return filtered;
};

export const getSession = async (id: string): Promise<{ session: Session; segments: Segment[]; documents: Doc[] }> => {
  const session = await sessionsStore.getItem<Session>(id);
  const segments = await segmentsStore.getItem<Segment[]>(id) || [];
  const documents = await documentsStore.getItem<Doc[]>(id) || [];
  if (!session) throw new Error("Session not found in local IndexedDB");
  return { session, segments, documents };
};

export const getSessionStatus = async (id: string) => {
  const session: any = await sessionsStore.getItem(id);
  return { id, status: session?.status || "error" };
};

export const deleteSession = async (id: string) => {
  await sessionsStore.removeItem(id);
  await segmentsStore.removeItem(id);
  await documentsStore.removeItem(id);
  return { success: true };
};

export const clearSessions = async () => {
  await sessionsStore.clear();
  await segmentsStore.clear();
  await documentsStore.clear();
  return { success: true };
};

// Whisper API 단일 호출
const callWhisperAPI = async (file: File | Blob, apikey: string, topic?: string, language?: string): Promise<string> => {
  const fd = new FormData();
  fd.append("file", file, (file as File).name || "audio.webm");
  fd.append("model", "whisper-1");
  if (topic) fd.append("prompt", topic);
  if (language && language !== "auto") fd.append("language", language);

  const res = await fetch("https://api.openai.com/v1/audio/transcriptions", {
    method: "POST",
    headers: { "Authorization": `Bearer ${apikey}` },
    body: fd
  });
  if (!res.ok) {
    const errText = await res.text().catch(() => res.statusText);
    throw new Error(`Whisper API failed (${res.status}): ${errText}`);
  }
  const data = await res.json();
  if (data.error) throw new Error(data.error.message);
  return data.text || "";
};

const audioBufferToWavBlob = (buffer: AudioBuffer, startSec: number, endSec: number) => {
  const numOfChan = buffer.numberOfChannels;
  const sampleRate = buffer.sampleRate;
  const startOffset = Math.floor(startSec * sampleRate);
  let endOffset = Math.floor(endSec * sampleRate);
  if (endOffset > buffer.length) endOffset = buffer.length;
  const lengthInSamples = endOffset - startOffset;
  
  const bufferToEncode = new Float32Array(lengthInSamples * numOfChan);
  for (let i = 0; i < numOfChan; i++) {
    const channelData = buffer.getChannelData(i);
    let offset = i;
    for (let j = startOffset; j < endOffset; j++) {
      bufferToEncode[offset] = channelData[j];
      offset += numOfChan;
    }
  }
  
  const dataView = new DataView(new ArrayBuffer(44 + bufferToEncode.length * 2));
  const writeString = (view: DataView, offset: number, string: string) => {
    for (let i = 0; i < string.length; i++) view.setUint8(offset + i, string.charCodeAt(i));
  };
  
  writeString(dataView, 0, 'RIFF');
  dataView.setUint32(4, 36 + bufferToEncode.length * 2, true);
  writeString(dataView, 8, 'WAVE');
  writeString(dataView, 12, 'fmt ');
  dataView.setUint32(16, 16, true);
  dataView.setUint16(20, 1, true);
  dataView.setUint16(22, numOfChan, true);
  dataView.setUint32(24, sampleRate, true);
  dataView.setUint32(28, sampleRate * numOfChan * 2, true);
  dataView.setUint16(32, numOfChan * 2, true);
  dataView.setUint16(34, 16, true);
  writeString(dataView, 36, 'data');
  dataView.setUint32(40, bufferToEncode.length * 2, true);
  
  let offset = 44;
  for (let i = 0; i < bufferToEncode.length; i++) {
    let s = Math.max(-1, Math.min(1, bufferToEncode[i]));
    dataView.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
    offset += 2;
  }
  return new Blob([dataView], { type: 'audio/wav' });
};

// 큰 파일을 청크로 분할 (WebAudio API 기반 무결점 방식)
const splitFileIntoChunks = async (file: File, chunkMinutes = 10): Promise<Blob[]> => {
  const arrayBuffer = await file.arrayBuffer();
  const audioContext = new (window.AudioContext || (window as any).webkitAudioContext)();
  const audioBuffer = await audioContext.decodeAudioData(arrayBuffer);
  
  const chunks: Blob[] = [];
  const chunkSizeSec = chunkMinutes * 60;
  for (let start = 0; start < audioBuffer.duration; start += chunkSizeSec) {
    chunks.push(audioBufferToWavBlob(audioBuffer, start, start + chunkSizeSec));
  }
  audioContext.close();
  return chunks;
};

// File Upload via Direct Whisper API (자동 청크 분할 지원)
export const uploadFile = async (formData: FormData) => {
  const file = formData.get("file") as File;
  const apikey = getApiKey();
  if (!apikey) throw new Error("OpenAI API Key is missing.");
  if (!file) throw new Error("No file provided.");

  const sessionId = crypto.randomUUID();
  const session = {
    id: sessionId,
    title: formData.get("title") || file.name,
    type: formData.get("type") || "meeting",
    topic: formData.get("topic") as string || "",
    speakers: formData.get("speakers") as string || "",
    language: formData.get("language") as string || "",
    translate: formData.get("translate") === "true",
    status: "processing",
    created_at: new Date().toISOString(),
    source: "mobile"
  };
  await sessionsStore.setItem(sessionId, session);

  // Background processing
  (async () => {
    try {
      let fullText = "";

      if (file.size <= 24 * 1024 * 1024) {
        // 24MB 이하: 단일 호출
        fullText = await callWhisperAPI(file, apikey, session.topic, session.language);
      } else {
        // 24MB 초과: 안전한 WebAudio 기반 10분 단위 청크 분할 및 WAV 변환 후 순차 처리
        const chunks = await splitFileIntoChunks(file);
        const textParts: string[] = [];
        for (let i = 0; i < chunks.length; i++) {
          const chunkFile = new File([chunks[i]], `${file.name}_part${i + 1}.wav`, { type: 'audio/wav' });
          const partText = await callWhisperAPI(chunkFile, apikey, session.topic, session.language);
          textParts.push(partText);
        }
        fullText = textParts.join("\n\n");
      }

      const segments = [{ start: 0, end: 0, text: fullText || "(No speech detected)", speaker: "Audio", translatedText: "" }];
      await segmentsStore.setItem(sessionId, segments);

      await generateSummaryForSession(sessionId);
    } catch (e: any) {
      console.error("Audio processing failed", e);
      session.status = "error";
      await sessionsStore.setItem(sessionId, session);
    }
  })();

  return { sessionId, status: "processing" };
};

// Text Input Direct Processing
export const processTextInput = async (text: string, metadata: any) => {
  const sessionId = crypto.randomUUID();
  const session = {
    id: sessionId,
    title: metadata.title || "Text Document",
    type: metadata.type || "meeting",
    topic: metadata.topic || "",
    translate: metadata.translate || false,
    language: metadata.language || "",
    status: "processing",
    created_at: new Date().toISOString(),
    source: "mobile"
  };
  
  const segments = [{ start: 0, end: 0, speaker: "Document", text, translatedText: "" }];
  await sessionsStore.setItem(sessionId, session);
  await segmentsStore.setItem(sessionId, segments);
  
  // Start background summary
  generateSummaryForSession(sessionId);
  return { sessionId, status: "processing" };
};

// WebSocket connector for OpenAI Realtime API
export function createRealtimeWS(): WebSocket {
  const apiKey = getApiKey();
  if (!apiKey) throw new Error("OpenAI API Key is missing. Please configure it in Settings.");

  const url = "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-10-01";
  const ws = new WebSocket(url, [
    "realtime",
    `openai-insecure-api-key.${apiKey}`,
    "openai-beta.realtime-v1"
  ]);

  // 모바일 WebView 연결 타임아웃 감지
  const timeout = setTimeout(() => {
    if (ws.readyState !== WebSocket.OPEN) {
      console.error("[RealtimeWS] Connection timeout (10s). readyState:", ws.readyState);
      ws.close();
    }
  }, 10000);

  ws.addEventListener('open', () => {
    clearTimeout(timeout);
    console.log("[RealtimeWS] Connected successfully");
  });

  ws.addEventListener('error', (e) => {
    clearTimeout(timeout);
    console.error("[RealtimeWS] WebSocket error:", e);
  });

  return ws;
}

export const saveCompleteSession = async (sessionData: any, segments: any[]) => {
  const sessionId = crypto.randomUUID();
  const session = {
    id: sessionId,
    title: sessionData.title || "Realtime Session",
    type: sessionData.type || "meeting",
    topic: sessionData.topic || "",
    status: "processing", // initial status
    created_at: new Date().toISOString(),
    source: "mobile",
    duration_sec: sessionData.duration || 0,
    translate: sessionData.translate || false
  };
  await sessionsStore.setItem(sessionId, session);
  await segmentsStore.setItem(sessionId, segments);
  return sessionId;
};

// ChatGPT API Call with Fallback Logic
const callOpenAIWithFallback = async (prompt: string, apikey: string, primaryModel: string, fallbackModel = "gpt-4o-mini") => {
   const doFetch = async (model: string) => {
     const res = await fetch("https://api.openai.com/v1/chat/completions", {
        method: "POST",
        headers: { "Content-Type": "application/json", "Authorization": `Bearer ${apikey}` },
        body: JSON.stringify({ model, messages: [{ role: "user", content: prompt }] })
     });
     if (!res.ok) {
       const errText = await res.text().catch(() => res.statusText);
       throw new Error(`OpenAI API failed (${res.status}): ${errText}`);
     }
     const data = await res.json();
     if (data.error) throw new Error(data.error.message || JSON.stringify(data.error));
     return data.choices[0].message.content;
   };

   try {
     return await doFetch(primaryModel);
   } catch (e: any) {
     console.warn(`[API] Primary model ${primaryModel} failed: ${e.message}. Trying fallback ${fallbackModel}...`);
     if (primaryModel !== fallbackModel) {
       return await doFetch(fallbackModel);
     }
     throw e;
   }
};

// Client-side Session Document Generation
export const generateSummaryForSession = async (sessionId: string, userNotes?: string) => {
   const session: any = await sessionsStore.getItem(sessionId);
   const segments: any = await segmentsStore.getItem(sessionId) || [];
   if (!session || segments.length === 0) return;

   try {
     session.status = "processing";
     await sessionsStore.setItem(sessionId, session);

     const config = await getConfig();
     const apikey = getApiKey();
     if (!apikey) throw new Error("No API Key");
     
     const text = segments.map((s:any) => `[${s.speaker || 'Speaker'}] ${s.text} ${s.translatedText ? '(' + s.translatedText + ')' : ''}`).join('\n');
     
     let noteContext = session.topic ? `\n\nContext/Topic:\n${session.topic}` : "";
     if (session.speakers) noteContext += `\nParticipants: ${session.speakers}`;
     if (userNotes) noteContext += `\n\nUser Notes:\n${userNotes}`;
     
     const prompt1 = `You are an expert meeting assistant. Summarize the following transcript in Korean. Use markdown formatting with clear headings like "세션 요약" and "주요 내용".${noteContext}\n\nTranscript:\n${text}`;
     const minutesContent = await callOpenAIWithFallback(prompt1, apikey, config.models?.gpt_model || "gpt-4o-mini");
     
     const prompt2 = `Extract action items from the following meeting transcript in Korean. Format as a markdown list with checkboxes.${noteContext}\n\nTranscript:\n${text}`;
     const actionsContent = await callOpenAIWithFallback(prompt2, apikey, config.models?.gpt_model || "gpt-4o-mini");

     const documents = [
       { type: "minutes", content: minutesContent, format: "md" },
       { type: "actions", content: actionsContent, format: "md" },
       { type: "summary", content: minutesContent, format: "md" } // duplicate for summary tab fallback
     ];
     await documentsStore.setItem(sessionId, documents);
     
     session.status = "completed";
     await sessionsStore.setItem(sessionId, session);
   } catch (e) {
     console.error("Summary generation failed", e);
     session.status = "error";
     await sessionsStore.setItem(sessionId, session);
   }
};
