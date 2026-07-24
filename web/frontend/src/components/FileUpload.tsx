import React, { useState, useRef } from "react";
import {
  Upload, FileAudio, Loader2, ChevronDown,
} from "lucide-react";
import { motion, AnimatePresence } from "motion/react";
import { uploadFile, getProfiles } from "../lib/api";
import { MODE_PRESETS } from "../lib/types";
import type { Profile } from "../lib/types";
import ModeSelector from "./ModeSelector";

export default function FileUpload({ onComplete }: { onComplete: (id: string) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [topic, setTopic] = useState("");
  const [speakers, setSpeakers] = useState("");
  const [modeNum, setModeNum] = useState(2);
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [showProfiles, setShowProfiles] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const preset = MODE_PRESETS[modeNum] || MODE_PRESETS[2];

  React.useEffect(() => {
    getProfiles().then(setProfiles).catch(() => {});
  }, []);

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const f = e.dataTransfer.files[0];
    if (f) setFile(f);
  };

  const handleSubmit = async () => {
    if (!file) return;
    setUploading(true);

    const formData = new FormData();
    formData.append("file", file);
    formData.append("title", title || file.name);
    formData.append("topic", topic);
    formData.append("type", preset.type);
    formData.append("language", preset.language);
    formData.append("translate", preset.translate.toString());
    formData.append("speakers", speakers);
    formData.append("mode", modeNum.toString());

    try {
      const data = await uploadFile(formData);
      onComplete(data.sessionId);
    } catch (err) {
      console.error(err);
      alert(`업로드 실패: ${err instanceof Error ? err.message : "콘솔에서 상세 내용을 확인하세요."}`);
      setUploading(false);
    }
  };

  const formatSize = (bytes: number) => {
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  return (
    <div className="max-w-4xl mx-auto px-1 md:px-0">
      <h2 className="text-2xl font-bold tracking-tight mb-1">파일 업로드</h2>
      <p className="text-brand-500 mb-4 text-sm">오디오/영상 파일을 올리면 전사·번역·회의록을 자동 생성합니다.</p>

      <div className="bg-white border border-zinc-200 rounded-2xl shadow-sm p-4 md:p-5 flex flex-col gap-4">
        {/* Drop Zone */}
        <div
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={handleDrop}
          onClick={() => fileRef.current?.click()}
          className={`border-2 border-dashed rounded-xl p-5 md:p-7 text-center cursor-pointer transition-all ${
            dragOver ? "border-brand-900 bg-brand-50" : file ? "border-emerald-300 bg-emerald-50/30" : "border-brand-200 hover:border-brand-400 bg-zinc-50/50 hover:bg-zinc-50"
          }`}
        >
          <input
            ref={fileRef}
            type="file"
            accept="audio/*,video/*,.mp3,.wav,.mp4,.webm,.m4a,.ogg,.flac,.avi,.mkv,.mov"
            className="hidden"
            onChange={(e) => e.target.files?.[0] && setFile(e.target.files[0])}
          />
          {file ? (
            <div className="flex flex-col md:flex-row items-center justify-center gap-4 md:gap-6">
              <div className="w-16 h-16 bg-emerald-100 text-emerald-600 rounded-full flex items-center justify-center shrink-0">
                <FileAudio size={32} />
              </div>
              <div className="text-center md:text-left">
                <p className="font-bold text-brand-900 line-clamp-1">{file.name}</p>
                <p className="text-sm text-brand-500">{formatSize(file.size)}</p>
              </div>
              <button
                onClick={(e) => { e.stopPropagation(); setFile(null); }}
                className="mt-2 md:mt-0 px-4 py-2 bg-white border border-red-200 text-sm text-red-500 hover:text-red-700 hover:bg-red-50 rounded-xl font-semibold md:ml-4 shadow-sm transition-colors"
              >
                파일 변경
              </button>
            </div>
          ) : (
            <div className="flex flex-col items-center">
              <div className="w-14 h-14 bg-brand-100 text-brand-600 rounded-full flex items-center justify-center mb-3 shadow-sm">
                <Upload className="w-7 h-7" />
              </div>
              <h3 className="text-base font-bold text-brand-900 mb-1">클릭해서 파일 선택</h3>
              <p className="text-sm text-brand-500">또는 여기로 끌어다 놓기</p>
              <div className="flex flex-wrap items-center justify-center gap-2 mt-3 text-xs font-semibold text-brand-400">
                <span className="bg-white px-2.5 py-1 rounded-md border border-brand-100">MP3</span>
                <span className="bg-white px-2.5 py-1 rounded-md border border-brand-100">WAV</span>
                <span className="bg-white px-2.5 py-1 rounded-md border border-brand-100">M4A</span>
                <span className="bg-white px-2.5 py-1 rounded-md border border-brand-100">MP4</span>
              </div>
            </div>
          )}
        </div>

        {/* Settings */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="space-y-3">
            <div className="space-y-1.5">
              <label className="text-xs font-bold text-zinc-400 uppercase tracking-widest">제목</label>
              <input
                type="text"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder={file?.name || "회의 제목"}
                className="w-full px-3 py-2.5 bg-zinc-50 border border-zinc-200 rounded-lg focus:ring-2 focus:ring-zinc-900 outline-none transition-all font-medium text-sm"
              />
            </div>
            <div className="space-y-1.5">
              <label className="text-xs font-bold text-zinc-400 uppercase tracking-widest">주제 / 맥락</label>
              <textarea
                value={topic}
                onChange={(e) => setTopic(e.target.value)}
                placeholder="정확도를 높이려면 회의 배경을 적어주세요..."
                className="w-full px-3 py-2.5 bg-zinc-50 border border-zinc-200 rounded-lg focus:ring-2 focus:ring-zinc-900 outline-none transition-all h-20 resize-none font-medium text-sm"
              />
            </div>
            <div className="space-y-1.5">
              <label className="text-xs font-bold text-zinc-400 uppercase tracking-widest">참석자 <span className="text-brand-300 font-normal normal-case">(선택)</span></label>
              <input
                type="text"
                value={speakers}
                onChange={(e) => setSpeakers(e.target.value)}
                placeholder="예: 홍길동, 김영희, 이철수"
                className="w-full px-3 py-2.5 bg-zinc-50 border border-zinc-200 rounded-lg focus:ring-2 focus:ring-zinc-900 outline-none transition-all font-medium text-sm"
              />
            </div>
          </div>

          <div className="space-y-3">
            <ModeSelector modeNum={modeNum} onChange={setModeNum} />

            {/* Quick Profiles */}
            {profiles.length > 0 && (
              <div>
                <button
                  onClick={() => setShowProfiles(!showProfiles)}
                  className="flex items-center gap-2 text-xs font-bold text-zinc-500 hover:text-zinc-900 transition-colors"
                >
                  <ChevronDown className={`w-4 h-4 transition-transform ${showProfiles ? "" : "-rotate-90"}`} />
                  빠른 프로필 ({profiles.length})
                </button>
                <AnimatePresence>
                  {showProfiles && (
                    <motion.div
                      initial={{ height: 0, opacity: 0 }}
                      animate={{ height: "auto", opacity: 1 }}
                      exit={{ height: 0, opacity: 0 }}
                      className="mt-3 space-y-2 overflow-hidden"
                    >
                      {profiles.map(p => (
                        <button
                          key={p.name}
                          onClick={() => {
                            const match = Object.entries(MODE_PRESETS).find(
                              ([, v]) => v.language === p.language && v.translate === p.translate && v.type === p.type
                            );
                            if (match) setModeNum(Number(match[0]));
                          }}
                          className="w-full text-left px-4 py-2 bg-white border border-zinc-200 rounded-xl hover:bg-zinc-50 transition-all"
                        >
                          <span className="text-sm font-bold">{p.name}</span>
                          <span className="text-xs text-zinc-400 ml-2">{p.description}</span>
                        </button>
                      ))}
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
            )}
          </div>
        </div>

        {/* Submit */}
        <button
          onClick={handleSubmit}
          disabled={!file || uploading}
          className="w-full mt-1 flex items-center justify-center gap-2 py-3 bg-zinc-900 text-white rounded-xl font-bold hover:bg-zinc-800 transition-all shadow-lg disabled:opacity-50 disabled:cursor-not-allowed active:scale-[0.98]"
        >
          {uploading ? <Loader2 className="animate-spin" size={18} /> : <Upload size={18} />}
          {uploading ? "처리 중..." : "업로드 & 처리"}
        </button>
      </div>
    </div>
  );
}
