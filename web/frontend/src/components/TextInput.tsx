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
      alert("Processing failed. Check API Keys and console for details.");
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
      <h2 className="text-3xl md:text-4xl font-bold tracking-tight mb-2">Text Analysis</h2>
      <p className="text-brand-500 mb-6 md:mb-10 text-sm md:text-base">Paste existing meeting notes or raw transcripts for AI analysis.</p>

      <div className="bg-white border border-brand-100 md:border-zinc-200 rounded-2xl md:rounded-3xl shadow-sm md:shadow-xl p-5 md:p-10">
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 md:gap-8">
          <div className="md:col-span-1 lg:col-span-2 space-y-5 md:space-y-6 flex flex-col">
            <div className="space-y-2">
              <label className="text-xs font-bold text-zinc-400 uppercase tracking-widest">Title</label>
              <input
                type="text"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="e.g. Team Meeting Notes"
                className="w-full px-4 md:px-5 py-3 md:py-4 bg-zinc-50 border border-zinc-200 rounded-xl focus:ring-2 focus:ring-zinc-900 outline-none font-medium text-sm md:text-base"
              />
            </div>
            <div className="space-y-2">
              <label className="text-xs font-bold text-zinc-400 uppercase tracking-widest">Topic / Context</label>
              <input
                type="text"
                value={topic}
                onChange={(e) => setTopic(e.target.value)}
                placeholder="Provide context..."
                className="w-full px-4 md:px-5 py-3 md:py-4 bg-zinc-50 border border-zinc-200 rounded-xl focus:ring-2 focus:ring-zinc-900 outline-none font-medium text-sm md:text-base"
              />
            </div>
            <div className="space-y-2 flex-1 flex flex-col">
              <div className="flex items-center justify-between">
                <label className="text-xs font-bold text-zinc-400 uppercase tracking-widest">Text Content</label>
                <button
                  onClick={handlePaste}
                  className="flex items-center gap-1.5 text-xs font-bold text-brand-600 hover:text-brand-800 bg-brand-50 hover:bg-brand-100 px-3 py-1.5 rounded-lg transition-colors border border-brand-100"
                >
                  <ClipboardPaste size={14} /> Paste
                </button>
              </div>
              <textarea
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder="Paste your meeting notes, raw transcript, or any text here..."
                className="w-full px-4 md:px-5 py-3 md:py-4 bg-zinc-50 border border-zinc-200 rounded-xl focus:ring-2 focus:ring-zinc-900 outline-none flex-1 min-h-[250px] md:min-h-[300px] resize-none font-medium text-sm md:text-base mt-2"
              />
              <p className="text-[10px] md:text-xs text-zinc-400 mt-1">{text.length.toLocaleString()} characters</p>
            </div>
          </div>

          <div className="md:col-span-1 lg:col-span-1 border-t border-brand-100 md:border-t-0 pt-6 md:pt-0">
            <ModeSelector
              modeNum={modeNum}
              onChange={setModeNum}
              hint="Text will be analyzed and organized into meeting documents using AI."
            />
          </div>
        </div>

        <button
          onClick={handleSubmit}
          disabled={!text.trim() || processing}
          className="w-full mt-10 flex items-center justify-center gap-3 py-4 bg-zinc-900 text-white rounded-2xl font-bold hover:bg-zinc-800 transition-all shadow-xl disabled:opacity-50 disabled:cursor-not-allowed active:scale-[0.98]"
        >
          {processing ? <Loader2 className="animate-spin" size={20} /> : <FileText size={20} />}
          {processing ? "AI is processing..." : "Analyze & Generate Documents"}
        </button>
      </div>
    </div>
  );
}
