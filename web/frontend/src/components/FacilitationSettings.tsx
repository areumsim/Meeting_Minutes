import React, { useEffect, useState } from "react";
import { AlertTriangle, CheckCircle, Loader2, Lock, Save, Users } from "lucide-react";
import {
  getFacilitationPersonas, updateConfig,
  type FacilitationPersonas, type PersonaInfo,
} from "../lib/api";

/**
 * 페르소나별 참견도 매트릭스 (PRD §19.6).
 *
 * 왜 전용 컴포넌트인가 — 설정 화면은 스키마 기반이고 필드 타입에 슬라이더가 없다.
 * 8개 페르소나 × 참견도 6단을 number 입력 8줄로 두면 아무도 못 쓴다. 처리 프로필·폴더
 * 감시 카드처럼 자체 섹션으로 두고 저장도 스스로 한다(스키마 폼의 dirty 상태와 섞지 않는다).
 *
 * 이 화면이 없으면 M1 은 사실상 꺼진 기능이다 — M0 부터 쓰던 config.json 은 전원
 * 참견도 1(관찰)이라, 기능을 켜도 화면에 아무것도 뜨지 않는다. "켰는데 아무 일도
 * 안 일어난다"는 이 리포가 반복해서 없애온 함정이다.
 *
 * 목록·상한·실효값은 **서버 레지스트리**에서 받는다(personas.py + facilitation.persona_level).
 * 프런트가 클램프를 따로 계산하면 "3으로 올렸는데 안 뜬다"가 된다.
 */

const LEVEL_LABEL = ["금지", "관찰", "소극", "표준", "알림음", "음성"];
const LEVEL_HINT = [
  "트리아지에서 제외 — 이 페르소나에는 비용이 들지 않습니다",
  "기록만 — 화면에 뜨지 않습니다(오탐률 실측용)",
  "모아 보기 — 녹음 화면의 [지금 점검]을 누를 때 표시됩니다",
  "표준 — 개입이 생기면 옆 카드로 조용히 표시됩니다(소리 없음)",
  "알림음 — 아직 구현되지 않았습니다(M3)",
  "음성 — 아직 구현되지 않았습니다(M3)",
];

const RISK_LABEL: Record<string, { text: string; cls: string }> = {
  low: { text: "저위험", cls: "bg-sky-50 text-sky-700 border-sky-200" },
  medium: { text: "중위험", cls: "bg-violet-50 text-violet-700 border-violet-200" },
  high: { text: "고위험", cls: "bg-amber-50 text-amber-700 border-amber-200" },
};

/** 프리셋 — 사내 온보딩 마찰을 줄인다. 위험 페르소나는 어느 프리셋에서도 올리지 않는다. */
const PRESETS: { id: string; label: string; desc: string; level: (p: PersonaInfo) => number }[] = [
  {
    id: "quiet", label: "조용히", desc: "전원 관찰 — 화면 표시 없이 기록만(오탐률 실측용)",
    level: () => 1,
  },
  {
    id: "standard", label: "표준", desc: "저위험은 옆 카드, 중위험은 모아 보기, 고위험은 관찰",
    level: (p) => (p.risk === "low" ? 3 : p.risk === "medium" ? 2 : 1),
  },
  {
    id: "active", label: "적극", desc: "저·중위험 모두 옆 카드. 고위험(팩트체커·비판자)은 관찰 유지",
    level: (p) => (p.risk === "high" ? 1 : 3),
  },
];

export default function FacilitationSettings() {
  const [data, setData] = useState<FacilitationPersonas | null>(null);
  const [levels, setLevels] = useState<Record<string, number>>({});
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");

  const load = async () => {
    const d = await getFacilitationPersonas();
    if (!d) return;
    setData(d);
    // 편집 대상은 **설정에 적힌 값**이다. 실효값(상한에 걸려 내려간 값)을 편집하면
    // 저장할 때마다 사용자가 적어둔 값이 조용히 깎인다.
    setLevels(Object.fromEntries(d.personas.map((p) => [p.key, p.configuredLevel])));
  };
  useEffect(() => { load(); }, []);

  if (!data) return null;               // 백엔드 없는 단독 모드에서는 렌더하지 않는다

  /** 이 페르소나가 올릴 수 있는 최대 참견도 — hard_cap 과 전역 상한 중 낮은 쪽. */
  const capOf = (p: PersonaInfo) =>
    Math.min(p.hardCap ?? data.maxLevel, data.maxLevel);

  const dirty = data.personas.some((p) => levels[p.key] !== p.configuredLevel);

  const applyPreset = (id: string) => {
    const preset = PRESETS.find((x) => x.id === id);
    if (!preset) return;
    setLevels(Object.fromEntries(data.personas.map((p) => [
      p.key, Math.min(preset.level(p), capOf(p)),
    ])));
    setSaved(false);
  };

  const save = async () => {
    setSaving(true);
    setError("");
    try {
      // 점 있는 키는 서버가 중첩 경로로 풀어 준다(settings.py `_dset`) —
      // facilitation.personas.<key>.level 로 저장된다.
      const payload: Record<string, number> = {};
      for (const p of data.personas) payload[`personas.${p.key}.level`] = levels[p.key];
      await updateConfig({ facilitation: payload });
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
      await load();                     // 서버가 클램프한 실효값을 다시 읽는다
    } catch (e: any) {
      setError(e?.message || "저장 실패");
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="bg-white border border-brand-200 rounded-2xl p-4 md:p-5 mb-3 shadow-sm">
      <div className="flex items-start justify-between gap-3 mb-1">
        <h3 className="text-base font-bold text-brand-900 flex items-center gap-2">
          <Users size={16} className="text-brand-500" /> 페르소나별 참견도
        </h3>
        <div className="flex gap-1.5 shrink-0">
          {PRESETS.map((p) => (
            <button
              key={p.id}
              type="button"
              onClick={() => applyPreset(p.id)}
              title={p.desc}
              className="px-2.5 py-1 text-[11px] font-semibold bg-brand-50 text-brand-700 rounded-lg hover:bg-brand-100 transition-colors"
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>
      <p className="text-xs text-brand-500 mb-3">
        0=금지(비용 0) · 1=관찰(기록만) · 2=모아 보기([지금 점검]) · 3=옆 카드 자동(무음).
        {!data.enabled && " 이 기능이 꺼져 있어 지금은 아무 것도 동작하지 않습니다 — 위 [회의 진행 페르소나 사용]을 먼저 켜세요."}
      </p>

      <div className="space-y-1.5">
        {data.personas.map((p) => {
          const cap = capOf(p);
          const cur = levels[p.key] ?? p.configuredLevel;
          const risk = RISK_LABEL[p.risk] || RISK_LABEL.low;
          return (
            <div key={p.key} className="flex flex-col md:flex-row md:items-center gap-1.5 md:gap-3 py-1.5 border-b border-brand-100 last:border-0">
              <div className="md:w-64 shrink-0">
                <div className="flex items-center gap-1.5">
                  <span className="text-sm font-semibold text-brand-900">{p.label}</span>
                  <span className={`text-[10px] px-1.5 py-0.5 rounded border ${risk.cls}`}>
                    {risk.text}
                  </span>
                </div>
                <p className="text-[11px] text-brand-400 line-clamp-1" title={p.role}>{p.role}</p>
              </div>

              <div className="flex items-center gap-1 flex-wrap">
                {LEVEL_LABEL.map((name, lvl) => {
                  const locked = lvl > cap;
                  const active = cur === lvl;
                  return (
                    <button
                      key={lvl}
                      type="button"
                      disabled={locked}
                      onClick={() => { setLevels((s) => ({ ...s, [p.key]: lvl })); setSaved(false); }}
                      title={locked
                        ? (p.hardCap !== null && lvl > p.hardCap
                          ? `${p.label}는 오탐의 비용이 커서 참견도 ${p.hardCap} 이상으로 올릴 수 없습니다(코드 상한).`
                          : `참견도 전역 상한(${data.maxLevel})을 넘습니다 — 위 [참견도 전역 상한]을 먼저 올리세요.`)
                        : LEVEL_HINT[lvl]}
                      className={`px-2 py-1 text-[11px] font-medium rounded-lg border transition-colors flex items-center gap-1 ${
                        locked
                          ? "border-zinc-100 bg-zinc-50 text-zinc-300 cursor-not-allowed"
                          : active
                            ? "border-brand-900 bg-brand-900 text-white"
                            : "border-brand-200 text-brand-600 hover:border-brand-400"
                      }`}
                    >
                      {locked && <Lock className="w-2.5 h-2.5" />}
                      {lvl} {name}
                    </button>
                  );
                })}
              </div>

              {/* 적용값이 적어둔 값과 다르면 그 사실을 숨기지 않는다 */}
              {p.level !== p.configuredLevel && cur === p.configuredLevel && (
                <span className="text-[11px] text-amber-600 flex items-center gap-1">
                  <AlertTriangle className="w-3 h-3" /> 적용값 {p.level}
                </span>
              )}
            </div>
          );
        })}
      </div>

      {error && (
        <p className="text-xs text-red-600 mt-2">{error}</p>
      )}

      <div className="flex items-center gap-2 mt-3">
        <button
          type="button"
          onClick={save}
          disabled={saving || !dirty}
          className="flex items-center gap-2 px-4 py-2 bg-brand-950 text-white rounded-xl text-sm font-bold hover:bg-brand-900 transition-all disabled:opacity-40"
        >
          {saving ? <Loader2 size={14} className="animate-spin" />
            : saved ? <CheckCircle size={14} /> : <Save size={14} />}
          {saved ? "저장되었습니다!" : "참견도 저장"}
        </button>
        <p className="text-[11px] text-brand-400">
          참견도 2 이상은 개입 문장 생성에 상위 모델이 쓰여 회의당 비용 캡을 소모합니다.
          {data.personas.some((p) => (levels[p.key] ?? 0) >= data.displayLevel)
            && " 3(옆 카드)로 둔 페르소나는 녹음 중 화면에 카드가 뜹니다."}
        </p>
      </div>
    </section>
  );
}
