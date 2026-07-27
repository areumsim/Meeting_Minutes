import React, { useState } from "react";
import {
  Mic, Loader2, CheckCircle, XCircle, FolderOpen, ChevronRight, ChevronLeft, Eye, EyeOff, Sparkles,
} from "lucide-react";
import { motion, AnimatePresence } from "motion/react";
import { updateConfig, testOpenAIKey, testAnthropicKey, testEmail, pickFolder } from "../lib/api";

const TOTAL = 5;

// 첫 실행 설정 마법사 — 비개발자가 필수 3~4가지만 순서대로 마치도록 안내.
// 저장은 스텝별로 기존 updateConfig 를 재사용한다(신규 백엔드 불필요).
export default function Onboarding({ onClose }: { onClose: () => void }) {
  const [step, setStep] = useState(0);
  const [busy, setBusy] = useState(false);

  // Step 1 — OpenAI 키
  const [key, setKey] = useState("");
  const [reveal, setReveal] = useState(false);
  const [keyResult, setKeyResult] = useState<{ ok: boolean; message: string } | null>(null);

  // Step 2 — Anthropic(Claude) 키 (선택)
  const [anthropicKey, setAnthropicKey] = useState("");
  const [revealA, setRevealA] = useState(false);
  const [anthropicResult, setAnthropicResult] = useState<{ ok: boolean; message: string } | null>(null);

  // Step 3 — 저장 폴더
  const [outputDir, setOutputDir] = useState("");
  // Step 3 — Obsidian 볼트(선택)
  const [vault, setVault] = useState("");
  // Step 4 — 이메일(선택)
  const [sender, setSender] = useState("");
  const [password, setPassword] = useState("");
  const [emailResult, setEmailResult] = useState<{ ok: boolean; message: string } | null>(null);

  const dismiss = () => {
    localStorage.setItem("ONBOARDING_DISMISSED", "1");
    onClose();
  };

  const testKey = async () => {
    if (!key.trim()) { setKeyResult({ ok: false, message: "키를 입력하세요." }); return; }
    setBusy(true);
    try {
      await updateConfig({ api: { openai_api_key: key.trim() } });
      setKeyResult(await testOpenAIKey());
    } catch (e: any) {
      setKeyResult({ ok: false, message: e?.message || "저장 실패" });
    }
    setBusy(false);
  };

  const testClaudeNow = async () => {
    if (!anthropicKey.trim()) { setAnthropicResult({ ok: false, message: "키를 입력하세요." }); return; }
    setBusy(true);
    try {
      await updateConfig({ api: { anthropic_api_key: anthropicKey.trim() } });
      setAnthropicResult(await testAnthropicKey());
    } catch (e: any) {
      setAnthropicResult({ ok: false, message: e?.message || "저장 실패" });
    }
    setBusy(false);
  };

  const testEmailNow = async () => {
    if (!sender.trim() || !password.trim()) { setEmailResult({ ok: false, message: "보내는 주소와 앱 비밀번호를 입력하세요." }); return; }
    setBusy(true);
    try {
      await updateConfig({ email: { sender: sender.trim(), password: password.trim() } });
      setEmailResult(await testEmail());
    } catch (e: any) {
      setEmailResult({ ok: false, message: e?.message || "저장 실패" });
    }
    setBusy(false);
  };

  const pick = async (setter: (p: string) => void, initial: string) => {
    setBusy(true);
    const r = await pickFolder(initial);
    if (r.ok && r.path) setter(r.path);
    setBusy(false);
  };

  const next = async () => {
    setBusy(true);
    try {
      if (step === 0 && key.trim()) await updateConfig({ api: { openai_api_key: key.trim() } });
      if (step === 1 && anthropicKey.trim()) await updateConfig({ api: { anthropic_api_key: anthropicKey.trim() } });
      if (step === 2 && outputDir.trim()) await updateConfig({ output_dir: outputDir.trim() });
      if (step === 3 && vault.trim()) await updateConfig({ obsidian: { vault_path: vault.trim() } });
      if (step === 4 && (sender.trim() || password.trim())) {
        await updateConfig({ email: { sender: sender.trim(), password: password.trim() } });
      }
    } catch { /* 저장 실패는 무시하고 진행 — 나중에 [설정]에서 재입력 가능 */ }
    setBusy(false);
    if (step < TOTAL - 1) setStep(step + 1);
    else dismiss();
  };

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-brand-950/50 backdrop-blur-sm p-4">
      <motion.div
        initial={{ opacity: 0, scale: 0.96, y: 10 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        className="w-full max-w-lg bg-white rounded-3xl shadow-2xl overflow-hidden"
      >
        {/* Header */}
        <div className="bg-brand-950 text-white px-6 py-5 flex items-center gap-3">
          <div className="w-10 h-10 bg-white/15 rounded-xl flex items-center justify-center shrink-0">
            <Mic size={20} />
          </div>
          <div>
            <h2 className="font-bold text-lg leading-tight">AI Minutes 시작하기</h2>
            <p className="text-xs text-white/70">{step + 1} / {TOTAL} 단계</p>
          </div>
          <button onClick={dismiss} className="ml-auto text-xs text-white/70 hover:text-white underline underline-offset-2">나중에 하기</button>
        </div>

        {/* Progress */}
        <div className="h-1 bg-brand-100">
          <div className="h-full bg-emerald-500 transition-all duration-300" style={{ width: `${((step + 1) / TOTAL) * 100}%` }} />
        </div>

        {/* Body */}
        <div className="p-6 min-h-[19rem]">
          <AnimatePresence mode="wait">
            <motion.div key={step} initial={{ opacity: 0, x: 12 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -12 }} transition={{ duration: 0.18 }}>
              {step === 0 && (
                <div className="space-y-3">
                  <StepTitle icon={<Sparkles size={18} />} title="OpenAI API 키 (필수)" />
                  <p className="text-sm text-brand-500">음성 인식과 회의록 생성에 사용됩니다. 키는 이 PC에만 저장됩니다.</p>
                  <div className="relative">
                    <input
                      type={reveal ? "text" : "password"}
                      value={key}
                      onChange={(e) => { setKey(e.target.value); setKeyResult(null); }}
                      placeholder="sk-proj-..."
                      className="w-full px-3 py-2.5 pr-10 bg-zinc-50 border border-zinc-200 rounded-lg outline-none focus:ring-2 focus:ring-brand-500 text-sm font-mono"
                    />
                    <button type="button" onClick={() => setReveal((s) => !s)} tabIndex={-1} className="absolute right-2 top-1/2 -translate-y-1/2 text-brand-400 hover:text-brand-700 p-1">
                      {reveal ? <EyeOff size={16} /> : <Eye size={16} />}
                    </button>
                  </div>
                  <div className="flex items-center gap-3">
                    <button onClick={testKey} disabled={busy} className="flex items-center gap-2 px-4 py-2 bg-brand-50 text-brand-700 rounded-lg text-sm font-semibold hover:bg-brand-100 transition-all">
                      {busy ? <Loader2 size={16} className="animate-spin" /> : <CheckCircle size={16} />} 연결 테스트
                    </button>
                    {keyResult && (
                      <span className={`flex items-center gap-1.5 text-sm ${keyResult.ok ? "text-emerald-600" : "text-red-600"}`}>
                        {keyResult.ok ? <CheckCircle size={16} /> : <XCircle size={16} />} {keyResult.message}
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-brand-400">
                    키가 없으신가요? <a href="https://platform.openai.com/api-keys" target="_blank" rel="noreferrer" className="text-brand-700 underline underline-offset-2">platform.openai.com/api-keys</a> 에서 발급하세요.
                  </p>
                </div>
              )}

              {step === 1 && (
                <div className="space-y-3">
                  <StepTitle icon={<Sparkles size={18} />} title="Claude(Anthropic) API 키 (선택)" />
                  <p className="text-sm text-brand-500">회의록을 Claude로 만들고 싶을 때만 입력하세요. 안 쓰면 건너뛰어도 됩니다(OpenAI 키만으로 모든 기능이 동작합니다).</p>
                  <div className="relative">
                    <input
                      type={revealA ? "text" : "password"}
                      value={anthropicKey}
                      onChange={(e) => { setAnthropicKey(e.target.value); setAnthropicResult(null); }}
                      placeholder="sk-ant-..."
                      className="w-full px-3 py-2.5 pr-10 bg-zinc-50 border border-zinc-200 rounded-lg outline-none focus:ring-2 focus:ring-brand-500 text-sm font-mono"
                    />
                    <button type="button" onClick={() => setRevealA((s) => !s)} tabIndex={-1} className="absolute right-2 top-1/2 -translate-y-1/2 text-brand-400 hover:text-brand-700 p-1">
                      {revealA ? <EyeOff size={16} /> : <Eye size={16} />}
                    </button>
                  </div>
                  <div className="flex items-center gap-3">
                    <button onClick={testClaudeNow} disabled={busy} className="flex items-center gap-2 px-4 py-2 bg-brand-50 text-brand-700 rounded-lg text-sm font-semibold hover:bg-brand-100 transition-all">
                      {busy ? <Loader2 size={16} className="animate-spin" /> : <CheckCircle size={16} />} 연결 테스트
                    </button>
                    {anthropicResult && (
                      <span className={`flex items-center gap-1.5 text-sm ${anthropicResult.ok ? "text-emerald-600" : "text-red-600"}`}>
                        {anthropicResult.ok ? <CheckCircle size={16} /> : <XCircle size={16} />} {anthropicResult.message}
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-brand-400">
                    키 발급: <a href="https://console.anthropic.com/settings/keys" target="_blank" rel="noreferrer" className="text-brand-700 underline underline-offset-2">console.anthropic.com/settings/keys</a> · 사용하려면 [설정] → 모델에서 '회의록 생성 AI'를 Claude로 바꾸세요. (음성 인식은 항상 OpenAI 사용)
                  </p>
                </div>
              )}

              {step === 2 && (
                <div className="space-y-3">
                  <StepTitle icon={<FolderOpen size={18} />} title="결과물 저장 폴더" />
                  <p className="text-sm text-brand-500">완성된 회의록·요약·전사 파일이 저장될 폴더입니다. 잘 모르겠으면 비워 두세요(프로그램 옆 기본 폴더 사용).</p>
                  <PickerInput value={outputDir} onChange={setOutputDir} onPick={() => pick(setOutputDir, outputDir)} busy={busy} placeholder="기본값 사용 (예: D:\Minutes)" />
                </div>
              )}

              {step === 3 && (
                <div className="space-y-3">
                  <StepTitle icon={<FolderOpen size={18} />} title="Obsidian 볼트 폴더 (선택)" />
                  <p className="text-sm text-brand-500">Obsidian을 쓴다면 볼트(.md 폴더)를 지정하세요. 회의록이 볼트에 저장되고 위키 검색에 활용됩니다. 안 쓰면 건너뛰어도 됩니다.</p>
                  <PickerInput value={vault} onChange={setVault} onPick={() => pick(setVault, vault)} busy={busy} placeholder="예: D:\Obsidian\MyVault" />
                </div>
              )}

              {step === 4 && (
                <div className="space-y-3">
                  <StepTitle icon={<Sparkles size={18} />} title="이메일 자동 발송 (선택)" />
                  <p className="text-sm text-brand-500">회의록이 완성되면 메일로 받고 싶을 때만 입력하세요. 안 쓰면 건너뛰세요.</p>
                  <div className="space-y-2">
                    <label className="text-xs font-medium text-brand-600">보내는 메일 주소</label>
                    <input value={sender} onChange={(e) => setSender(e.target.value)} placeholder="myid@gmail.com" className="w-full px-3 py-2.5 bg-zinc-50 border border-zinc-200 rounded-lg outline-none focus:ring-2 focus:ring-brand-500 text-sm" />
                    <label className="text-xs font-medium text-brand-600">메일 앱 비밀번호</label>
                    <input type="password" value={password} onChange={(e) => { setPassword(e.target.value); setEmailResult(null); }} placeholder="앱 비밀번호(로그인 비번 아님)" className="w-full px-3 py-2.5 bg-zinc-50 border border-zinc-200 rounded-lg outline-none focus:ring-2 focus:ring-brand-500 text-sm font-mono" />
                    <div className="flex items-center gap-3 pt-1">
                      <button onClick={testEmailNow} disabled={busy} className="flex items-center gap-2 px-4 py-2 bg-brand-50 text-brand-700 rounded-lg text-sm font-semibold hover:bg-brand-100 transition-all">
                        {busy ? <Loader2 size={16} className="animate-spin" /> : <CheckCircle size={16} />} 테스트 메일 보내기
                      </button>
                      {emailResult && (
                        <span className={`flex items-center gap-1.5 text-sm ${emailResult.ok ? "text-emerald-600" : "text-red-600"}`}>
                          {emailResult.ok ? <CheckCircle size={16} /> : <XCircle size={16} />} {emailResult.message}
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-brand-400">평소 로그인 비밀번호가 아니라 메일 서비스 보안설정에서 발급하는 '앱 비밀번호'입니다. 자세한 안내는 [설정] → 이메일 항목을 참고하세요.</p>
                  </div>
                </div>
              )}
            </motion.div>
          </AnimatePresence>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between gap-2 px-6 py-4 border-t border-brand-100 bg-brand-50/40">
          <button
            onClick={() => setStep(Math.max(0, step - 1))}
            disabled={step === 0 || busy}
            className="flex items-center gap-1 px-3 py-2 text-sm text-brand-500 hover:text-brand-900 disabled:opacity-0 transition-all"
          >
            <ChevronLeft size={16} /> 이전
          </button>
          <div className="flex items-center gap-2">
            {step > 0 && step < TOTAL - 1 && (
              <button onClick={() => (step < TOTAL - 1 ? setStep(step + 1) : dismiss())} disabled={busy} className="px-4 py-2 text-sm text-brand-500 hover:text-brand-900 transition-all">
                건너뛰기
              </button>
            )}
            <button onClick={next} disabled={busy} className="flex items-center gap-2 px-6 py-2.5 bg-brand-950 text-white rounded-xl text-sm font-bold hover:bg-brand-900 transition-all active:scale-95">
              {busy ? <Loader2 size={16} className="animate-spin" /> : step === TOTAL - 1 ? <CheckCircle size={16} /> : <ChevronRight size={16} />}
              {step === TOTAL - 1 ? "완료" : "다음"}
            </button>
          </div>
        </div>
      </motion.div>
    </div>
  );
}

function StepTitle({ icon, title }: { icon: React.ReactNode; title: string }) {
  return (
    <h3 className="flex items-center gap-2 text-lg font-bold text-brand-900">
      <span className="text-brand-500">{icon}</span> {title}
    </h3>
  );
}

function PickerInput({ value, onChange, onPick, busy, placeholder }: { value: string; onChange: (v: string) => void; onPick: () => void; busy: boolean; placeholder?: string }) {
  return (
    <div className="flex items-stretch gap-2">
      <input value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} className="flex-1 px-3 py-2.5 bg-zinc-50 border border-zinc-200 rounded-lg outline-none focus:ring-2 focus:ring-brand-500 text-sm font-mono" />
      <button type="button" onClick={onPick} disabled={busy} className="flex items-center gap-1.5 px-4 bg-brand-50 text-brand-700 rounded-lg text-sm font-semibold hover:bg-brand-100 transition-all shrink-0">
        {busy ? <Loader2 size={16} className="animate-spin" /> : <FolderOpen size={16} />} 찾아보기
      </button>
    </div>
  );
}
