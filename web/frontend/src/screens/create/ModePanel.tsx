import React, { useEffect, useState } from "react";
import { ChevronDown } from "lucide-react";
import { MODE_PRESETS } from "../../lib/types";
import { typeLabel } from "../../lib/format";
import { getProfiles } from "../../lib/api";
import { Field, Select } from "../../ui/Field";
import { Tag } from "../../ui/StatusPill";
import { Button } from "../../ui/Button";
import type { Profile } from "../../lib/types";

/**
 * 처리 모드 + 모드 요약 + 빠른 프로필 (PRD FR-NEW-2·FR-NEW-4).
 *
 * 구 `ModeSelector` 를 대체한다. 달라진 점:
 *  - 유형 라벨을 인라인 매핑하지 않고 `lib/format.typeLabel` 을 쓴다(같은 표가 두 벌이었다).
 *  - **빠른 프로필**을 여기로 모은다. 종전에는 업로드 화면에만 있어서 녹음·텍스트에서는
 *    같은 프로필을 쓸 수 없었다.
 *  - `translation` 을 끌 수 있다. 텍스트 경로(`/api/process-text`)는 language·translate 를
 *    **받지 않으므로**(tools.py) 그 화면에서 "번역: 영어→한국어"를 보여주면 거짓이 된다.
 *
 * 모델 ID 는 여기 없다 — 기술 옵션은 [설정] → 고급이다(PRD §6.2 AC).
 */
export default function ModePanel({
  modeNum, onChange, disabled, hint, translation = true,
}: {
  modeNum: number;
  onChange: (mode: number) => void;
  disabled?: boolean;
  hint?: React.ReactNode;
  /** 이 경로에서 번역이 실제로 적용되는지. false 면 요약에서 언어·번역 줄을 뺀다. */
  translation?: boolean;
}) {
  const preset = MODE_PRESETS[modeNum] || MODE_PRESETS[1];
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [showProfiles, setShowProfiles] = useState(false);

  useEffect(() => { getProfiles().then(setProfiles).catch(() => {}); }, []);

  const applyProfile = (p: Profile) => {
    const match = Object.entries(MODE_PRESETS).find(
      ([, v]) => v.language === p.language && v.translate === p.translate && v.type === p.type,
    );
    if (match) onChange(Number(match[0]));
  };

  return (
    <div className="space-y-2.5 rounded-card border border-line bg-surface p-3">
      <Field label="처리 모드" htmlFor="mode-select">
        <Select id="mode-select" value={modeNum} disabled={disabled}
          onChange={(e) => onChange(Number(e.target.value))}>
          {Object.entries(MODE_PRESETS).map(([k, v]) => (
            <option key={k} value={k}>{k}. {v.label}</option>
          ))}
        </Select>
      </Field>

      <div className="flex flex-wrap gap-1.5">
        {translation && <Tag>언어 {preset.language === "ko" ? "한국어" : "영어"}</Tag>}
        {translation && <Tag>{preset.translate ? "번역 영어→한국어" : "번역 없음"}</Tag>}
        <Tag>유형 {typeLabel(preset.type)}</Tag>
      </div>

      {profiles.length > 0 && (
        <div>
          <button type="button" onClick={() => setShowProfiles((v) => !v)}
            aria-expanded={showProfiles}
            className="inline-flex items-center gap-1 text-sm font-semibold text-ink-2 hover:text-ink">
            <ChevronDown size={13} aria-hidden="true"
              className={`transition-transform ${showProfiles ? "" : "-rotate-90"}`} />
            빠른 프로필 ({profiles.length})
          </button>
          {showProfiles && (
            <ul className="mt-1.5 space-y-1">
              {profiles.map((p) => (
                <li key={p.name}>
                  <Button variant="secondary" size="sm" className="w-full justify-start"
                    disabled={disabled} onClick={() => applyProfile(p)}>
                    <span className="font-semibold">{p.name}</span>
                    {p.description && <span className="ml-1 text-ink-3">{p.description}</span>}
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {hint && <p className="text-xs leading-relaxed text-ink-3">{hint}</p>}

      <p className="border-t border-line pt-2 text-xs text-ink-3">
        음성 인식 모델·2단계 보정 같은 기술 옵션은 <b>[설정] → 고급</b>에 있습니다.
      </p>
    </div>
  );
}
