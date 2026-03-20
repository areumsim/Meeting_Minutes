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
    <div className="space-y-6">
      <div className="space-y-2">
        <label className="text-xs font-bold text-zinc-400 uppercase tracking-widest">Processing Mode</label>
        <select
          value={modeNum}
          onChange={(e) => onChange(Number(e.target.value))}
          disabled={disabled}
          className="w-full px-4 py-3 bg-zinc-50 border border-zinc-200 rounded-xl focus:ring-2 focus:ring-zinc-900 outline-none transition-all disabled:opacity-50 font-medium"
        >
          {Object.entries(MODE_PRESETS).map(([k, v]) => (
            <option key={k} value={k}>{k}. {v.label}</option>
          ))}
        </select>
      </div>

      <div className="p-6 bg-zinc-50 border border-zinc-100 rounded-2xl space-y-3">
        <div className="flex items-center gap-3">
          <Languages size={16} className="text-zinc-500" />
          <span className="text-sm font-bold text-zinc-700">
            Language: {preset.language === "ko" ? "Korean" : "English"}
          </span>
        </div>
        <div className="flex items-center gap-3">
          <CheckCircle2 size={16} className={preset.translate ? "text-emerald-500" : "text-zinc-300"} />
          <span className="text-sm font-bold text-zinc-700">
            Translation: {preset.translate ? "EN -> KO" : "Off"}
          </span>
        </div>
        <div className="flex items-center gap-3">
          <Info size={16} className="text-zinc-500" />
          <span className="text-sm font-bold text-zinc-700">
            Type: {preset.type}
          </span>
        </div>
      </div>

      {hint && (
        <div className="flex items-center gap-3 p-4 bg-blue-50 border border-blue-100 rounded-2xl text-blue-700 text-xs">
          <Info className="w-4 h-4 shrink-0" />
          <p>{hint}</p>
        </div>
      )}
    </div>
  );
}
