import React, { useState, useEffect } from "react";
import {
  Settings, Plus, Trash2, CheckCircle, XCircle, Save, Loader2, Plug,
  Eye, EyeOff, FolderOpen, ChevronDown, ChevronRight, Wand2, AlertTriangle,
} from "lucide-react";
import { motion, AnimatePresence } from "motion/react";
import {
  getConfig, updateConfig, getConfigSchema, isPackagedMode,
  testOpenAIKey, testAnthropicKey, testObsidianPath, testEmail, testSlack, testTeams, reindexVault, shutdownApp,
  getProfiles, createProfile, deleteProfile, clearSessions,
  getApiKey, setApiKey, getAnthropicKey, setAnthropicKey,
  getWatcherStatus, startWatcher, stopWatcher, obsidianDiagnose, pickFolder,
  getBackendUrl, setBackendUrl, testBackendUrl, revealSecret,
  localSttStatus, prepareLocalStt,
} from "../../lib/api";
import { Capacitor } from "@capacitor/core";
import { typeLabel } from "../../lib/format";
import { getThemeChoice, setThemeChoice, type ThemeChoice } from "../../lib/theme";
import { SegmentedControl } from "../../ui/Tabs";
import { Input } from "../../ui/Field";
import Toggle from "../../ui/Toggle";
import type { Profile } from "../../lib/types";
import type { WatcherStatus, DiagnoseResult, LocalSttStatus } from "../../lib/api";
import PersonaMatrix from "./PersonaMatrix";

// 네이티브 앱(iOS/Android)에서만 'PC 서버 연결' 카드를 노출한다.
const IS_NATIVE = Capacitor.isNativePlatform();

interface Field {
  section: string;
  key: string;
  label: string;
  /** 스키마가 실제로 쓰는 값들. "boolean" 은 한 필드(analysis.minutes_vault_context)만
   *  쓰는데 종전 화면이 그 표기를 몰라 **불리언 설정이 텍스트 입력으로** 그려졌다. */
  type: "text" | "password" | "bool" | "boolean" | "select" | "number" | "list" | "textarea";
  default?: any;
  desc?: string;
  options?: (string | { value: string; label: string })[];
  sensitive?: boolean;
  mirror?: [string, string][];
  placeholder?: string;
  scalar?: boolean;   // 최상위 스칼라 키(예: output_dir) — section 자체가 값
  picker?: boolean;   // 폴더 선택 '찾아보기' 버튼 표시
  required?: boolean; // 필수값 — 라벨에 * 표시, 미입력 시 저장 전 경고
}
interface Group {
  id: string;
  label: string;
  desc?: string;
  advanced?: boolean; // true 면 기본 접힘('고급')
  tier?: "core" | "common" | "advanced"; // 화면 배치 단계(꼭 확인/자주 쓰는 선택/고급)
  fields: Field[];
}

// 모바일(백엔드 없음) 폴백 스키마 — config.json 대신 localStorage 에 저장.
const CLIENT_FALLBACK_SCHEMA: Group[] = [
  {
    id: "api", label: "API 키",
    desc: "이 기기에만 저장됩니다.",
    fields: [
      { section: "api", key: "openai_api_key", label: "OpenAI API 키 (필수)", type: "password", sensitive: true, required: true, placeholder: "sk-proj-..." },
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

/**
 * 화면 표시 단계만 내리는 예외 표 (PRD §6.7 리뷰 P2-4).
 *
 * 스키마의 tier 가 정본이고 서버(`config_schema.py`)를 바꾸지 않는다 — 다만 PRD 는
 * "필수 = 키 + 폴더"로 확정했고 모델 그룹은 거기 없다. 서버 스키마에서 모델은 core 라
 * 그대로 두면 첫 실행 사용자가 STT·LLM 모델 8종을 먼저 만난다. 그래서 **이 한 줄만**
 * 예외로 두고, 새 예외가 생기면 여기에 적어 한눈에 보이게 한다.
 */
const TIER_OVERRIDE: Record<string, "core" | "common" | "advanced"> = { models: "common" };

/** 설정 검색 — 라벨·설명·키를 함께 본다(사용자는 "ssl" 로도, "인증서" 로도 찾는다). */
function matchesQuery(group: Group, q: string): Group | null {
  const needle = q.trim().toLowerCase();
  if (!needle) return group;
  const hit = (t?: string) => !!t && t.toLowerCase().includes(needle);
  if (hit(group.label) || hit(group.desc) || hit(group.id)) return group;
  const fields = group.fields.filter(
    (f) => hit(f.label) || hit(f.desc) || hit(f.key) || hit(f.section) || hit(pathOf(f)),
  );
  return fields.length ? { ...group, fields } : null;
}

// 서버가 내려준 마스킹 값인지(예: "sk-proj-...Ab3X" 또는 "***"). 실제 키/비번엔
// 보통 "..."/"***"가 없으므로 편집·저장 판단에 쓴다.
const looksMasked = (v: any) => typeof v === "string" && (v.includes("...") || v.includes("***"));

// ── PC 서버 연결 카드 (네이티브 앱 전용) ─────────────────────────
// 같은 WiFi의 PC(exe)에 연결하면 서버 파이프라인(2-pass 보정·위키·그래프)을
// 그대로 쓴다. 비우면 단독 모드(기기에 저장된 OpenAI 키로 직접 전사).
function BackendConnectionCard({ onChanged }: { onChanged: () => void }) {
  const [url, setUrl] = useState(getBackendUrl());
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);
  const connected = !!getBackendUrl();

  const doTest = async () => {
    setBusy(true); setResult(null);
    setResult(await testBackendUrl(url));
    setBusy(false);
  };
  const doConnect = async () => {
    setBusy(true);
    const r = await testBackendUrl(url);
    setResult(r);
    if (r.ok) { setBackendUrl(url); onChanged(); }
    setBusy(false);
  };
  const doDisconnect = () => {
    setUrl(""); setBackendUrl(""); setResult(null); onChanged();
  };

  return (
    <section className="bg-surface border border-line rounded-card mb-3 shadow-sm overflow-hidden">
      <div className="px-4 md:px-5 py-4">
        <div className="flex items-center gap-2 mb-1">
          <Plug size={16} className="text-ink-3 shrink-0" />
          <span className="text-base font-bold text-ink">PC 서버 연결</span>
          <span className={`ml-auto text-[11px] font-bold px-2 py-0.5 rounded-full ${connected ? "bg-ok text-ok" : "bg-surface-2 text-ink-3"}`}>
            {connected ? "연결됨 (서버 모드)" : "단독 모드"}
          </span>
        </div>
        <p className="text-xs text-ink-3 mb-3">
          같은 WiFi에 있는 PC에서 <b>MeetingMinutes.exe</b>를 켠 뒤 그 주소를 넣으면,
          PC의 고품질 전사(2단계 보정)·위키·그래프를 아이폰에서 그대로 씁니다.
          비워 두면 이 기기의 OpenAI 키로 직접 처리합니다(단독 모드).
        </p>
        <div className="flex flex-col sm:flex-row gap-2">
          <input
            type="url"
            inputMode="url"
            autoCapitalize="none"
            autoCorrect="off"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="예: http://192.168.0.10:8501"
            className="flex-1 px-4 py-2.5 bg-surface-2 border border-line rounded-card outline-none focus:ring-2 focus:ring-accent font-mono text-sm"
          />
          <div className="flex gap-2">
            <button onClick={doTest} disabled={busy}
              className="px-4 py-2.5 bg-surface-2 text-ink-2 rounded-card text-sm font-semibold hover:bg-hover transition-all disabled:opacity-50">
              {busy ? <Loader2 size={16} className="animate-spin" /> : "테스트"}
            </button>
            <button onClick={doConnect} disabled={busy || !url.trim()}
              className="px-4 py-2.5 bg-accent-solid text-white rounded-card text-sm font-bold hover:bg-accent-solid-hover transition-all disabled:opacity-50">
              연결
            </button>
            {connected && (
              <button onClick={doDisconnect} disabled={busy}
                className="px-4 py-2.5 bg-rec-bg text-rec rounded-card text-sm font-semibold hover:bg-rec-bg transition-all disabled:opacity-50">
                해제
              </button>
            )}
          </div>
        </div>
        {result && (
          <div className={`mt-2 flex items-center gap-2 text-sm ${result.ok ? "text-ok" : "text-rec"}`}>
            {result.ok ? <CheckCircle size={14} /> : <XCircle size={14} />} {result.message}
          </div>
        )}
        <p className="text-[11px] text-ink-3 mt-2">
          PC 주소 확인: exe 실행 후 브라우저에 표시되는 주소, 또는 PC에서 <b>ipconfig</b>의 IPv4 주소 + <b>:8501</b>.
        </p>
      </div>
    </section>
  );
}

export default function SettingsView() {
  const [schema, setSchema] = useState<Group[] | null>(null);
  const [packaged, setPackaged] = useState(false);
  const [values, setValues] = useState<Record<string, any>>({});
  const [dirty, setDirty] = useState<Record<string, boolean>>({});
  const [open, setOpen] = useState<Record<string, boolean>>({}); // 그룹 펼침 상태

  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");
  const [warn, setWarn] = useState<string[]>([]);

  const [testing, setTesting] = useState<string>("");
  const [testMsg, setTestMsg] = useState<Record<string, { ok: boolean; message: string }>>({});
  const [diag, setDiag] = useState<DiagnoseResult | null>(null);
  const [localStt, setLocalStt] = useState<LocalSttStatus | null>(null);

  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [showNewProfile, setShowNewProfile] = useState(false);
  const [newProfile, setNewProfile] = useState({ name: "", description: "", type: "meeting", language: "ko", translate: false });

  // 설정 검색(FR-SET-1) — 14그룹·수십 필드라 "어디 있더라"가 가장 흔한 막힘이다.
  const [query, setQuery] = useState("");

  // 화면 테마 — [설정]과 모바일 [더보기] 두 곳에서 바꾼다(사용자 확정).
  const [theme, setTheme] = useState<ThemeChoice>(getThemeChoice);

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
        // scalar: section 자체가 값 / key 에 점이 있으면 중첩 경로(예: slack.webhook_url)
        const raw = f.key ? getNested(cfg?.[f.section], f.key) : cfg?.[f.section];
        v[pathOf(f)] = raw ?? f.default ?? (f.type === "bool" || f.type === "boolean" ? false : "");
      }
    }
    // 모바일: 키는 localStorage 에 별도 저장
    if (!pm) {
      v["api.openai_api_key"] = getApiKey();
      v["api.anthropic_api_key"] = getAnthropicKey();
    }
    setValues(v);
    setDirty({});
    // 그룹 펼침 초기화: 필수는 펼침, 고급은 접힘
    setOpen(Object.fromEntries(sch.map((g) => [g.id, !g.advanced])));
    setProfiles(await getProfiles());
    if (pm) setLocalStt(await localSttStatus());
  };

  useEffect(() => { load(); }, []);

  const setField = (path: string, val: any) => {
    setValues((prev) => ({ ...prev, [path]: val }));
    setDirty((prev) => ({ ...prev, [path]: true }));
  };

  // 저장 전 가벼운 형식 검증. errors 는 저장 차단, warnings 는 경고 후 진행.
  const validateBeforeSave = (): { errors: string[]; warnings: string[] } => {
    const errors: string[] = [];
    const warnings: string[] = [];
    if (!schema) return { errors, warnings };
    const val = (p: string) => (values[p] ?? "").toString().trim();

    for (const g of schema) {
      for (const f of g.fields) {
        if (f.required && !val(pathOf(f))) warnings.push(`'${f.label}' 이(가) 비어 있습니다. 이 값이 없으면 정상 동작하지 않을 수 있어요.`);
      }
    }
    const emailRe = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
    for (const p of ["email.sender", "email.recipient"]) {
      const v = val(p);
      if (v && !emailRe.test(v)) errors.push(`이메일 형식이 올바르지 않습니다: ${v}`);
    }
    const port = val("email.smtp_port");
    if (port) {
      if (!/^\d+$/.test(port)) errors.push("SMTP 포트는 숫자여야 합니다.");
      else {
        const n = Number(port);
        if (n !== 0 && (n < 1 || n > 65535)) errors.push("SMTP 포트는 1~65535 범위여야 합니다.");
      }
    }
    const key = val("api.openai_api_key");
    if (key && !key.includes("...") && !key.startsWith("sk-")) {
      warnings.push("OpenAI 키는 보통 'sk-' 로 시작합니다. 올바른 키인지 확인하세요.");
    }
    // 마스킹된 채 부분 수정된 민감 필드는 서버가 '변경 안 됨'으로 보고 조용히 무시한다
    // (settings.py: '...'/'***' 포함 값 스킵). 저장 전에 막아 사용자 혼란 방지.
    for (const g of schema) {
      for (const f of g.fields) {
        if (f.sensitive && dirty[pathOf(f)] && looksMasked(values[pathOf(f)])) {
          errors.push(`'${f.label}' 에 마스킹 기호(…/***)가 남아 있어 저장되지 않습니다. 눈 아이콘('보이기')으로 실제 값을 확인 후 전체를 다시 입력하거나, 바꿀 게 없으면 원래대로 두세요.`);
        }
      }
    }
    return { errors, warnings };
  };

  const handleSave = async (): Promise<boolean> => {
    if (!schema) return false;
    const { errors, warnings } = validateBeforeSave();
    setWarn(warnings);
    if (errors.length) {
      setError(errors.join(" "));
      return false;
    }
    setSaving(true);
    setError("");
    try {
      const bySection: Record<string, any> = {};
      const put = (section: string, key: string, val: any) => {
        if (key) { setNested((bySection[section] ||= {}), key, val); }
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
      return true;
    } catch (e: any) {
      setError(e?.message || "저장에 실패했습니다.");
      return false;
    } finally {
      setSaving(false);
    }
  };

  const runTest = async (kind: "openai" | "anthropic" | "obsidian" | "email" | "slack" | "teams") => {
    setTesting(kind);
    // 연결 테스트는 저장된 config.json 을 읽는다. 입력만 하고 [설정 저장]을 안 누른
    // 경우 '설정되지 않음'이 나오므로, 아직 저장 안 된 입력이 있으면 먼저 저장한다.
    if (Object.values(dirty).some(Boolean)) { if (!(await handleSave())) { setTesting(""); return; } }
    const res =
      kind === "openai" ? await testOpenAIKey() :
      kind === "anthropic" ? await testAnthropicKey() :
      kind === "email" ? await testEmail() :
      kind === "slack" ? await testSlack() :
      kind === "teams" ? await testTeams() :
      await testObsidianPath();
    setTestMsg((prev) => ({ ...prev, [kind]: res }));
    setTesting("");
  };

  const handleReindex = async () => {
    setTesting("reindex");
    if (Object.values(dirty).some(Boolean)) { if (!(await handleSave())) { setTesting(""); return; } }
    const res = await reindexVault();
    setTestMsg((prev) => ({ ...prev, reindex: res }));
    setTesting("");
  };

  // 로컬 백업 모델 가중치 미리 받기 — 수백 MB·수 분 걸릴 수 있어 진행 중 안내를 남긴다.
  // (전사 중에는 서버가 다운로드를 하지 않으므로 이 버튼이 유일한 준비 경로다.)
  const handlePrepareLocalStt = async () => {
    setTesting("localstt");
    if (Object.values(dirty).some(Boolean)) { if (!(await handleSave())) { setTesting(""); return; } }
    const res = await prepareLocalStt();
    setTestMsg((prev) => ({ ...prev, localstt: res }));
    setLocalStt(await localSttStatus());
    setTesting("");
  };

  const handleDiagnose = async () => {
    setTesting("diagnose");
    if (Object.values(dirty).some(Boolean)) { if (!(await handleSave())) { setTesting(""); return; } }
    setDiag(await obsidianDiagnose());
    setTesting("");
  };

  const openWizard = () => {
    localStorage.removeItem("ONBOARDING_DISMISSED");
    window.dispatchEvent(new CustomEvent("mm:open-onboarding"));
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
    // 이 저장은 config.json 전체를 넘긴 JSON 으로 대체한다 — 편집기에서 실수로 지운
    // 섹션(API 키 포함)이 그대로 사라진다. 앱의 다른 파괴적 동작은 모두 confirm 을
    // 거치는데 여기만 없었다.
    if (!confirm(
      "설정 파일 전체를 이 JSON 내용으로 대체합니다.\n" +
      "편집 중 빠뜨린 항목(API 키 등)은 함께 삭제됩니다.\n\n계속할까요?"
    )) return;
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
    // 대시보드의 [전체 삭제]와 **같은 문구**를 쓴다(FR-001: 두 진입점의 문구가 같아야 한다).
    // "되돌릴 수 없습니다"는 이제 사실이 아니다 — 삭제는 휴지통으로 보내는 것이다.
    if (!confirm("모든 회의 기록을 휴지통으로 보낼까요? 나중에 되돌릴 수 있습니다.")) return;
    await clearSessions();
    alert("휴지통으로 보냈습니다. [대시보드] → [휴지통]에서 되돌릴 수 있습니다.");
  };

  if (!schema) return null;

  // 화면 배치: 스키마 tier(core/common/advanced)로 3단 분리. tier가 없으면 advanced 여부로 폴백.
  // TIER_OVERRIDE 는 화면 표시 단계만 바꾼다(서버 스키마는 그대로).
  const tierOf = (g: Group): "core" | "common" | "advanced" =>
    TIER_OVERRIDE[g.id] || g.tier || (g.advanced ? "advanced" : "core");
  // 검색어가 있으면 걸린 그룹만, 그룹 안에서도 걸린 필드만 남긴다.
  const visible = schema
    .map((g) => matchesQuery(g, query))
    .filter((g): g is Group => !!g);
  const core = visible.filter((g) => tierOf(g) === "core");
  const common = visible.filter((g) => tierOf(g) === "common");
  const advanced = visible.filter((g) => tierOf(g) === "advanced");
  const searching = query.trim().length > 0;

  const renderGroup = (group: Group) => {
    // 검색 중에는 접힘을 무시한다 — 찾았는데 접혀 있으면 못 찾은 것과 같다.
    const isOpen = searching || (open[group.id] ?? tierOf(group) !== "advanced");
    return (
      <section key={group.id} className="bg-surface border border-line rounded-card mb-3 shadow-sm overflow-hidden">
        <button
          onClick={() => setOpen((p) => ({ ...p, [group.id]: !isOpen }))}
          className="w-full flex items-center gap-2 px-4 md:px-5 py-4 text-left hover:bg-hover transition-colors"
        >
          {isOpen ? <ChevronDown size={18} className="text-ink-3 shrink-0" /> : <ChevronRight size={18} className="text-ink-3 shrink-0" />}
          <Settings size={16} className="text-ink-3 shrink-0" />
          <span className="text-base font-bold text-ink">{group.label}</span>
        </button>

        {isOpen && (
          <div className="px-4 md:px-5 pb-5">
            {group.desc && <p className="text-xs text-ink-3 mb-3 -mt-1">{group.desc}</p>}
            <div className="space-y-3">
              {group.fields.map((f) => (
                <FieldRow key={pathOf(f)} field={f} value={values[pathOf(f)]} packaged={packaged} onChange={(v) => setField(pathOf(f), v)} />
              ))}
            </div>

            {/* 연결 테스트 버튼 (패키지 모드) */}
            {packaged && group.id === "api" && (
              <>
                <TestRow label="OpenAI 연결 테스트" busy={testing === "openai"} result={testMsg.openai} onClick={() => runTest("openai")} />
                <TestRow label="Claude 연결 테스트" busy={testing === "anthropic"} result={testMsg.anthropic} onClick={() => runTest("anthropic")} />
              </>
            )}
            {/* 로컬 STT 최종 백업 — 가중치 사전 준비 (OpenAI·Groq가 모두 죽었을 때의 마지막 수단) */}
            {packaged && group.id === "audio" && (
              <>
                <TestRow
                  label={`로컬 백업 모델 준비${localStt?.installed ? ` — 준비됨 (${localStt.model} · ${localStt.size_mb}MB)` : " — 미준비"}`}
                  busy={testing === "localstt"}
                  result={testMsg.localstt}
                  onClick={handlePrepareLocalStt}
                />
                <p className="text-xs text-ink-3 mt-1">
                  {localStt && !localStt.lib_available
                    ? "이 설치본에는 로컬 전사 라이브러리(faster-whisper)가 없습니다. 포터블 배포본에서는 기본 포함됩니다."
                    : "회의 중에 내려받지 않도록 미리 준비하세요. 모델 크기에 따라 수십~수백 MB, 1~3분 걸립니다. 준비하지 않으면 위 '로컬 STT 최종 백업'을 켜도 이 백업은 폴백 순서에서 자동으로 제외됩니다(전사 자체는 계속됩니다)."}
                </p>
              </>
            )}
            {packaged && group.id === "email" && (
              <>
                <TestRow label="메일 연결 테스트 (테스트 메일 발송)" busy={testing === "email"} result={testMsg.email} onClick={() => runTest("email")} />
                <p className="text-xs text-ink-3 mt-1">받는 주소로 테스트 메일 1통을 보내 설정을 확인합니다. 받은 편지함(스팸함 포함)을 확인하세요.</p>
              </>
            )}
            {packaged && group.id === "notify" && (
              <>
                <TestRow label="Slack 테스트 메시지 보내기" busy={testing === "slack"} result={testMsg.slack} onClick={() => runTest("slack")} />
                <TestRow label="Teams 테스트 메시지 보내기" busy={testing === "teams"} result={testMsg.teams} onClick={() => runTest("teams")} />
                <p className="text-xs text-ink-3 mt-1">각 Webhook URL을 입력·저장한 뒤 눌러 채널에 메시지가 도착하는지 확인하세요.</p>
              </>
            )}
            {packaged && group.id === "obsidian" && (
              <>
                <TestRow label="노트 폴더 경로 확인" busy={testing === "obsidian"} result={testMsg.obsidian} onClick={() => runTest("obsidian")} />
                <TestRow label="검색 인덱스·그래프 재빌드" busy={testing === "reindex"} result={testMsg.reindex} onClick={handleReindex} />
                <p className="text-xs text-ink-3 mt-1">노트 폴더(.md)를 바꾸거나 노트를 추가한 뒤 눌러 검색·위키·지식 그래프를 최신화하세요. (Obsidian 앱은 필요 없습니다.)</p>
                <div className="mt-5">
                  <button onClick={handleDiagnose} disabled={testing === "diagnose"} className="flex items-center gap-2 px-5 py-2.5 bg-surface-2 text-ink-2 rounded-card text-sm font-semibold hover:bg-hover transition-all w-fit">
                    {testing === "diagnose" ? <Loader2 size={16} className="animate-spin" /> : <Plug size={16} />} Obsidian 전체 진단
                  </button>
                  {diag && (
                    <div className="mt-3 space-y-1.5">
                      {diag.checks.map((ch) => (
                        <div key={ch.name} className="flex items-start gap-2 text-sm">
                          {ch.ok ? <CheckCircle size={16} className="text-ok mt-0.5 shrink-0" /> : <XCircle size={16} className="text-rec mt-0.5 shrink-0" />}
                          <span><b className="text-ink">{ch.name}</b> — <span className="text-ink-3">{ch.detail}</span></span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </>
            )}
          </div>
        )}
      </section>
    );
  };

  return (
    <div className="mx-auto w-full max-w-4xl px-1 md:px-0">
      <div className="flex items-start justify-between gap-3 mb-1">
        <h2 className="text-2xl font-bold tracking-tight">설정</h2>
        {packaged && (
          <button onClick={openWizard} className="flex items-center gap-1.5 px-3 py-1.5 bg-surface-2 text-ink-2 rounded-ctl text-xs font-semibold hover:bg-hover transition-all shrink-0">
            <Wand2 size={14} /> 설정 마법사 다시 열기
          </button>
        )}
      </div>
      <p className="text-sm text-ink-3 mb-4">
        {packaged
          ? "모든 설정은 이 PC의 config.json 에 저장됩니다. 잘 모르는 항목은 그대로 두세요."
          : "설정은 이 기기(브라우저)에만 저장됩니다."}
      </p>

      {/* 설정 검색 — 14그룹·수십 필드 중 어디에 있는지 찾는 것이 가장 흔한 막힘이다.
          라벨뿐 아니라 설명과 키(ssl.verify)도 함께 본다. */}
      <div className="mb-3">
        <Input aria-label="설정 검색" className="w-full" value={query} onChange={(e) => setQuery(e.target.value)}
          placeholder="설정 검색 — 예: 노트 폴더, 페르소나, SSL, 지출 한도" />
        {searching && (
          <p className="mt-1 text-xs text-ink-3">
            {core.length + common.length + advanced.length}개 그룹에서 찾았습니다.
            {" "}<button type="button" onClick={() => setQuery("")}
              className="underline hover:text-ink">검색 지우기</button>
          </p>
        )}
      </div>

      {/* 화면 테마 — 스키마 필드가 아니라 이 기기의 표시 설정이라 여기에 직접 둔다
          (config.json 이 아니라 localStorage). 모바일은 [더보기]에도 같은 컨트롤이 있다. */}
      {!searching && (
        <section className="mb-3 rounded-card border border-line bg-surface p-3 shadow-card">
          <h3 className="mb-1.5 text-md font-semibold text-ink">화면 테마</h3>
          <SegmentedControl id="settings-theme" label="화면 테마" value={theme}
            onChange={(c) => { setTheme(c); setThemeChoice(c); }}
            items={[
              { key: "system" as const, label: "시스템 설정 따름" },
              { key: "light" as const, label: "라이트" },
              { key: "dark" as const, label: "다크" },
            ]} />
          <p className="mt-1.5 text-xs text-ink-3">
            이 기기에만 저장됩니다(설정 파일과 무관). 기본값은 Windows 의 앱 모드를 따릅니다.
          </p>
        </section>
      )}

      {/* PC 서버 연결 (네이티브 앱 전용) — 연결/해제 시 전체 설정을 다시 로드해
          서버 모드 ↔ 단독 모드 전환을 즉시 반영한다. */}
      {IS_NATIVE && <BackendConnectionCard onChanged={load} />}

      {/* 1단 — 꼭 확인 (펼침) */}
      {core.length > 0 && (
        <>
          <SectionHeader label="꼭 확인" hint="시작에 반드시 확인하세요" first />
          {core.map(renderGroup)}
        </>
      )}

      {/* 2단 — 자주 쓰는 선택 (펼침) */}
      {common.length > 0 && (
        <>
          <SectionHeader label="자주 쓰는 선택" hint="필요하면 채우세요 — 없어도 회의록 생성엔 지장 없음" />
          {common.map(renderGroup)}
        </>
      )}

      {/* 3단 — 고급 (기본 접힘) */}
      {advanced.length > 0 && (
        <>
          <SectionHeader label="고급" hint="안 써도 됩니다 — 기본값 그대로 둬도 잘 동작해요" />
          {advanced.map((g) => (
            <React.Fragment key={g.id}>
              {renderGroup(g)}
              {/* 페르소나별 참견도는 8×6 매트릭스라 스키마 필드로는 못 쓴다 —
                  해당 그룹 바로 아래에 전용 카드로 붙인다(PRD §19.6). */}
              {g.id === "facilitation" && (open[g.id] ?? !g.advanced) && <PersonaMatrix />}
            </React.Fragment>
          ))}
        </>
      )}

      {searching && core.length + common.length + advanced.length === 0 && (
        <p className="rounded-card border border-line bg-surface px-3 py-6 text-center text-sm text-ink-3">
          “{query}” 와 맞는 설정이 없습니다. 다른 말로 찾아보세요(예: 폴더, 키, 한도, 알림).
        </p>
      )}

      {error && (
        <div role="alert" className="mb-3 rounded-card border border-rec bg-rec-bg text-rec px-4 py-3 text-sm flex items-start gap-2">
          <XCircle size={16} className="mt-0.5 shrink-0" /> <span>{error}</span>
        </div>
      )}
      {warn.length > 0 && (
        <div className="mb-4 rounded-card border border-warn-line bg-warn-bg text-warn px-4 py-3 text-sm">
          <div className="flex items-center gap-2 font-semibold mb-1"><AlertTriangle size={16} /> 확인이 필요합니다(저장은 되었습니다)</div>
          <ul className="list-disc ml-6 space-y-0.5">{warn.map((w, i) => <li key={i}>{w}</li>)}</ul>
        </div>
      )}

      {/* 항상 보이는 하단 고정 저장 바 — 어느 섹션에서든 바로 저장 */}
      <div className="sticky bottom-3 z-20 mb-8">
        <div className="flex flex-col md:flex-row md:items-center gap-2 bg-surface/85 backdrop-blur border border-line rounded-card p-3 shadow-2xl shadow-none">
          <button
            onClick={handleSave}
            disabled={saving}
            className="w-full md:w-auto flex items-center justify-center gap-2 px-8 py-3 bg-accent-solid text-white rounded-card font-bold hover:bg-accent-solid-hover transition-all active:scale-95"
          >
            {saving ? <Loader2 size={18} className="animate-spin" /> : saved ? <CheckCircle size={18} /> : <Save size={18} />}
            {saved ? "저장되었습니다!" : "설정 저장"}
          </button>
          <p className="text-xs text-ink-3 md:ml-1">
            변경 후 꼭 저장하세요. (연결 테스트·진단·재빌드는 자동으로 먼저 저장합니다.)
          </p>
        </div>
      </div>

      {/* 고급: 전체 설정(JSON) 직접 편집 */}
      {packaged && (
        <section className="bg-surface border border-line rounded-card p-4 md:p-5 mb-3 shadow-sm">
          <button onClick={toggleRaw} className="flex items-center gap-2 text-sm font-bold text-ink-2 hover:text-ink">
            <Settings size={16} /> 고급: 전체 설정(JSON) 직접 편집 {showRaw ? "▲" : "▼"}
          </button>
          {showRaw && (
            <div className="mt-3 space-y-2">
              <p className="text-xs text-ink-3">위 폼에 없는 항목(도메인 매핑·카테고리·별칭 등)까지 config.json 전체를 직접 편집합니다. 키는 마스킹되어 보이며 그대로 두면 유지됩니다.</p>
              <textarea
                value={rawText}
                onChange={(e) => setRawText(e.target.value)}
                spellCheck={false}
                className="w-full h-96 px-3 py-2 bg-accent-solid text-surface-2 border border-ink-2 rounded-ctl outline-none font-mono text-xs leading-relaxed resize-y"
              />
              {rawMsg && (
                <div className={`text-sm ${rawMsg.ok ? "text-ok" : "text-rec"}`}>{rawMsg.text}</div>
              )}
              <button onClick={saveRaw} className="flex items-center gap-2 px-5 py-2.5 bg-accent-solid text-white rounded-card text-sm font-bold hover:bg-accent-solid-hover transition-all">
                <Save size={16} /> 전체 설정 저장
              </button>
            </div>
          )}
        </section>
      )}

      {/* 폴더 자동 감시 (패키지 모드) */}
      {packaged && <WatcherCard />}

      {/* Profiles */}
      <section className="bg-surface border border-line rounded-card p-4 md:p-5 mb-3">
        <div className="flex items-center justify-between mb-6">
          <h3 className="text-lg font-bold">처리 프로필</h3>
          <button
            onClick={() => setShowNewProfile(!showNewProfile)}
            className="flex items-center gap-2 px-4 py-2 bg-surface-2 text-ink-2 rounded-card text-sm font-medium hover:bg-hover transition-all"
          >
            <Plus size={14} /> 새 프로필
          </button>
        </div>

        <AnimatePresence>
          {showNewProfile && (
            <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="overflow-hidden mb-6">
              <div className="p-6 bg-surface-2 rounded-card space-y-4 border border-line">
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <input type="text" value={newProfile.name} onChange={(e) => setNewProfile((p) => ({ ...p, name: e.target.value }))} placeholder="프로필 이름 (예: weekly_team)" className="px-4 py-2 border border-line rounded-ctl outline-none focus:ring-2 focus:ring-accent text-sm" />
                  <input type="text" value={newProfile.description} onChange={(e) => setNewProfile((p) => ({ ...p, description: e.target.value }))} placeholder="설명" className="px-4 py-2 border border-line rounded-ctl outline-none focus:ring-2 focus:ring-accent text-sm" />
                </div>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                  <select value={newProfile.type} onChange={(e) => setNewProfile((p) => ({ ...p, type: e.target.value }))} className="px-3 py-2 border border-line rounded-ctl text-sm bg-surface">
                    <option value="meeting">회의</option>
                    <option value="seminar">세미나</option>
                    <option value="lecture">강의</option>
                  </select>
                  <select value={newProfile.language} onChange={(e) => setNewProfile((p) => ({ ...p, language: e.target.value }))} className="px-3 py-2 border border-line rounded-ctl text-sm bg-surface">
                    <option value="ko">한국어</option>
                    <option value="en">English</option>
                  </select>
                  <label className="flex items-center gap-2 text-sm ml-2">
                    <input type="checkbox" checked={newProfile.translate} onChange={(e) => setNewProfile((p) => ({ ...p, translate: e.target.checked }))} className="w-4 h-4 rounded border-line-strong text-ink focus:ring-accent" />
                    영어 → 한국어 번역
                  </label>
                </div>
                <button onClick={handleCreateProfile} className="w-full md:w-auto px-6 py-2.5 bg-accent-solid text-white rounded-ctl text-sm font-semibold hover:bg-accent-solid-hover transition-all mt-2">프로필 생성</button>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        <div className="space-y-3">
          {profiles.map((p) => (
            <div key={p.name} className="flex flex-col md:flex-row md:items-center justify-between p-4 bg-surface-2 border border-line rounded-card gap-3">
              <div>
                <span className="font-bold text-sm text-ink">{p.name}</span>
                <span className="text-xs text-ink-3 ml-3">{p.description}</span>
                <span className="text-[11px] text-ink-3 font-bold ml-2 bg-surface-2 px-2 py-0.5 rounded-md">
                  {p.source === "builtin" ? "기본 제공" : "직접 추가"}
                </span>
              </div>
              <div className="flex items-center gap-3 text-xs text-ink-3 font-medium">
                <span className="bg-surface px-2 py-1 rounded shadow-sm">{typeLabel(p.type)}</span>
                <span className="bg-surface px-2 py-1 rounded shadow-sm">
                  {p.language === "ko" ? "한국어" : p.language === "en" ? "영어" : p.language}
                </span>
                {p.translate && <span className="bg-warn-bg text-warn px-2 py-1 rounded shadow-sm">번역</span>}
                {p.source !== "builtin" && (
                  <button
                    onClick={() => handleDeleteProfile(p.name)}
                    title="이 프로필 삭제"
                    aria-label={`${p.name} 프로필 삭제`}
                    className="p-1.5 hover:text-rec hover:bg-rec-bg rounded-ctl transition-colors ml-1"
                  >
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
        <section className="bg-surface border border-line rounded-card p-4 md:p-5 mb-3 shadow-sm">
          <h3 className="text-base font-bold mb-1">앱 종료</h3>
          <p className="text-xs text-ink-3 mb-3">프로그램을 완전히 종료합니다. 종료 후 이 브라우저 탭은 닫으세요. 다시 쓰려면 MeetingMinutes.exe 를 다시 실행하세요.</p>
          <button
            onClick={async () => {
              if (!confirm("앱을 종료할까요? 진행 중인 처리가 있으면 중단됩니다.")) return;
              await shutdownApp();
              alert("앱이 종료되었습니다. 이 탭을 닫으세요.");
            }}
            className="flex items-center gap-2 px-5 py-2.5 bg-ink text-white rounded-card text-sm font-bold hover:bg-accent-solid transition-all"
          >
            앱 종료
          </button>
        </section>
      )}

      {/* Danger Zone */}
      <section className="bg-surface border border-rec rounded-card p-6 md:p-8">
        <h3 className="text-lg font-bold text-rec mb-2">위험 구역</h3>
        <p className="text-sm text-rec/80 mb-5">
          모든 세션·전사·요약을 휴지통으로 보냅니다. [대시보드] → [휴지통]에서 되돌리거나
          완전히 삭제할 수 있습니다(완전 삭제는 결과 폴더를 Windows 휴지통으로 보냅니다).
        </p>
        <button onClick={handleClearHistory} className="flex items-center justify-center w-full md:w-auto gap-2 px-6 py-3 bg-rec-bg text-rec border border-rec rounded-card font-bold hover:bg-rec-bg transition-all">
          <Trash2 size={16} /> 모든 기록 휴지통으로 보내기
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

  const onAddFolder = async () => {
    setBusy(true); setMsg("");
    const r = await pickFolder();
    if (!r.ok || !r.path) {
      if (r.message && !r.cancelled) setMsg(r.message);
      setBusy(false);
      return;
    }
    const next = Array.from(new Set([...(status?.folders || []), r.path]));
    try {
      await updateConfig({ vault_watcher: { watch_folders: next } });
      setMsg(running ? "감시 폴더가 추가되었습니다. 반영하려면 '감시 중지' 후 다시 시작하세요." : "감시 폴더가 추가되었습니다.");
    } catch (e: any) {
      setMsg(`추가 실패: ${e?.message || e}`);
    }
    await refresh();
    setBusy(false);
  };

  const onRemoveFolder = async (folder: string) => {
    setBusy(true); setMsg("");
    const next = (status?.folders || []).filter((f) => f !== folder);
    try {
      await updateConfig({ vault_watcher: { watch_folders: next } });
      setMsg("감시 폴더가 제거되었습니다.");
    } catch (e: any) {
      setMsg(`제거 실패: ${e?.message || e}`);
    }
    await refresh();
    setBusy(false);
  };

  const running = !!status?.running;
  const c = status?.counts;

  return (
    <section className="bg-surface border border-line rounded-card p-4 md:p-5 mb-3 shadow-sm">
      <h3 className="text-base font-bold mb-1 flex items-center gap-2 text-ink">
        <Settings size={16} /> 폴더 자동 감시
        <span className={`ml-1 text-[11px] font-bold px-2 py-0.5 rounded-full ${running ? "bg-ok text-ok" : "bg-surface-2 text-ink-3"}`}>
          {running ? "● 감시 중" : "○ 중지됨"}
        </span>
      </h3>
      <p className="text-xs text-ink-3 mb-1">
        지정한 폴더에 새 녹음 파일이 생기면 자동으로 회의록을 생성합니다. 아래에서 감시할 폴더를 추가하세요.
      </p>
      {/*
        녹취 고지 — Recorder·FileUpload 와 같은 톤의 정적 한 줄. 자동 처리 경로라
        처리 시점에 사람이 없으므로, 폴더를 **지정하는** 이 자리가 유일한 고지 지점이다.
      */}
      <p className="text-xs text-ink-3 mb-3">
        감시 폴더에 넣는 녹음은 <b>참석자에게 녹음·자동 전사 사실을 알린 뒤</b> 취득한 것이어야 합니다.
      </p>

      {/* 감시 폴더 목록 + 추가/삭제 */}
      <div className="mb-3">
        <div className="flex items-center justify-between mb-1.5">
          <span className="text-xs font-bold text-ink-2">감시 폴더</span>
          <button onClick={onAddFolder} disabled={busy} className="flex items-center gap-1.5 px-3 py-1.5 bg-surface-2 text-ink-2 rounded-ctl text-xs font-semibold hover:bg-hover transition-all">
            {busy ? <Loader2 size={13} className="animate-spin" /> : <FolderOpen size={13} />} 폴더 추가
          </button>
        </div>
        {status?.folders && status.folders.length > 0 ? (
          <ul className="space-y-1">
            {status.folders.map((f) => (
              <li key={f} className="flex items-center justify-between gap-2 bg-surface-2 border border-line rounded-ctl px-3 py-2 text-xs">
                <span className="font-mono text-ink-2 truncate">{f}</span>
                <button onClick={() => onRemoveFolder(f)} disabled={busy} className="p-1 text-ink-3 hover:text-rec hover:bg-rec-bg rounded transition-colors shrink-0">
                  <Trash2 size={14} />
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <div className="text-xs text-warn bg-warn-bg border border-warn-line rounded-ctl px-3 py-2">감시 폴더가 없습니다. '폴더 추가'로 녹음 파일이 쌓이는 폴더를 지정하세요.</div>
        )}
      </div>

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
          <div className="font-bold text-ink-2 mb-1">최근 처리</div>
          <div className="space-y-1">
            {status.recent.slice(0, 5).map((r, i) => (
              <div key={i} className="flex items-center gap-2 text-ink-3">
                <span className={`w-1.5 h-1.5 rounded-full ${r.status === "done" ? "bg-ok" : r.status === "failed" ? "bg-rec" : "bg-idle"}`} />
                <span className="font-mono truncate max-w-[16rem]">{r.file}</span>
                <span className="text-ink-3">{r.processed_at?.replace("T", " ")}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="flex items-center gap-3">
        {running ? (
          <button onClick={onStop} disabled={busy} className="flex items-center gap-2 px-5 py-2.5 bg-ink text-white rounded-card text-sm font-bold hover:bg-accent-solid transition-all">
            {busy ? <Loader2 size={16} className="animate-spin" /> : <XCircle size={16} />} 감시 중지
          </button>
        ) : (
          <button onClick={onStart} disabled={busy} className="flex items-center gap-2 px-5 py-2.5 bg-accent-solid text-white rounded-card text-sm font-bold hover:bg-accent-solid-hover transition-all">
            {busy ? <Loader2 size={16} className="animate-spin" /> : <Plug size={16} />} 감시 시작
          </button>
        )}
      </div>
      {msg && <p className="text-xs text-ink-3 mt-2">{msg}</p>}
    </section>
  );
}

function Stat({ label, value, tone }: { label: string; value: number; tone: string }) {
  const tones: Record<string, string> = {
    emerald: "bg-ok-bg text-ok",
    sky: "bg-surface-2 text-ent-org",
    red: "bg-rec-bg text-rec",
    zinc: "bg-surface-2 text-ink-2",
  };
  return (
    <span className={`px-2.5 py-1 rounded-ctl font-bold ${tones[tone] || tones.zinc}`}>
      {label} {value}
    </span>
  );
}

function FieldRow({ field, value, onChange, packaged }: { field: Field; value: any; onChange: (v: any) => void; packaged: boolean }) {
  const [reveal, setReveal] = useState(false);
  const [picking, setPicking] = useState(false);
  // 라벨-입력 연결. <label>이 형제로만 놓여 있어 설정 화면의 모든 입력에 접근 가능한
  // 이름이 없었다 — 스크린리더는 "편집 가능, 빈칸"만 읽는다. 렌더러가 이 한 곳이라
  // 여기만 고치면 설정 전체가 해결된다.
  const inputId = React.useId();
  const descId = React.useId();
  const describedBy = field.desc ? descId : undefined;
  // 민감 필드 '보이기': 서버에서 실제 값을 받아 화면에만 덮어씌운다(부모 value는
  // 마스킹 그대로 유지 → 수정 안 하면 저장 시 서버가 기존 값 보존). shown!==null 이면
  // 그 값을 입력창에 표시한다. revealNote는 LAN(모바일)에서 실제값 거부됐을 때 안내.
  const [shown, setShown] = useState<string | null>(null);
  const [revealBusy, setRevealBusy] = useState(false);
  const [revealNote, setRevealNote] = useState("");

  const toggleReveal = async () => {
    if (reveal) { setReveal(false); return; }
    const cur = shown ?? (value ?? "");
    if (looksMasked(cur)) {
      setRevealBusy(true);
      const real = await revealSecret(pathOf(field));
      setRevealBusy(false);
      if (real !== null) { setShown(real); setRevealNote(""); }
      else { setRevealNote("실제 키는 이 PC에서만 볼 수 있어요. 변경하려면 새 값을 전부 입력하세요."); }
    }
    setReveal(true);
  };

  if (field.type === "bool" || field.type === "boolean") {
    // 캐노니컬 Toggle(role=switch)을 쓴다. 종전 체크박스는 스크린리더가 "체크박스"로 읽고
    // 꺼짐 상태의 테두리가 배경 대비 3:1 에 못 미쳤다(PRD §5.4·§5.5). 스키마에 "boolean"
    // 으로 적힌 필드도 있어(analysis.minutes_vault_context) 두 표기를 함께 받는다 —
    // 그 필드는 여기 걸리지 않아 텍스트 입력으로 그려지고 있었다.
    return (
      <Toggle checked={!!value} onChange={onChange}
        label={field.label} description={field.desc} />
    );
  }

  if (field.type === "textarea") {
    // 자유 입력 여러 줄 문자열(예: 회의록 맞춤 지시). "list"와 달리 배열로 만들지 않고
    // 문자열 그대로 저장한다.
    return (
      <div className="space-y-2">
        <label htmlFor={inputId} className="text-sm font-medium text-ink">
          {field.label}
          {field.required && <span className="text-rec ml-0.5" aria-hidden="true">*</span>}
        </label>
        <textarea
          id={inputId}
          value={value ?? ""}
          onChange={(e) => onChange(e.target.value)}
          placeholder={field.placeholder || ""}
          rows={5}
          aria-required={field.required || undefined}
          aria-describedby={describedBy}
          className="w-full px-3 py-2 bg-surface-2 border border-line rounded-ctl outline-none focus:ring-2 focus:ring-accent text-sm resize-y leading-relaxed"
        />
        {field.desc && <p id={descId} className="text-xs text-ink-3">{field.desc}</p>}
      </div>
    );
  }

  const showPicker = field.picker && packaged;
  const doPick = async () => {
    setPicking(true);
    const r = await pickFolder(typeof value === "string" ? value : "");
    if (r.ok && r.path) onChange(r.path);
    setPicking(false);
  };

  if (field.type === "list") {
    // 저장된 값은 배열, 편집 중에는 줄바꿈 구분 문자열 — 서버가 저장 시 배열로 정규화한다.
    const text = Array.isArray(value) ? value.join("\n") : (value ?? "");
    const doPickAppend = async () => {
      setPicking(true);
      const r = await pickFolder("");
      if (r.ok && r.path) onChange(text.trim() ? `${text.replace(/\s+$/, "")}\n${r.path}` : r.path);
      setPicking(false);
    };
    return (
      <div className="space-y-2">
        <label htmlFor={inputId} className="text-sm font-medium text-ink">
          {field.label}
          {field.required && <span className="text-rec ml-0.5" aria-hidden="true">*</span>}
        </label>
        <div className="flex items-stretch gap-2">
          <textarea
            id={inputId}
            value={text}
            onChange={(e) => onChange(e.target.value)}
            placeholder={field.placeholder || ""}
            rows={3}
            aria-required={field.required || undefined}
            aria-describedby={describedBy}
            className="flex-1 px-3 py-2 bg-surface-2 border border-line rounded-ctl outline-none focus:ring-2 focus:ring-accent text-sm font-mono tracking-wide resize-y"
          />
          {showPicker && (
            <button type="button" onClick={doPickAppend} disabled={picking}
              className="flex items-center gap-1.5 px-3 bg-surface-2 text-ink-2 rounded-ctl text-sm font-semibold hover:bg-hover transition-all shrink-0 self-stretch"
              title="폴더 추가">
              {picking ? <Loader2 size={16} className="animate-spin" /> : <FolderOpen size={16} />}
              <span className="hidden md:inline">폴더 추가</span>
            </button>
          )}
        </div>
        {field.desc && <p id={descId} className="text-xs text-ink-3">{field.desc}</p>}
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <label htmlFor={inputId} className="text-sm font-medium text-ink">
        {field.label}
        {field.required && <span className="text-rec ml-0.5" aria-hidden="true">*</span>}
      </label>
      {field.type === "select" ? (
        <select id={inputId} value={value ?? ""} onChange={(e) => onChange(e.target.value)}
          aria-required={field.required || undefined} aria-describedby={describedBy}
          className="w-full px-3 py-2 bg-surface-2 border border-line rounded-ctl outline-none focus:ring-2 focus:ring-accent text-sm">
          {(field.options || []).map((o) => {
            const val = typeof o === "string" ? o : o.value;
            const lbl = typeof o === "string" ? o : o.label;
            return <option key={val} value={val}>{lbl}</option>;
          })}
        </select>
      ) : (
        <div className="flex items-stretch gap-2">
          <div className="relative flex-1">
            <input
              id={inputId}
              type={field.type === "password" && !reveal ? "password" : field.type === "number" ? "number" : "text"}
              value={field.type === "password" && shown !== null ? shown : (value ?? "")}
              onChange={(e) => { if (shown !== null) setShown(null); setRevealNote(""); onChange(e.target.value); }}
              placeholder={field.placeholder || (field.type === "password" && looksMasked(value) ? "변경하려면 새 값 입력 (그대로 두면 기존 유지)" : "")}
              aria-required={field.required || undefined}
              aria-describedby={describedBy}
              className={`w-full px-3 py-2 bg-surface-2 border border-line rounded-ctl outline-none focus:ring-2 focus:ring-accent text-sm font-mono tracking-wide ${field.type === "password" ? "pr-10" : ""}`}
            />
            {field.type === "password" && (
              // tabIndex=-1 제거 — 키보드 사용자도 키를 확인하며 입력할 수 있어야 한다
              <button type="button" onClick={toggleReveal} disabled={revealBusy}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-ink-3 hover:text-ink-2 p-1 disabled:opacity-50"
                aria-label={reveal ? "키 숨기기" : "키 표시"} aria-pressed={reveal}
                title={reveal ? "숨기기" : "표시"}>
                {revealBusy ? <Loader2 size={16} className="animate-spin" /> : reveal ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            )}
          </div>
          {showPicker && (
            <button type="button" onClick={doPick} disabled={picking}
              className="flex items-center gap-1.5 px-3 bg-surface-2 text-ink-2 rounded-ctl text-sm font-semibold hover:bg-hover transition-all shrink-0"
              title="폴더 찾아보기">
              {picking ? <Loader2 size={16} className="animate-spin" /> : <FolderOpen size={16} />}
              <span className="hidden md:inline">찾아보기</span>
            </button>
          )}
        </div>
      )}
      {revealNote && <p className="text-xs text-warn">{revealNote}</p>}
      {field.desc && <p id={descId} className="text-xs text-ink-3">{field.desc}</p>}
    </div>
  );
}

// 설정 3단(꼭 확인 / 자주 쓰는 선택 / 고급) 구분 헤더.
function SectionHeader({ label, hint, first }: { label: string; hint?: string; first?: boolean }) {
  return (
    <div className={`flex items-baseline gap-3 mb-3 ${first ? "mt-1" : "mt-7"}`}>
      <span className="text-xs font-bold text-ink-2 uppercase tracking-widest whitespace-nowrap">{label}</span>
      {hint && <span className="text-[11px] text-ink-3 whitespace-nowrap hidden sm:inline">{hint}</span>}
      <div className="h-px flex-1 bg-line self-center" />
    </div>
  );
}

function TestRow({ label, busy, result, onClick }: { label: string; busy: boolean; result?: { ok: boolean; message: string }; onClick: () => void }) {
  return (
    <div className="mt-5 flex flex-col md:flex-row md:items-center gap-3">
      <button onClick={onClick} disabled={busy} className="flex items-center gap-2 px-5 py-2.5 bg-surface-2 text-ink-2 rounded-card text-sm font-semibold hover:bg-hover transition-all w-fit">
        {busy ? <Loader2 size={16} className="animate-spin" /> : <Plug size={16} />} {label}
      </button>
      {result && (
        <span className={`flex items-center gap-1.5 text-sm ${result.ok ? "text-ok" : "text-rec"}`}>
          {result.ok ? <CheckCircle size={16} /> : <XCircle size={16} />} {result.message}
        </span>
      )}
    </div>
  );
}

// ── 중첩 키(점 표기) 헬퍼 — notify."slack.webhook_url" 같은 필드용 ──────────
function getNested(obj: any, dotted: string): any {
  if (obj == null) return undefined;
  if (!dotted.includes(".")) return obj[dotted];
  return dotted.split(".").reduce((acc, p) => (acc == null ? undefined : acc[p]), obj);
}
function setNested(obj: any, dotted: string, val: any): void {
  if (!dotted.includes(".")) { obj[dotted] = val; return; }
  const parts = dotted.split(".");
  let node = obj;
  for (const p of parts.slice(0, -1)) {
    if (typeof node[p] !== "object" || node[p] == null) node[p] = {};
    node = node[p];
  }
  node[parts[parts.length - 1]] = val;
}
