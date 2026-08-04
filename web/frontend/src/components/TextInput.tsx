import React, { useState } from "react";
import { FileText, Loader2, ClipboardPaste } from "lucide-react";
import { uploadFile } from "../lib/api";
import { MODE_PRESETS } from "../lib/types";
import ModeSelector from "./ModeSelector";

export default function TextInput({ onComplete }: { onComplete: (id: string) => void }) {
  const [text, setText] = useState("");
  const [title, setTitle] = useState("");
  const [topic, setTopic] = useState("");
  const [modeNum, setModeNum] = useState(1);
  const [processing, setProcessing] = useState(false);

  const preset = MODE_PRESETS[modeNum] || MODE_PRESETS[1];

  const handleSubmit = async () => {
    if (!text.trim()) return;
    setProcessing(true);

    try {
      const data = await import("../lib/api").then(api => api.processTextInput(text, {
        title,
        topic,
        type: preset.type,
        language: preset.language,
        translate: preset.translate
      }));
      onComplete(data.sessionId);
    } catch (err) {
      console.error(err);
      // 사무용 사용자에게 "콘솔을 확인하세요"는 실행 가능한 안내가 아니다.
      alert(`처리 실패: ${err instanceof Error ? err.message
        : "[설정]에서 API 키가 입력돼 있는지 확인한 뒤 다시 시도해 주세요."}`);
      setProcessing(false);
    }
  };

  const handlePaste = async () => {
    try {
      if (navigator.clipboard?.readText) {
        const clipboardText = await navigator.clipboard.readText();
        if (clipboardText) {
          setText((prev) => prev + (prev ? "\n" : "") + clipboardText);
          return;
        }
      }
      // Fallback: focus textarea so user can use Ctrl+V / Cmd+V
      document.querySelector<HTMLTextAreaElement>("textarea")?.focus();
      alert("텍스트 영역을 길게 눌러 '붙여넣기'를 선택해주세요.");
    } catch (err) {
      // iOS Safari may block clipboard API - guide user to manual paste
      document.querySelector<HTMLTextAreaElement>("textarea")?.focus();
      alert("텍스트 영역을 길게 눌러 '붙여넣기'를 선택해주세요.");
    }
  };

  return (
    <div className="max-w-4xl mx-auto px-1 md:px-0 pb-20 md:pb-0">
      <h2 className="text-2xl font-bold tracking-tight mb-1">텍스트 분석</h2>
      <p className="text-brand-500 mb-4 text-sm">기존 회의록이나 전사 텍스트를 붙여넣으면 AI가 정리합니다.</p>

      <div className="bg-white border border-zinc-200 rounded-2xl shadow-sm p-4 md:p-5">
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          <div className="lg:col-span-2 space-y-3 flex flex-col">
            <div className="space-y-1.5">
              <label htmlFor="text-title" className="text-xs font-bold text-zinc-500 uppercase tracking-widest">제목</label>
              <input
                id="text-title"
                type="text"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="예: 팀 회의 메모"
                className="w-full px-3 py-2.5 bg-zinc-50 border border-zinc-200 rounded-lg focus:ring-2 focus:ring-zinc-900 outline-none font-medium text-sm"
              />
            </div>
            <div className="space-y-1.5">
              <label htmlFor="text-topic" className="text-xs font-bold text-zinc-500 uppercase tracking-widest">주제 / 맥락</label>
              <input
                id="text-topic"
                type="text"
                value={topic}
                onChange={(e) => setTopic(e.target.value)}
                placeholder="배경 정보를 적어주세요..."
                className="w-full px-3 py-2.5 bg-zinc-50 border border-zinc-200 rounded-lg focus:ring-2 focus:ring-zinc-900 outline-none font-medium text-sm"
              />
            </div>
            <div className="space-y-1.5 flex-1 flex flex-col">
              <div className="flex items-center justify-between">
                <label htmlFor="text-body" className="text-xs font-bold text-zinc-500 uppercase tracking-widest">본문</label>
                <button
                  onClick={handlePaste}
                  className="flex items-center gap-1.5 text-xs font-bold text-brand-600 hover:text-brand-800 bg-brand-50 hover:bg-brand-100 px-3 py-1.5 rounded-lg transition-colors border border-brand-100"
                >
                  <ClipboardPaste size={14} /> 붙여넣기
                </button>
              </div>
              <textarea
                id="text-body"
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder="회의 메모, 전사 텍스트 등을 여기에 붙여넣으세요..."
                className="w-full px-3 py-2.5 bg-zinc-50 border border-zinc-200 rounded-lg focus:ring-2 focus:ring-zinc-900 outline-none flex-1 min-h-[220px] resize-none font-medium text-sm mt-1"
              />
              <p className="text-xs text-zinc-500 mt-1">{text.length.toLocaleString()}자</p>
            </div>
          </div>

          <div className="lg:col-span-1 border-t border-brand-100 lg:border-t-0 pt-4 lg:pt-0">
            <ModeSelector
              modeNum={modeNum}
              onChange={setModeNum}
              hint="입력한 텍스트를 AI가 분석해 회의록 문서로 정리합니다."
            />
          </div>
        </div>

        <button
          onClick={handleSubmit}
          disabled={!text.trim() || processing}
          className="w-full mt-4 flex items-center justify-center gap-2 py-3 bg-zinc-900 text-white rounded-xl font-bold hover:bg-zinc-800 transition-all shadow-lg disabled:opacity-50 disabled:cursor-not-allowed active:scale-[0.98]"
        >
          {processing ? <Loader2 className="animate-spin" size={18} /> : <FileText size={18} />}
          {processing ? "AI가 처리 중..." : "분석 & 문서 생성"}
        </button>
      </div>
    </div>
  );
}
