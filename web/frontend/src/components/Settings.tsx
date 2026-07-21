import React, { useState, useEffect } from "react";
import {
  Settings, Plus, Trash2, CheckCircle, XCircle, Save, Loader2, Plug,
} from "lucide-react";
import { motion, AnimatePresence } from "motion/react";
import {
  getConfig, updateConfig, getConfigSchema, isPackagedMode,
  testOpenAIKey, testAnthropicKey, testObsidianPath, reindexVault, shutdownApp,
  getProfiles, createProfile, deleteProfile, clearSessions,
  getApiKey, setApiKey, getAnthropicKey, setAnthropicKey,
  getWatcherStatus, startWatcher, stopWatcher,
} from "../lib/api";
import type { Profile } from "../lib/types";
import type { WatcherStatus } from "../lib/api";

interface Field {
  section: string;
  key: string;
  label: string;
  type: "text" | "password" | "bool" | "select" | "number";
  default?: any;
  desc?: string;
  options?: (string | { value: string; label: string })[];
  sensitive?: boolean;
  mirror?: [string, string][];
  placeholder?: string;
  scalar?: boolean;  // 최상위 스칼라 키(예: output_dir) — section 자체가 값
}
interface Group {
  id: string;
  label: string;
  desc?: string;
  fields: Field[];
}

// 모바일(백엔드 없음) 폴백 스키마 — config.json 대신 localStorage 에 저장.
const CLIENT_FALLBACK_SCHEMA: Group[] = [
  {
    id: "api", label: "API 키",
    desc: "이 기기에만 저장됩니다.",
    fields: [
      { section: "api", key: "openai_api_key", label: "OpenAI API 키 (필수)", type: "password", sensitive: true, placeholder: "sk-proj-..." },
      { section: "api", key: "anthropic_api_key", label: "Anthropic API 키 (선택)", type: "password", sensitive: true, placeholder: "sk-ant-..." },
    ],
  },
  {
    id: "models", label: "모델",
    fields: [
      { section: "models", key: "stt", label: "STT 모델", type: "text" },
      { section: "models", key: "gpt_model", label: "GPT 모델", type: "text" },
      { section: "models", key: "claude_model", label: "Claude 모델", type: "text" },
      { section: "models", key: "translate_model", label: "번역 모델", type: "text" },
    ],
  },
];

const pathOf = (f: Field) => (f.key ? `${f.section}.${f.key}` : f.section);

export default function SettingsView() {
  const [schema, setSchema] = useState<Group[] | null>(null);
  const [packaged, setPackaged] = useState(false);
  const [values, setValues] = useState<Record<string, any>>({});
  const [dirty, setDirty] = useState<Record<string, boolean>>({});

  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");

  const [testing, setTesting] = useState<string>("");
  const [testMsg, setTestMsg] = useState<Record<string, { ok: boolean; message: string }>>({});

  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [showNewProfile, setShowNewProfile] = useState(false);
  const [newProfile, setNewProfile] = useState({ name: "", description: "", type: "meeting", language: "ko", translate: false });

  // 고급: 전체 설정(JSON) 직접 편집
  const [showRaw, setShowRaw] = useState(false);
  const [rawText, setRawText] = useState("");
  const [rawMsg, setRawMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const load = async () => {
    const pm = await isPackagedMode();
    setPackaged(pm);
    let sch = await getConfigSchema();
    if (!sch) sch = CLIENT_FALLBACK_SCHEMA;
    setSchema(sch);

    const cfg = await getConfig();
    const v: Record<string, any> = {};
    for (const group of sch) {
      for (const f of group.fields) {
        const raw = f.key ? cfg?.[f.section]?.[f.key] : cfg?.[f.section]; // scalar: section 자체
        v[pathOf(f)] = raw ?? f.default ?? (f.type === "bool" ? false : "");
      }
    }
    // 모바일: 키는 localStorage 에 별도 저장
    if (!pm) {
      v["api.openai_api_key"] = getApiKey();
      v["api.anthropic_api_key"] = getAnthropicKey();
    }
    setValues(v);
    setDirty({});
    setProfiles(await getProfiles());
  };

  useEffect(() => { load(); }, []);

  const setField = (path: string, val: any) => {
    setValues((prev) => ({ ...prev, [path]: val }));
    setDirty((prev) => ({ ...prev, [path]: true }));
  };

  const handleSave = async () => {
    if (!schema) return;
    setSaving(true);
    setError("");
    try {
      const bySection: Record<string, any> = {};
      const put = (section: string, key: string, val: any) => {
        if (key) { (bySection[section] ||= {})[key] = val; }
        else { bySection[section] = val; }  // 최상위 스칼라(output_dir 등)
      };
      for (const group of schema) {
        for (const f of group.fields) {
          const path = pathOf(f);
          if (!dirty[path]) continue;
          let val = values[path];
          if (f.type === "number") val = Number(val);
          put(f.section, f.key, val);
          for (const [ms, mk] of f.mirror || []) put(ms, mk, val);
        }
      }

      if (packaged) {
        if (Object.keys(bySection).length) await updateConfig(bySection);
      } else {
        // 모바일: api 키는 localStorage, 나머지는 APP_CONFIG 병합
        if (bySection.api?.openai_api_key !== undefined) setApiKey(bySection.api.openai_api_key);
        if (bySection.api?.anthropic_api_key !== undefined) setAnthropicKey(bySection.api.anthropic_api_key);
        const full = await getConfig();
        for (const [sec, kv] of Object.entries(bySection)) {
          if (sec === "api") continue;
          full[sec] = { ...(full[sec] || {}), ...kv };
        }
        await updateConfig(full);
      }

      setDirty({});
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
      // 저장 후 서버 마스킹 값 재로드
      if (packaged) await load();
    } catch (e: any) {
      setError(e?.message || "저장에 실패했습니다.");
    }
    setSaving(false);
  };

  const runTest = async (kind: "openai" | "anthropic" | "obsidian") => {
    setTesting(kind);
    const res =
      kind === "openai" ? await testOpenAIKey() :
      kind === "anthropic" ? await testAnthropicKey() :
      await testObsidianPath();
    setTestMsg((prev) => ({ ...prev, [kind]: res }));
    setTesting("");
  };

  const handleReindex = async () => {
    setTesting("reindex");
    const res = await reindexVault();
    setTestMsg((prev) => ({ ...prev, reindex: res }));
    setTesting("");
  };

  const toggleRaw = async () => {
    if (!showRaw) {
      const cfg = await getConfig();
      setRawText(JSON.stringify(cfg, null, 2));
      setRawMsg(null);
    }
    setShowRaw((s) => !s);
  };

  const saveRaw = async () => {
    setRawMsg(null);
    let obj: any;
    try {
      obj = JSON.parse(rawText);
    } catch (e: any) {
      setRawMsg({ ok: false, text: `JSON 형식 오류: ${e?.message || e}` });
      return;
    }
    try {
      await updateConfig(obj);
      setRawMsg({ ok: true, text: "전체 설정이 저장되었습니다." });
      await load();  // 친절한 폼도 갱신
    } catch (e: any) {
      setRawMsg({ ok: false, text: `저장 실패: ${e?.message || e}` });
    }
  };

  const handleCreateProfile = async () => {
    if (!newProfile.name.trim()) return;
    await createProfile(newProfile);
    setShowNewProfile(false);
    setNewProfile({ name: "", description: "", type: "meeting", language: "ko", translate: false });
    setProfiles(await getProfiles());
  };
  const handleDeleteProfile = async (name: string) => {
    if (!confirm(`프로필 "${name}" 을(를) 삭제할까요?`)) return;
    await deleteProfile(name);
    setProfiles(await getProfiles());
  };
  const handleClearHistory = async () => {
    if (!confirm("이 기기의 모든 세션 기록을 삭제할까요? 되돌릴 수 없습니다.")) return;
    await clearSessions();
    alert("기록이 삭제되었습니다.");
  };

  if (!schema) return null;

  return (
    <div className="max-w-3xl mx-auto px-1 md:px-0">
      <h2 className="text-2xl font-bold tracking-tight mb-1">설정</h2>
      <p className="text-sm text-brand-500 mb-4">
        {packaged
          ? "모든 설정은 이 PC의 config.json 에 저장됩니다."
          : "설정은 이 기기(브라우저)에만 저장됩니다."}
      </p>

      {schema.map((group) => (
        <section key={group.id} className="bg-white border border-brand-200 rounded-2xl p-4 md:p-5 mb-3 shadow-sm">
          <h3 className="text-base font-bold mb-1 flex items-center gap-2 text-brand-900">
            <Settings size={16} /> {group.label}
          </h3>
          {group.desc && <p className="text-xs text-brand-500 mb-3">{group.desc}</p>}

          <div className="space-y-3">
            {group.fields.map((f) => (
              <FieldRow key={pathOf(f)} field={f} value={values[pathOf(f)]} onChange={(v) => setField(pathOf(f), v)} />
            ))}
          </div>

          {/* 연결 테스트 버튼 (패키지 모드) */}
          {packaged && group.id === "api" && (
            <>
              <TestRow label="OpenAI 연결 테스트" busy={testing === "openai"} result={testMsg.openai} onClick={() => runTest("openai")} />
              <TestRow label="Claude 연결 테스트" busy={testing === "anthropic"} result={testMsg.anthropic} onClick={() => runTest("anthropic")} />
            </>
          )}
          {packaged && group.id === "obsidian" && (
            <>
              <TestRow label="Obsidian 경로 확인" busy={testing === "obsidian"} result={testMsg.obsidian} onClick={() => runTest("obsidian")} />
              <TestRow label="검색 인덱스 재빌드" busy={testing === "reindex"} result={testMsg.reindex} onClick={handleReindex} />
              <p className="text-xs text-brand-400 mt-1">볼트(.md 폴더)를 바꾸거나 노트를 추가한 뒤 눌러 검색·위키를 최신화하세요.</p>
            </>
          )}
        </section>
      ))}

      {error && (
        <div className="mb-4 rounded-xl border border-red-200 bg-red-50 text-red-600 px-4 py-3 text-sm">{error}</div>
      )}

      <button
        onClick={handleSave}
        disabled={saving}
        className="mb-8 w-full md:w-auto flex items-center justify-center gap-2 px-8 py-3.5 bg-brand-950 text-white rounded-xl font-bold hover:bg-brand-900 transition-all shadow-xl active:scale-95"
      >
        {saving ? <Loader2 size={18} className="animate-spin" /> : saved ? <CheckCircle size={18} /> : <Save size={18} />}
        {saved ? "저장되었습니다!" : "설정 저장"}
      </button>

      {/* 고급: 전체 설정(JSON) 직접 편집 */}
      {packaged && (
        <section className="bg-white border border-brand-200 rounded-2xl p-4 md:p-5 mb-3 shadow-sm">
          <button onClick={toggleRaw} className="flex items-center gap-2 text-sm font-bold text-brand-700 hover:text-brand-900">
            <Settings size={16} /> 고급: 전체 설정(JSON) 직접 편집 {showRaw ? "▲" : "▼"}
          </button>
          {showRaw && (
            <div className="mt-3 space-y-2">
              <p className="text-xs text-brand-400">위 폼에 없는 항목(도메인 매핑·카테고리·별칭·Webhook 등)까지 config.json 전체를 직접 편집합니다. 키는 마스킹되어 보이며 그대로 두면 유지됩니다.</p>
              <textarea
                value={rawText}
                onChange={(e) => setRawText(e.target.value)}
                spellCheck={false}
                className="w-full h-96 px-3 py-2 bg-zinc-900 text-zinc-100 border border-zinc-700 rounded-lg outline-none font-mono text-xs leading-relaxed resize-y"
              />
              {rawMsg && (
                <div className={`text-sm ${rawMsg.ok ? "text-emerald-600" : "text-red-600"}`}>{rawMsg.text}</div>
              )}
              <button onClick={saveRaw} className="flex items-center gap-2 px-5 py-2.5 bg-brand-950 text-white rounded-xl text-sm font-bold hover:bg-brand-900 transition-all">
                <Save size={16} /> 전체 설정 저장
              </button>
            </div>
          )}
        </section>
      )}

      {/* 폴더 자동 감시 (패키지 모드) */}
      {packaged && <WatcherCard />}

      {/* Profiles */}
      <section className="bg-white border border-brand-200 rounded-2xl p-4 md:p-5 mb-3">
        <div className="flex items-center justify-between mb-6">
          <h3 className="text-lg font-bold">처리 프로필</h3>
          <button
            onClick={() => setShowNewProfile(!showNewProfile)}
            className="flex items-center gap-2 px-4 py-2 bg-brand-50 text-brand-700 rounded-xl text-sm font-medium hover:bg-brand-100 transition-all"
          >
            <Plus size={14} /> 새 프로필
          </button>
        </div>

        <AnimatePresence>
          {showNewProfile && (
            <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="overflow-hidden mb-6">
              <div className="p-6 bg-zinc-50 rounded-xl space-y-4 border border-zinc-200">
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <input type="text" value={newProfile.name} onChange={(e) => setNewProfile((p) => ({ ...p, name: e.target.value }))} placeholder="프로필 이름 (예: weekly_team)" className="px-4 py-2 border border-zinc-200 rounded-lg outline-none focus:ring-2 focus:ring-zinc-900 text-sm" />
                  <input type="text" value={newProfile.description} onChange={(e) => setNewProfile((p) => ({ ...p, description: e.target.value }))} placeholder="설명" className="px-4 py-2 border border-zinc-200 rounded-lg outline-none focus:ring-2 focus:ring-zinc-900 text-sm" />
                </div>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                  <select value={newProfile.type} onChange={(e) => setNewProfile((p) => ({ ...p, type: e.target.value }))} className="px-3 py-2 border border-zinc-200 rounded-lg text-sm bg-white">
                    <option value="meeting">회의</option>
                    <option value="seminar">세미나</option>
                    <option value="lecture">강의</option>
                  </select>
                  <select value={newProfile.language} onChange={(e) => setNewProfile((p) => ({ ...p, language: e.target.value }))} className="px-3 py-2 border border-zinc-200 rounded-lg text-sm bg-white">
                    <option value="ko">한국어</option>
                    <option value="en">English</option>
                  </select>
                  <label className="flex items-center gap-2 text-sm ml-2">
                    <input type="checkbox" checked={newProfile.translate} onChange={(e) => setNewProfile((p) => ({ ...p, translate: e.target.checked }))} className="w-4 h-4 rounded border-brand-300 text-brand-900 focus:ring-brand-900" />
                    영어 → 한국어 번역
                  </label>
                </div>
                <button onClick={handleCreateProfile} className="w-full md:w-auto px-6 py-2.5 bg-brand-950 text-white rounded-lg text-sm font-semibold hover:bg-brand-900 transition-all mt-2">프로필 생성</button>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        <div className="space-y-3">
          {profiles.map((p) => (
            <div key={p.name} className="flex flex-col md:flex-row md:items-center justify-between p-4 bg-zinc-50 border border-zinc-100 rounded-xl gap-3">
              <div>
                <span className="font-bold text-sm text-zinc-900">{p.name}</span>
                <span className="text-xs text-zinc-500 ml-3">{p.description}</span>
                <span className="text-[10px] text-brand-500 font-bold ml-2 uppercase bg-brand-50 px-2 py-0.5 rounded-md">[{p.source}]</span>
              </div>
              <div className="flex items-center gap-3 text-xs text-zinc-500 font-medium">
                <span className="bg-white px-2 py-1 rounded shadow-sm">{p.type}</span>
                <span className="bg-white px-2 py-1 rounded shadow-sm">{p.language}</span>
                {p.translate && <span className="bg-amber-50 text-amber-700 px-2 py-1 rounded shadow-sm">번역</span>}
                {p.source !== "builtin" && (
                  <button onClick={() => handleDeleteProfile(p.name)} className="p-1.5 hover:text-red-500 hover:bg-red-50 rounded-lg transition-colors ml-1">
                    <Trash2 size={16} />
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* 앱 종료 (패키지 모드) */}
      {packaged && (
        <section className="bg-white border border-brand-200 rounded-2xl p-4 md:p-5 mb-3 shadow-sm">
          <h3 className="text-base font-bold mb-1">앱 종료</h3>
          <p className="text-xs text-brand-400 mb-3">프로그램을 완전히 종료합니다. 종료 후 이 브라우저 탭은 닫으세요. 다시 쓰려면 MeetingMinutes.exe 를 다시 실행하세요.</p>
          <button
            onClick={async () => {
              if (!confirm("앱을 종료할까요? 진행 중인 처리가 있으면 중단됩니다.")) return;
              await shutdownApp();
              alert("앱이 종료되었습니다. 이 탭을 닫으세요.");
            }}
            className="flex items-center gap-2 px-5 py-2.5 bg-zinc-800 text-white rounded-xl text-sm font-bold hover:bg-zinc-900 transition-all"
          >
            앱 종료
          </button>
        </section>
      )}

      {/* Danger Zone */}
      <section className="bg-white border border-red-200 rounded-2xl p-6 md:p-8">
        <h3 className="text-lg font-bold text-red-600 mb-2">위험 구역</h3>
        <p className="text-sm text-red-500/80 mb-5">이 기기에 저장된 모든 세션·전사·요약을 삭제합니다.</p>
        <button onClick={handleClearHistory} className="flex items-center justify-center w-full md:w-auto gap-2 px-6 py-3 bg-red-50 text-red-600 border border-red-200 rounded-xl font-bold hover:bg-red-100 transition-all">
          <Trash2 size={16} /> 모든 기기 기록 삭제
        </button>
      </section>
    </div>
  );
}

function WatcherCard() {
  const [status, setStatus] = useState<WatcherStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  const refresh = async () => setStatus(await getWatcherStatus());

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 5000);  // 실행 중 상태·처리 건수 갱신
    return () => clearInterval(id);
  }, []);

  const onStart = async () => {
    setBusy(true); setMsg("");
    const r = await startWatcher();
    setMsg(r.message);
    await refresh();
    setBusy(false);
  };
  const onStop = async () => {
    setBusy(true); setMsg("");
    const r = await stopWatcher();
    setMsg(r.message);
    await refresh();
    setBusy(false);
  };

  const running = !!status?.running;
  const c = status?.counts;

  return (
    <section className="bg-white border border-brand-200 rounded-2xl p-4 md:p-5 mb-3 shadow-sm">
      <h3 className="text-base font-bold mb-1 flex items-center gap-2 text-brand-900">
        <Settings size={16} /> 폴더 자동 감시
        <span className={`ml-1 text-[11px] font-bold px-2 py-0.5 rounded-full ${running ? "bg-emerald-100 text-emerald-700" : "bg-zinc-100 text-zinc-500"}`}>
          {running ? "● 감시 중" : "○ 중지됨"}
        </span>
      </h3>
      <p className="text-xs text-brand-500 mb-3">
        지정한 폴더에 새 녹음 파일이 생기면 자동으로 회의록을 생성합니다. 감시 폴더는
        위 <b>'고급: 전체 설정(JSON)'</b>에서 <code>vault_watcher.watch_folders</code> 에 추가하세요.
      </p>

      {status?.folders && status.folders.length > 0 ? (
        <div className="mb-3 text-xs text-brand-600">
          <span className="font-bold">감시 폴더:</span>
          <ul className="list-disc ml-5 mt-1 space-y-0.5 font-mono">
            {status.folders.map((f) => <li key={f}>{f}</li>)}
          </ul>
        </div>
      ) : (
        <div className="mb-3 text-xs text-amber-600">감시 폴더가 설정되지 않았습니다.</div>
      )}

      {c && (
        <div className="mb-3 flex flex-wrap gap-2 text-xs">
          <Stat label="완료" value={c.done} tone="emerald" />
          <Stat label="처리중" value={c.processing} tone="sky" />
          <Stat label="실패" value={c.failed} tone="red" />
          <Stat label="건너뜀" value={c.skipped} tone="zinc" />
        </div>
      )}

      {status?.recent && status.recent.length > 0 && (
        <div className="mb-3 text-xs">
          <div className="font-bold text-brand-600 mb-1">최근 처리</div>
          <div className="space-y-1">
            {status.recent.slice(0, 5).map((r, i) => (
              <div key={i} className="flex items-center gap-2 text-brand-500">
                <span className={`w-1.5 h-1.5 rounded-full ${r.status === "done" ? "bg-emerald-500" : r.status === "failed" ? "bg-red-500" : "bg-sky-500"}`} />
                <span className="font-mono truncate max-w-[16rem]">{r.file}</span>
                <span className="text-brand-400">{r.processed_at?.replace("T", " ")}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="flex items-center gap-3">
        {running ? (
          <button onClick={onStop} disabled={busy} className="flex items-center gap-2 px-5 py-2.5 bg-zinc-800 text-white rounded-xl text-sm font-bold hover:bg-zinc-900 transition-all">
            {busy ? <Loader2 size={16} className="animate-spin" /> : <XCircle size={16} />} 감시 중지
          </button>
        ) : (
          <button onClick={onStart} disabled={busy} className="flex items-center gap-2 px-5 py-2.5 bg-brand-950 text-white rounded-xl text-sm font-bold hover:bg-brand-900 transition-all">
            {busy ? <Loader2 size={16} className="animate-spin" /> : <Plug size={16} />} 감시 시작
          </button>
        )}
      </div>
      {msg && <p className="text-xs text-brand-500 mt-2">{msg}</p>}
    </section>
  );
}

function Stat({ label, value, tone }: { label: string; value: number; tone: string }) {
  const tones: Record<string, string> = {
    emerald: "bg-emerald-50 text-emerald-700",
    sky: "bg-sky-50 text-sky-700",
    red: "bg-red-50 text-red-700",
    zinc: "bg-zinc-100 text-zinc-600",
  };
  return (
    <span className={`px-2.5 py-1 rounded-lg font-bold ${tones[tone] || tones.zinc}`}>
      {label} {value}
    </span>
  );
}

function FieldRow({ field, value, onChange }: { field: Field; value: any; onChange: (v: any) => void }) {
  if (field.type === "bool") {
    return (
      <label className="flex items-start justify-between gap-4 cursor-pointer">
        <span>
          <span className="text-sm font-medium text-brand-900">{field.label}</span>
          {field.desc && <span className="block text-xs text-brand-400 mt-0.5">{field.desc}</span>}
        </span>
        <input type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} className="mt-1 w-5 h-5 rounded border-brand-300 text-brand-900 focus:ring-brand-900 shrink-0" />
      </label>
    );
  }

  return (
    <div className="space-y-2">
      <label className="text-xs font-bold text-zinc-400 uppercase tracking-widest">{field.label}</label>
      {field.type === "select" ? (
        <select value={value ?? ""} onChange={(e) => onChange(e.target.value)} className="w-full px-3 py-2 bg-zinc-50 border border-zinc-200 rounded-lg outline-none focus:ring-2 focus:ring-brand-500 text-sm">
          {(field.options || []).map((o) => {
            const val = typeof o === "string" ? o : o.value;
            const lbl = typeof o === "string" ? o : o.label;
            return <option key={val} value={val}>{lbl}</option>;
          })}
        </select>
      ) : (
        <input
          type={field.type === "password" ? "password" : field.type === "number" ? "number" : "text"}
          value={value ?? ""}
          onChange={(e) => onChange(e.target.value)}
          placeholder={field.placeholder || ""}
          className="w-full px-3 py-2 bg-zinc-50 border border-zinc-200 rounded-lg outline-none focus:ring-2 focus:ring-brand-500 text-sm font-mono tracking-wide"
        />
      )}
      {field.desc && <p className="text-xs text-brand-400">{field.desc}</p>}
    </div>
  );
}

function TestRow({ label, busy, result, onClick }: { label: string; busy: boolean; result?: { ok: boolean; message: string }; onClick: () => void }) {
  return (
    <div className="mt-5 flex flex-col md:flex-row md:items-center gap-3">
      <button onClick={onClick} disabled={busy} className="flex items-center gap-2 px-5 py-2.5 bg-brand-50 text-brand-700 rounded-xl text-sm font-semibold hover:bg-brand-100 transition-all w-fit">
        {busy ? <Loader2 size={16} className="animate-spin" /> : <Plug size={16} />} {label}
      </button>
      {result && (
        <span className={`flex items-center gap-1.5 text-sm ${result.ok ? "text-emerald-600" : "text-red-600"}`}>
          {result.ok ? <CheckCircle size={16} /> : <XCircle size={16} />} {result.message}
        </span>
      )}
    </div>
  );
}
