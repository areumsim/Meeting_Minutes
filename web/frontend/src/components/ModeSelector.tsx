import React from "react";
import { Languages, CheckCircle2, Info } from "lucide-react";
import { MODE_PRESETS } from "../lib/types";

interface Props {
  modeNum: number;
  onChange: (mode: number) => void;
  disabled?: boolean;
  hint?: string;
}

export default function ModeSelector({ modeNum, onChange, disabled, hint }: Props) {
  const preset = MODE_PRESETS[modeNum] || MODE_PRESETS[1];

  return (
    <div className="space-y-3">
      <div className="space-y-1.5">
        <label className="text-xs font-bold text-zinc-400 uppercase tracking-widest">처리 모드</label>
        <select
          value={modeNum}
          onChange={(e) => onChange(Number(e.target.value))}
          disabled={disabled}
          className="w-full px-3 py-2.5 bg-zinc-50 border border-zinc-200 rounded-lg focus:ring-2 focus:ring-zinc-900 outline-none transition-all disabled:opacity-50 font-medium text-sm"
        >
          {Object.entries(MODE_PRESETS).map(([k, v]) => (
            <option key={k} value={k}>{k}. {v.label}</option>
          ))}
        </select>
      </div>

      <div className="p-3 bg-zinc-50 border border-zinc-100 rounded-xl space-y-2">
        <div className="flex items-center gap-2.5">
          <Languages size={15} className="text-zinc-500" />
          <span className="text-sm font-medium text-zinc-700">
            언어: {preset.language === "ko" ? "한국어" : "영어"}
          </span>
        </div>
        <div className="flex items-center gap-2.5">
          <CheckCircle2 size={15} className={preset.translate ? "text-emerald-500" : "text-zinc-300"} />
          <span className="text-sm font-medium text-zinc-700">
            번역: {preset.translate ? "영어 → 한국어" : "없음"}
          </span>
        </div>
        <div className="flex items-center gap-2.5">
          <Info size={15} className="text-zinc-500" />
          <span className="text-sm font-medium text-zinc-700">
            유형: {preset.type === "meeting" ? "회의" : preset.type === "seminar" ? "세미나" : preset.type === "lecture" ? "강의" : preset.type}
          </span>
        </div>
      </div>

      {hint && (
        <div className="flex items-center gap-3 p-3 bg-blue-50 border border-blue-100 rounded-xl text-blue-700 text-xs">
          <Info className="w-4 h-4 shrink-0" />
          <p>{hint}</p>
        </div>
      )}
    </div>
  );
}
