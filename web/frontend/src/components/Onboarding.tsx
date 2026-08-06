import React, { useState } from "react";
import {
  Mic, Loader2, CheckCircle, XCircle, FolderOpen, ChevronRight, ChevronLeft, Eye, EyeOff, Sparkles,
} from "lucide-react";
import { motion } from "motion/react";
import { updateConfig, testOpenAIKey, testAnthropicKey, testEmail, pickFolder } from "../lib/api";
import Modal from "../ui/Modal";
import { Button, IconButton } from "../ui/Button";
import { Field, Input, TextField } from "../ui/Field";
import { ProgressBar } from "../ui/states";

const TOTAL = 5;

/** 헤더의 진행 안내에 함께 읽어 주는 단계 이름 — 숫자만으로는 어디인지 알 수 없다. */
const STEP_TITLES = [
  "OpenAI API 키", "Claude API 키(선택)", "결과물 저장 폴더",
  "노트 폴더(선택)", "이메일 자동 발송(선택)",
];

// 첫 실행 설정 마법사 — 비개발자가 필수 3~4가지만 순서대로 마치도록 안내.
// 저장은 스텝별로 기존 updateConfig 를 재사용한다(신규 백엔드 불필요).
export default function Onboarding({ onClose }: { onClose: () => void }) {
  const [step, setStep] = useState(0);
  const [busy, setBusy] = useState(false);

  // Step 1 — OpenAI 키
  const [key, setKey] = useState("");
  const [keyResult, setKeyResult] = useState<{ ok: boolean; message: string } | null>(null);

  // Step 2 — Anthropic(Claude) 키 (선택)
  const [anthropicKey, setAnthropicKey] = useState("");
  const [anthropicResult, setAnthropicResult] = useState<{ ok: boolean; message: string } | null>(null);

  // Step 3 — 저장 폴더
  const [outputDir, setOutputDir] = useState("");
  // Step 4 — Obsidian 볼트(선택)
  const [vault, setVault] = useState("");
  // Step 5 — 이메일(선택)
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
    // Escape = [나중에 하기]와 동일(파괴적이지 않다 — [설정]의 '설정 마법사 다시 열기'로
    // 언제든 다시 연다). 백드롭 클릭은 닫지 않는다 — 입력 중 오클릭으로 마법사가
    // 사라지면 어디까지 저장됐는지 알 수 없다.
    <Modal labelledBy="onboarding-title" onClose={dismiss}
      overlayClassName="fixed inset-0 z-100 flex items-center justify-center bg-black/45 backdrop-blur-sm p-4"
      panelClassName="w-full max-w-lg">
      <motion.div
        initial={{ opacity: 0, scale: 0.96, y: 10 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        className="w-full overflow-hidden rounded-card border border-line bg-surface shadow-pop"
      >
        {/* Header */}
        {/* 액센트 면 위의 글자는 text-on-accent 다 — 다크에서 액센트가 밝아지므로 흰 글자를
            박아 두면 대비가 1.7:1 로 무너진다(라이트에서만 맞는 색). */}
        <div className="flex items-center gap-3 bg-accent-solid px-5 py-4 text-on-accent">
          <span aria-hidden="true"
            className="grid h-9 w-9 shrink-0 place-items-center rounded-ctl bg-black/10">
            <Mic size={18} />
          </span>
          <div className="min-w-0">
            <h2 id="onboarding-title" className="text-lg font-bold leading-tight">AI Minutes 시작하기</h2>
            {/* 단계 전환은 시각으로만 바뀌었다 — 스크린리더에도 알린다. */}
            <p role="status" className="text-xs opacity-80">{step + 1} / {TOTAL} 단계 · {STEP_TITLES[step]}</p>
          </div>
          <button onClick={dismiss}
            className="ml-auto shrink-0 text-xs underline underline-offset-2 opacity-80 hover:opacity-100">
            나중에 하기
          </button>
        </div>

        <ProgressBar percent={((step + 1) / TOTAL) * 100} label="설정 마법사 진행률"
          className="rounded-none" />

        {/* Body */}
        <div className="min-h-[19rem] p-5">
          {/* 단계 전환에 AnimatePresence(mode="wait")를 쓰지 않는다 — 이전 단계의 퇴장을
              기다리느라 **본문이 헤더·진행바보다 늦게 바뀐다**. 빠르게 [다음]을 누르면
              헤더는 5/5 인데 본문은 1단계인 상태가 눈에 보였다. 마법사는 폼이라 즉시
              바뀌어야 하고, 이 애니메이션이 주는 이득은 그 위험보다 작다(Modal 과 같은 판단). */}
          <div key={step}>
              {step === 0 && (
                <div className="space-y-3">
                  <StepTitle icon={<Sparkles size={18} />} title="OpenAI API 키 (필수)" />
                  <p className="text-sm text-ink-3">음성 인식과 회의록 생성에 사용됩니다. 키는 이 PC에만 저장됩니다.</p>
                  <SecretField id="ob-openai" label="OpenAI API 키" placeholder="sk-proj-..."
                    value={key} onChange={(v) => { setKey(v); setKeyResult(null); }} />
                  <TestRow label="연결 테스트" busy={busy} result={keyResult} onClick={testKey} />
                  <p className="text-xs text-ink-3">
                    키가 없으신가요? <a href="https://platform.openai.com/api-keys" target="_blank" rel="noreferrer" className="text-ink-2 underline underline-offset-2">platform.openai.com/api-keys</a> 에서 발급하세요.
                  </p>
                </div>
              )}

              {step === 1 && (
                <div className="space-y-3">
                  <StepTitle icon={<Sparkles size={18} />} title="Claude(Anthropic) API 키 (선택)" />
                  <p className="text-sm text-ink-3">회의록을 Claude로 만들고 싶을 때만 입력하세요. 안 쓰면 건너뛰어도 됩니다(OpenAI 키만으로 모든 기능이 동작합니다).</p>
                  <SecretField id="ob-claude" label="Claude(Anthropic) API 키" placeholder="sk-ant-..."
                    value={anthropicKey}
                    onChange={(v) => { setAnthropicKey(v); setAnthropicResult(null); }} />
                  <TestRow label="연결 테스트" busy={busy} result={anthropicResult} onClick={testClaudeNow} />
                  <p className="text-xs text-ink-3">
                    키 발급: <a href="https://console.anthropic.com/settings/keys" target="_blank" rel="noreferrer" className="text-ink-2 underline underline-offset-2">console.anthropic.com/settings/keys</a> · 사용하려면 [설정] → 모델에서 '회의록 생성 AI'를 Claude로 바꾸세요. (음성 인식은 항상 OpenAI 사용)
                  </p>
                </div>
              )}

              {step === 2 && (
                <div className="space-y-3">
                  <StepTitle icon={<FolderOpen size={18} />} title="결과물 저장 폴더" />
                  <p className="text-sm text-ink-3">완성된 회의록·요약·전사 파일이 저장될 폴더입니다. 잘 모르겠으면 비워 두세요(프로그램 옆 기본 폴더 사용).</p>
                  <PickerInput value={outputDir} onChange={setOutputDir} onPick={() => pick(setOutputDir, outputDir)} busy={busy} placeholder="기본값 사용 (예: D:\Minutes)" label="결과물 저장 폴더" />
                </div>
              )}

              {step === 3 && (
                <div className="space-y-3">
                  <StepTitle icon={<FolderOpen size={18} />} title="Obsidian 볼트 폴더 (선택)" />
                  <p className="text-sm text-ink-3">Obsidian을 쓴다면 볼트(.md 폴더)를 지정하세요. 회의록이 볼트에 저장되고 위키 검색에 활용됩니다. 안 쓰면 건너뛰어도 됩니다.</p>
                  <PickerInput value={vault} onChange={setVault} onPick={() => pick(setVault, vault)} busy={busy} placeholder="예: D:\Obsidian\MyVault" label="Obsidian 볼트 폴더" />
                </div>
              )}

              {step === 4 && (
                <div className="space-y-3">
                  <StepTitle icon={<Sparkles size={18} />} title="이메일 자동 발송 (선택)" />
                  <p className="text-sm text-ink-3">회의록이 완성되면 메일로 받고 싶을 때만 입력하세요. 안 쓰면 건너뛰세요.</p>
                  <div className="space-y-2.5">
                    <TextField id="ob-email-sender" label="보내는 메일 주소" value={sender}
                      placeholder="myid@gmail.com" onChange={(e) => setSender(e.target.value)} />
                    <SecretField id="ob-email-password" label="메일 앱 비밀번호"
                      placeholder="앱 비밀번호(로그인 비번 아님)" value={password}
                      onChange={(v) => { setPassword(v); setEmailResult(null); }} />
                    <TestRow label="테스트 메일 보내기" busy={busy} result={emailResult} onClick={testEmailNow} />
                    <p className="text-xs text-ink-3">
                      평소 로그인 비밀번호가 아니라 메일 서비스 보안설정에서 발급하는 앱 비밀번호입니다.
                      자세한 안내는 [설정] 의 이메일 항목을 참고하세요.
                    </p>
                  </div>
                </div>
              )}
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between gap-2 border-t border-line bg-surface-2 px-5 py-3">
          <span className={step === 0 ? "invisible" : ""}>
            <Button variant="ghost" icon={ChevronLeft} disabled={busy}
              onClick={() => setStep(Math.max(0, step - 1))}>이전</Button>
          </span>
          <div className="flex items-center gap-2">
            {step > 0 && step < TOTAL - 1 && (
              <Button variant="ghost" disabled={busy}
                onClick={() => setStep(step + 1)}>건너뛰기</Button>
            )}
            <Button variant="primary" busy={busy} onClick={next}
              icon={step === TOTAL - 1 ? CheckCircle : ChevronRight}>
              {step === TOTAL - 1 ? "완료" : "다음"}
            </Button>
          </div>
        </div>
      </motion.div>
    </Modal>
  );
}

/** 비밀 키 한 줄 — 보이기 토글 포함. OpenAI·Claude 두 단계가 같은 모양이라 하나로 둔다. */
function SecretField({
  id, label, value, onChange, placeholder,
}: {
  id: string; label: string; value: string;
  onChange: (v: string) => void; placeholder: string;
}) {
  // 보이기 상태는 이 필드 안에만 있다 — 부모가 키마다 따로 들고 있을 이유가 없다.
  const [reveal, setReveal] = useState(false);
  return (
    <Field label={label} htmlFor={id}>
      <div className="relative">
        <Input id={id} type={reveal ? "text" : "password"} value={value} placeholder={placeholder}
          className="w-full pr-9 font-mono" onChange={(e) => onChange(e.target.value)} />
        <span className="absolute right-1 top-1/2 -translate-y-1/2">
          <IconButton icon={reveal ? EyeOff : Eye} size="sm" variant="ghost"
            label={reveal ? "키 숨기기" : "키 표시"} aria-pressed={reveal}
            onClick={() => setReveal((v) => !v)} />
        </span>
      </div>
    </Field>
  );
}

/** 연결 테스트 버튼 + 결과 한 줄. 결과는 색만이 아니라 아이콘·글자로 함께 낸다. */
function TestRow({
  label, busy, result, onClick,
}: {
  label: string; busy: boolean;
  result: { ok: boolean; message: string } | null; onClick: () => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button size="sm" variant="secondary" icon={CheckCircle} busy={busy} onClick={onClick}>
        {label}
      </Button>
      {result && (
        <span role="status"
          className={`flex items-center gap-1 text-sm ${result.ok ? "text-ok" : "text-rec"}`}>
          {result.ok ? <CheckCircle size={14} aria-hidden="true" /> : <XCircle size={14} aria-hidden="true" />}
          {result.message}
        </span>
      )}
    </div>
  );
}

function StepTitle({ icon, title }: { icon: React.ReactNode; title: string }) {
  return (
    <h3 className="flex items-center gap-2 text-lg font-bold text-ink">
      <span className="text-ink-3">{icon}</span> {title}
    </h3>
  );
}

function PickerInput({ value, onChange, onPick, busy, placeholder, label }: { value: string; onChange: (v: string) => void; onPick: () => void; busy: boolean; placeholder?: string; label?: string }) {
  return (
    <div className="flex items-stretch gap-2">
      <Input value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder}
        aria-label={label || "폴더 경로"} className="min-w-0 flex-1 font-mono" />
      <Button variant="secondary" icon={FolderOpen} busy={busy} onClick={onPick} className="shrink-0">
        찾아보기
      </Button>
    </div>
  );
}
