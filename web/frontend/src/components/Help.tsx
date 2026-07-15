import React from "react";
import {
  Rocket, KeyRound, FolderOpen, RefreshCw, Power, LifeBuoy, ExternalLink, Sparkles,
} from "lucide-react";

function Card({ icon, title, children }: { icon: React.ReactNode; title: string; children: React.ReactNode }) {
  return (
    <section className="bg-white border border-brand-200 rounded-2xl p-4 md:p-5 mb-3 shadow-sm">
      <h3 className="text-base font-bold mb-2 flex items-center gap-2 text-brand-900">{icon} {title}</h3>
      <div className="text-sm text-brand-700 leading-relaxed space-y-1.5">{children}</div>
    </section>
  );
}

function A({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <a href={href} target="_blank" rel="noreferrer" className="text-brand-700 underline decoration-brand-300 hover:text-brand-900 inline-flex items-center gap-0.5">
      {children}<ExternalLink size={12} />
    </a>
  );
}

export default function Help() {
  return (
    <div className="max-w-3xl mx-auto px-1 md:px-0 pb-20 md:pb-0">
      <h2 className="text-2xl font-bold tracking-tight mb-1">도움말 · 사용법</h2>
      <p className="text-brand-500 mb-4 text-sm">처음 사용하시나요? 아래 순서대로 따라 하면 됩니다.</p>

      <Card icon={<Rocket size={16} />} title="1. 빠른 시작">
        <p>① 위 <b>[설정]</b>에서 OpenAI API 키를 넣고 저장합니다. (필수)</p>
        <p>② <b>[업로드]</b>에 녹음 파일(mp3·m4a·wav·mp4)을 올리면 자동으로 회의록이 만들어집니다.</p>
        <p>③ <b>[녹음]</b>으로 실시간 녹음도 가능하고, <b>[텍스트 분석]</b>에 메모를 붙여넣어도 됩니다.</p>
        <p>④ 결과는 <b>[대시보드]</b>에서 보고 복사·다운로드할 수 있습니다.</p>
      </Card>

      <Card icon={<KeyRound size={16} />} title="2. API 키 발급 방법">
        <p><b>OpenAI (필수)</b> — 음성 인식·회의록 생성</p>
        <ul className="list-disc ml-5 text-[13px] text-brand-600">
          <li><A href="https://platform.openai.com/api-keys">platform.openai.com/api-keys</A> 접속 → 로그인 → [Create new secret key]</li>
          <li>"sk-proj-..." 키 복사(한 번만 표시) → [설정]의 OpenAI API 키에 붙여넣기</li>
          <li>사용하려면 결제수단 등록 필요: <A href="https://platform.openai.com/account/billing">결제 설정</A></li>
        </ul>
        <p className="mt-2"><b>Anthropic / Claude (선택)</b> — Claude로 회의록 생성 시</p>
        <ul className="list-disc ml-5 text-[13px] text-brand-600">
          <li><A href="https://console.anthropic.com/settings/keys">console.anthropic.com/settings/keys</A> → [Create Key] → "sk-ant-..." 복사</li>
          <li>[설정] → 모델 → "회의록 생성 AI"를 Claude로 변경</li>
        </ul>
        <p className="mt-2"><b>Obsidian (선택)</b></p>
        <ul className="list-disc ml-5 text-[13px] text-brand-600">
          <li>가장 쉬움: [설정]의 <b>Obsidian 볼트 폴더</b>에 .md 폴더 경로만 넣으세요. REST API 없이도 그 폴더에 회의록이 저장되고 위키 검색에 쓰입니다.</li>
          <li>앱에 실시간 반영이 필요하면 Obsidian의 <A href="https://github.com/coddingtonbear/obsidian-local-rest-api">Local REST API</A> 플러그인 설치 후 [설정]에서 API 키 입력.</li>
        </ul>
        <p className="text-xs text-brand-400 mt-2">입력한 키는 이 PC에만 저장되고 화면에서는 ●●●로 가려집니다.</p>
      </Card>

      <Card icon={<Sparkles size={16} />} title="3. 기능 한눈에">
        <ul className="list-disc ml-5">
          <li><b>업로드/녹음/텍스트 분석</b> — 회의록·요약·액션아이템 자동 생성</li>
          <li><b>위키 질문</b> — 볼트(.md)에 쌓인 회의·세미나 기록을 근거로 질문 답변</li>
          <li><b>회의 준비</b> — 제목/주제로 관련 노트·이전 결정·미완료 액션을 모아 준비 자료</li>
          <li><b>설정</b> — 키·모델·저장 위치·기능 토글 (맨 아래 "전체 설정(JSON)"에서 모든 항목 편집 가능)</li>
        </ul>
      </Card>

      <Card icon={<FolderOpen size={16} />} title="4. 저장 위치">
        <p>결과물과 설정은 프로그램(exe) 옆 <b>MeetingMinutesData</b> 폴더에 저장됩니다.</p>
        <ul className="list-disc ml-5 text-[13px] text-brand-600">
          <li>config.json — 내 설정(키 포함)</li>
          <li>output — 생성된 회의록/요약 파일 ([설정]→저장 위치에서 변경 가능)</li>
          <li>data / web — 인덱스·DB·업로드·로그</li>
        </ul>
      </Card>

      <Card icon={<RefreshCw size={16} />} title="5. 업데이트 방법">
        <p>① [설정] → "앱 종료" 로 프로그램을 끕니다.</p>
        <p>② <b>MeetingMinutesData</b> 폴더를 백업(복사)해 둡니다.</p>
        <p>③ 새 버전을 푼 뒤, 백업한 MeetingMinutesData 폴더를 새 폴더(exe 옆)에 넣습니다.</p>
        <p>④ 새 MeetingMinutes.exe 실행 → 설정·회의록이 그대로 이어집니다(자동 마이그레이션).</p>
      </Card>

      <Card icon={<Power size={16} />} title="6. 종료 방법">
        <p>[설정] 화면 맨 아래 <b>"앱 종료"</b> 버튼을 누르세요. (콘솔 창은 없습니다)</p>
      </Card>

      <Card icon={<LifeBuoy size={16} />} title="7. 문제가 있을 때">
        <ul className="list-disc ml-5">
          <li><b>회의록 생성 실패</b> — [설정]에서 OpenAI/Claude "연결 테스트"가 성공인지 확인(키 만료·결제 미설정 시 실패).</li>
          <li><b>"ffmpeg 없음" 경고</b> — 오디오 변환 도구가 없어 업로드가 안 될 수 있습니다. 배포 담당자에게 ffmpeg 포함 버전을 요청하세요.</li>
          <li><b>위키/회의준비가 결과를 못 찾음</b> — [설정]에서 볼트 폴더 확인 후 "검색 인덱스 재빌드"를 누르세요.</li>
          <li><b>기타 오류</b> — MeetingMinutesData\data\logs\web_exe.log 파일을 담당자에게 전달.</li>
        </ul>
      </Card>
    </div>
  );
}
