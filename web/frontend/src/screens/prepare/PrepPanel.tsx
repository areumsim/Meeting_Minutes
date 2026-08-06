import React, { useState } from "react";
import { ClipboardList, Copy, Check, Save, FileText, AlertTriangle } from "lucide-react";
import Markdown from "../../components/Markdown";
import { prepBrief, savePrepBrief, type PrepBriefResult } from "../../lib/api";
import { submitOnEnter } from "../../lib/useSubmitOnEnter";
import { Button } from "../../ui/Button";
import { Banner } from "../../ui/Banner";
import { Field, Input, Textarea, TextField } from "../../ui/Field";
import { Tag } from "../../ui/StatusPill";

/**
 * 회의 준비 브리핑 — PRD FR-PRP-1.
 *
 * 저장 위치를 **실제 경로로** 적는다: 종전 버튼은 "대시보드에 저장"이었는데, 실제로는
 * 노트 폴더의 `Planning/` 아래 .md 로 저장되고 라이브러리에는 prep 세션으로 함께 보인다.
 * 사용자가 나중에 그 파일을 찾을 수 있어야 하므로 버튼이 그 사실을 말한다(리뷰 P2-6).
 */
export default function PrepPanel({ onSaved }: { onSaved?: (id: string) => void }) {
  const [title, setTitle] = useState("");
  const [date, setDate] = useState("");
  const [attendees, setAttendees] = useState("");
  const [topic, setTopic] = useState("");
  const [notes, setNotes] = useState("");
  const [meta, setMeta] = useState<PrepBriefResult | null>(null);
  const [brief, setBrief] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [savedId, setSavedId] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState("");

  const run = async () => {
    if (!title.trim() || loading) return;
    setLoading(true); setError(""); setBrief(""); setSavedId(null); setMeta(null);
    const res = await prepBrief(title.trim(), topic.trim(), {
      attendees: attendees.trim(), notes: notes.trim(),
    });
    if (res.ok && res.brief) { setBrief(res.brief); setMeta(res); }
    else setError(res.message || "브리핑을 만들지 못했습니다.");
    setLoading(false);
  };

  const save = async () => {
    if (!brief) return;
    setSaving(true); setError("");
    const res = await savePrepBrief({
      title: title.trim(), brief, topic: topic.trim(), date: date.trim(), attendees: attendees.trim(),
    });
    if (res.ok && res.sessionId) setSavedId(res.sessionId);
    else setError(res.message || "저장에 실패했습니다.");
    setSaving(false);
  };

  const copy = () => {
    navigator.clipboard?.writeText(brief);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="mx-auto grid max-w-5xl gap-3 lg:grid-cols-[1fr_300px]">
      <div className="space-y-3">
        <div className="space-y-2.5 rounded-card border border-line bg-surface p-3 shadow-card">
          {/* Enter 로 생성 — 한글 조합 가드가 들어 있다(없으면 후보 확정 Enter 가 LLM 호출을
              일으킨다). 두 입력창이 같은 헬퍼를 쓴다. */}
          <TextField label="회의 제목" required id="prep-title" value={title}
            onChange={(e) => setTitle(e.target.value)} onKeyDown={submitOnEnter(run)}
            placeholder="예: 3분기 로드맵 검토" />

          <div className="grid gap-2.5 sm:grid-cols-2">
            <Field label="회의 날짜" htmlFor="prep-date">
              <Input id="prep-date" type="date" value={date} onChange={(e) => setDate(e.target.value)} />
            </Field>
            <Field label="참석자" htmlFor="prep-attendees">
              <Input id="prep-attendees" value={attendees} placeholder="예: 홍길동, 김영희"
                onChange={(e) => setAttendees(e.target.value)} />
            </Field>
          </div>

          <Field label="주제 / 키워드" htmlFor="prep-topic">
            <Input id="prep-topic" value={topic} placeholder="예: 로드맵, 우선순위, 예산"
              onChange={(e) => setTopic(e.target.value)} onKeyDown={submitOnEnter(run)} />
          </Field>

          <Field label="추가 맥락" htmlFor="prep-notes"
            description="배경·확인할 안건을 적으면 관련 노트 검색과 브리핑에 반영됩니다.">
            <Textarea id="prep-notes" value={notes} rows={3}
              onChange={(e) => setNotes(e.target.value)} />
          </Field>

          <Button variant="primary" icon={ClipboardList} className="w-full" busy={loading}
            disabled={!title.trim()} onClick={run}>
            {loading ? "브리핑 만드는 중…" : "브리핑 생성"}
          </Button>
        </div>

        {error && <Banner tone="err" title="문제가 있었습니다" onDismiss={() => setError("")}>{error}</Banner>}

        {brief && (
          <div className="rounded-card border border-line bg-surface p-3 shadow-card">
            <div className="mb-2 flex flex-wrap justify-end gap-1.5">
              <Button size="sm" variant="ghost" icon={copied ? Check : Copy} onClick={copy}>
                {copied ? "복사됨" : "복사"}
              </Button>
              {savedId ? (
                <Button size="sm" variant="secondary" icon={Check} onClick={() => onSaved?.(savedId)}>
                  저장됨 — 열기
                </Button>
              ) : (
                <Button size="sm" variant="primary" icon={Save} busy={saving} onClick={save}
                  title="노트 폴더의 Planning 아래에 .md 로 저장하고, 라이브러리에도 준비 세션으로 남깁니다">
                  노트 폴더(Planning/)에 저장
                </Button>
              )}
            </div>
            <div className="max-h-[60vh] overflow-y-auto">
              <Markdown content={brief} />
            </div>
          </div>
        )}
      </div>

      {/* 연결된 노트 — 브리핑이 무엇을 근거로 만들어졌는지. 없으면 그 사실도 적는다. */}
      <aside className="h-fit space-y-2 rounded-card border border-line bg-surface p-3 shadow-card">
        <h3 className="text-md font-semibold text-ink">연결된 노트</h3>
        {!meta ? (
          <p className="text-sm text-ink-3">브리핑을 만들면 근거가 된 노트가 여기 나옵니다.</p>
        ) : meta.vault_connected === false ? (
          <p className="flex items-start gap-1.5 rounded-card border border-warn-line bg-warn-bg
            px-2 py-1.5 text-xs text-warn">
            <AlertTriangle size={12} className="mt-0.5 shrink-0" aria-hidden="true" />
            노트 폴더가 연결되지 않아 관련 노트를 찾지 못했습니다. [설정] → 노트 폴더(.md)를
            지정하면 지난 기록을 근거로 브리핑이 풍부해집니다(Obsidian 앱은 필요 없습니다).
          </p>
        ) : (
          <>
            <div className="flex flex-wrap gap-1.5">
              <Tag>관련 노트 {meta.related_count ?? 0}</Tag>
              <Tag>미완료 액션 {meta.open_actions ?? 0}</Tag>
              <Tag>최근 결정 {meta.recent_decisions ?? 0}</Tag>
            </div>
            {meta.related && meta.related.length > 0 ? (
              <ul className="space-y-1">
                {meta.related.map((n, i) => (
                  <li key={i} className="flex items-center gap-1.5 rounded-ctl border border-line
                    px-2 py-1 text-xs text-ink-2">
                    <FileText size={12} className="shrink-0 text-ink-3" aria-hidden="true" />
                    <span className="truncate">{n.title || n.path}</span>
                    {typeof n.score === "number" && (
                      <span className="num ml-auto shrink-0 text-ink-3">{n.score.toFixed(3)}</span>
                    )}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-xs text-ink-3">
                관련 노트를 찾지 못했습니다(노트 폴더에 관련 기록이 없을 수 있습니다).
              </p>
            )}
          </>
        )}
      </aside>
    </div>
  );
}
