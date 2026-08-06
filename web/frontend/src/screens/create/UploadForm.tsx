import React, { useRef, useState } from "react";
import { Upload, FileAudio, X } from "lucide-react";
import { uploadFile, confirmUpload, cancelPendingUpload } from "../../lib/api";
import { MODE_PRESETS } from "../../lib/types";
import { Button, IconButton } from "../../ui/Button";
import { Banner } from "../../ui/Banner";
import CostConfirmModal from "../../ui/CostConfirmModal";
import MetaFields, { emptyMeta, type Meta } from "./MetaFields";
import ModePanel from "./ModePanel";

/** 서버가 받는 형식(batch.py). 화면 문구와 accept 속성이 갈라지지 않게 한 곳에 둔다. */
const FORMATS = ["mp3", "m4a", "wav", "mp4", "webm", "ogg", "flac", "avi", "mkv", "mov"];
const ACCEPT = "audio/*,video/*," + FORMATS.map((f) => `.${f}`).join(",");

/**
 * 파일 업로드 (PRD §6.2 · 매트릭스 1-B).
 *
 * 과금 관문은 **서버가 주도한다** — `POST /api/upload` 가 `confirm_required` 와 함께
 * 예상 금액·길이·월 지출·한도를 돌려주고, 화면은 그것을 그대로 보여준 뒤 [계속]에서
 * `confirm` 을 호출한다. 프런트가 금액을 계산하지 않는다.
 */
export default function UploadForm({ onComplete }: { onComplete: (id: string) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [meta, setMeta] = useState<Meta>(emptyMeta);
  const [modeNum, setModeNum] = useState(2);
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [error, setError] = useState("");
  // 서버가 confirm_required 를 준 상태(예상 비용 확인 대기)
  const [pending, setPending] = useState<{
    pendingId: string; estimateUsd: number; durationSec: number;
    monthToDateUsd: number; monthlyCapUsd: number;
  } | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const preset = MODE_PRESETS[modeNum] || MODE_PRESETS[2];

  const submit = async () => {
    if (!file) return;
    setUploading(true);
    setError("");

    const form = new FormData();
    form.append("file", file);
    form.append("title", meta.title || file.name);
    form.append("topic", meta.topic);
    form.append("type", preset.type);
    form.append("language", preset.language);
    form.append("translate", preset.translate.toString());
    form.append("speakers", meta.speakers);
    form.append("mode", modeNum.toString());

    try {
      const data = await uploadFile(form);
      if ("pendingId" in data) { setPending(data); setUploading(false); return; }
      onComplete(data.sessionId);
    } catch (err) {
      // 사무용 사용자에게 "콘솔을 확인하세요"는 실행 가능한 안내가 아니다.
      setError(err instanceof Error ? err.message
        : "서버에 연결할 수 없습니다. 프로그램이 실행 중인지 확인한 뒤 다시 시도해 주세요.");
      setUploading(false);
    }
  };

  const confirm = async () => {
    if (!pending) return;
    setUploading(true);
    try {
      const data = await confirmUpload(pending.pendingId);
      setPending(null);
      onComplete(data.sessionId);
    } catch (err) {
      setError(err instanceof Error ? err.message : "처리를 시작하지 못했습니다.");
      setPending(null);
      setUploading(false);
    }
  };

  const cancelPending = () => {
    if (!pending) return;
    void cancelPendingUpload(pending.pendingId);
    setPending(null);
    setUploading(false);
  };

  const fmtSize = (bytes: number) =>
    bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(0)} KB` : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;

  return (
    <div className="grid gap-3 lg:grid-cols-[1fr_290px]">
      {pending && (
        <CostConfirmModal
          what="이 파일을 처리하면 음성 인식·번역·회의록 생성에 API 비용이 듭니다."
          estimateUsd={pending.estimateUsd}
          durationSec={pending.durationSec}
          monthToDateUsd={pending.monthToDateUsd}
          monthlyCapUsd={pending.monthlyCapUsd}
          busy={uploading}
          onCancel={cancelPending}
          onConfirm={confirm}
        />
      )}

      <div className="space-y-3">
        {error && <Banner tone="err" title="업로드하지 못했습니다" onDismiss={() => setError("")}>{error}</Banner>}

        <div
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => {
            e.preventDefault(); setDragOver(false);
            const f = e.dataTransfer.files[0];
            if (f) setFile(f);
          }}
          className={`rounded-card border border-dashed p-6 text-center transition-colors ${
            dragOver ? "border-accent bg-accent-weak"
              : file ? "border-ok bg-ok-bg/40" : "border-line-strong bg-surface-2"}`}
        >
          <input ref={fileRef} type="file" accept={ACCEPT} className="hidden"
            onChange={(e) => e.target.files?.[0] && setFile(e.target.files[0])} />
          {file ? (
            <div className="flex items-center justify-center gap-3">
              <FileAudio size={22} className="shrink-0 text-ok" aria-hidden="true" />
              <span className="min-w-0">
                <span className="block truncate font-semibold text-ink">{file.name}</span>
                <span className="num text-xs text-ink-3">{fmtSize(file.size)}</span>
              </span>
              <IconButton icon={X} label="파일 선택 해제" size="sm" onClick={() => setFile(null)} />
            </div>
          ) : (
            <>
              <Upload size={22} className="mx-auto mb-1.5 text-ink-3" aria-hidden="true" />
              <p className="text-base font-semibold text-ink">오디오 파일을 끌어다 놓거나</p>
              <Button variant="secondary" size="sm" className="mt-1.5"
                onClick={() => fileRef.current?.click()}>파일 선택</Button>
              <p className="mt-2 text-xs text-ink-3">{FORMATS.join(" · ")}</p>
            </>
          )}
        </div>

        <MetaFields value={meta} onChange={setMeta} disabled={uploading}
          titlePlaceholder={file?.name || "회의 제목"} />

        {/* 녹취 고지 — 녹음 화면과 같은 톤·같은 자리(입력 아래 정적 한 줄). 모달·상시 배너는
            두지 않는다(매번 확인 강제는 실익 없이 사용자만 이탈시킨다). 진입점마다 최소
            1회는 보여야 한다: 이 도구는 몰래 녹음용이 아니다. */}
        <p className="text-xs text-ink-3">
          업로드하는 녹음은 <b>참석자에게 녹음·자동 전사 사실을 알린 뒤</b> 취득한 것이어야 합니다.
        </p>

        <Button variant="primary" icon={Upload} className="w-full" busy={uploading}
          disabled={!file} onClick={submit}>
          {uploading ? "처리 중…" : "분석 & 회의록 생성"}
        </Button>
      </div>

      <ModePanel modeNum={modeNum} onChange={setModeNum} disabled={uploading}
        hint="올린 파일을 전사·번역한 뒤 회의록을 만듭니다. 길이에 비례해 시간이 걸립니다." />
    </div>
  );
}
