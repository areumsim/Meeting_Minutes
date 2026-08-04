import React, { useState, useRef } from "react";
import {
  Upload, FileAudio, Loader2, ChevronDown,
} from "lucide-react";
import { motion, AnimatePresence } from "motion/react";
import { uploadFile, confirmUpload, cancelPendingUpload, getProfiles } from "../lib/api";
import { MODE_PRESETS } from "../lib/types";
import type { Profile } from "../lib/types";
import ModeSelector from "./ModeSelector";
import Modal from "./ui/Modal";

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
  // 예상 비용 확인 대기 상태(서버가 confirm_required 를 돌려줬을 때)
  const [pending, setPending] = useState<{
    pendingId: string; estimateUsd: number; durationSec: number;
    monthToDateUsd: number; monthlyCapUsd: number;
  } | null>(null);
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
      if ("pendingId" in data) {
        // 서버가 예상 비용 확인을 요구 — 모달을 띄우고 사용자의 [계속]을 기다린다.
        setPending(data);
        setUploading(false);
        return;
      }
      onComplete(data.sessionId);
    } catch (err) {
      console.error(err);
      // 사무용 사용자에게 "콘솔을 확인하세요"는 실행 가능한 안내가 아니다.
      alert(`업로드 실패: ${err instanceof Error ? err.message
        : "서버에 연결할 수 없습니다. 프로그램이 실행 중인지 확인한 뒤 다시 시도해 주세요."}`);
      setUploading(false);
    }
  };

  const handleConfirm = async () => {
    if (!pending) return;
    setUploading(true);
    try {
      const data = await confirmUpload(pending.pendingId);
      setPending(null);
      onComplete(data.sessionId);
    } catch (err) {
      console.error(err);
      alert(`처리 시작 실패: ${err instanceof Error ? err.message : "다시 시도하세요."}`);
      setPending(null);
      setUploading(false);
    }
  };

  const handleCancelPending = async () => {
    if (!pending) return;
    void cancelPendingUpload(pending.pendingId);
    setPending(null);
    setUploading(false);
  };

  const fmtUsd = (n: number) => `$${n.toFixed(2)}`;

  const formatSize = (bytes: number) => {
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  return (
    <div className="max-w-4xl mx-auto px-1 md:px-0">
      {/* 예상 비용 확인 모달 — 돈을 쓰기 전 마지막 관문.
          백드롭 클릭으로는 닫지 않는다(오클릭 시 업로드된 파일이 서버에서 삭제돼
          대용량 파일을 처음부터 다시 올려야 하는 사고 방지). Escape 는 닫는다 —
          명시적 키보드 행동이라 오클릭 방지와 충돌하지 않고, 이것마저 없으면
          키보드 사용자는 모달에서 빠져나올 수단이 아예 없다(취소 버튼과 동일 동작). */}
      {pending && (
        <Modal labelledBy="upload-cost-title" onClose={handleCancelPending}
          panelClassName="bg-white rounded-2xl shadow-xl max-w-sm w-full p-6">
          <h3 id="upload-cost-title" className="text-lg font-bold text-brand-900 mb-1">예상 비용 확인</h3>
          <p className="text-sm text-brand-500 mb-4">
            이 파일을 처리하면 아래 정도의 API 비용이 발생합니다(대략값이라 실제 청구액과 다를 수 있습니다).
          </p>
          <div className="rounded-xl bg-zinc-50 border border-zinc-200 p-4 space-y-2 mb-5">
            <div className="flex items-baseline justify-between">
              <span className="text-sm text-brand-500">이 파일 예상 비용</span>
              <span className="text-2xl font-bold text-brand-900">
                {pending.estimateUsd > 0 ? fmtUsd(pending.estimateUsd) : "산정 불가"}
              </span>
            </div>
            {pending.durationSec > 0 && (
              <div className="flex items-center justify-between text-xs text-brand-500">
                <span>길이</span><span>약 {Math.round(pending.durationSec / 60)}분</span>
              </div>
            )}
            {pending.monthlyCapUsd > 0 && (
              <div className="flex items-center justify-between text-xs text-brand-500 pt-1 border-t border-zinc-200">
                <span>이번 달 예상 지출 / 한도</span>
                <span>{fmtUsd(pending.monthToDateUsd)} / {fmtUsd(pending.monthlyCapUsd)}</span>
              </div>
            )}
          </div>
          <div className="flex gap-2">
            <button
              onClick={handleCancelPending}
              className="flex-1 py-2.5 rounded-xl border border-zinc-200 text-sm font-semibold text-brand-600 hover:bg-zinc-50 transition-colors"
            >
              취소
            </button>
            <button
              onClick={handleConfirm}
              className="flex-1 py-2.5 rounded-xl bg-zinc-900 text-white text-sm font-bold hover:bg-zinc-800 transition-colors"
            >
              계속 처리
            </button>
          </div>
        </Modal>
      )}

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
              <label htmlFor="upload-title" className="text-xs font-bold text-zinc-400 uppercase tracking-widest">제목</label>
              <input
                id="upload-title"
                type="text"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder={file?.name || "회의 제목"}
                className="w-full px-3 py-2.5 bg-zinc-50 border border-zinc-200 rounded-lg focus:ring-2 focus:ring-zinc-900 outline-none transition-all font-medium text-sm"
              />
            </div>
            <div className="space-y-1.5">
              <label htmlFor="upload-topic" className="text-xs font-bold text-zinc-400 uppercase tracking-widest">주제 / 맥락</label>
              <textarea
                id="upload-topic"
                value={topic}
                onChange={(e) => setTopic(e.target.value)}
                placeholder="정확도를 높이려면 회의 배경을 적어주세요..."
                className="w-full px-3 py-2.5 bg-zinc-50 border border-zinc-200 rounded-lg focus:ring-2 focus:ring-zinc-900 outline-none transition-all h-20 resize-none font-medium text-sm"
              />
            </div>
            <div className="space-y-1.5">
              <label htmlFor="upload-speakers" className="text-xs font-bold text-zinc-400 uppercase tracking-widest">참석자 <span className="text-brand-400 font-normal normal-case">(선택)</span></label>
              <input
                id="upload-speakers"
                type="text"
                value={speakers}
                onChange={(e) => setSpeakers(e.target.value)}
                placeholder="예: 홍길동, 김영희, 이철수"
                className="w-full px-3 py-2.5 bg-zinc-50 border border-zinc-200 rounded-lg focus:ring-2 focus:ring-zinc-900 outline-none transition-all font-medium text-sm"
              />
            </div>
            {/*
              녹취 고지 — Recorder 의 문구와 같은 톤·같은 자리(입력 아래 정적 한 줄).
              모달·상시 배너는 두지 않는다(0c28713 판단 유지: 매번 확인 강제는 실익 없이
              사용자만 이탈시킨다). 실시간 경로에만 있던 안내를 업로드 경로에도 둔 것은
              "이 도구는 몰래 녹음용이 아니다"가 진입점마다 최소 1회 보여야 하기 때문이다.
            */}
            <p className="text-xs text-zinc-400 sm:col-span-2">
              업로드하는 녹음은 <b>참석자에게 녹음·자동 전사 사실을 알린 뒤</b> 취득한 것이어야 합니다.
            </p>
          </div>

          <div className="space-y-3">
            <ModeSelector modeNum={modeNum} onChange={setModeNum} />

            {/* Quick Profiles */}
            {profiles.length > 0 && (
              <div>
                <button
                  onClick={() => setShowProfiles(!showProfiles)}
                  aria-expanded={showProfiles}
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
