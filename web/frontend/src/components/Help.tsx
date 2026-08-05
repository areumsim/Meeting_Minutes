import React from "react";
import {
  Rocket, KeyRound, FolderOpen, RefreshCw, Power, LifeBuoy, ExternalLink, Sparkles,
  Wand2, Settings as SettingsIcon, FileAudio, Mic, FileText, MessageCircleQuestion,
  ClipboardList, CheckCircle, ChevronDown, Mail, Copy, Download, Share2, Network,
  CalendarClock, Search, Trash2, Zap, AlertCircle, GitMerge, Play, ListChecks, ShieldCheck,
} from "lucide-react";

// 도움말에서 특정 화면으로 바로 이동시키기 위한 최소 프롭(선택).
// App 의 setView 를 그대로 넘겨받는다(넓은 View 타입이라 이 부분집합에 할당 가능).
type NavTarget = "settings" | "upload" | "recorder" | "text" | "wiki" | "prep" | "assistant" | "graph" | "dashboard";

function Card({ icon, title, badge, children }: { icon: React.ReactNode; title: string; badge?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="bg-white border border-brand-200 rounded-2xl p-4 md:p-5 mb-3 shadow-sm">
      <h3 className="text-base font-bold mb-2 flex items-center gap-2 text-brand-900">
        {icon} {title} {badge}
      </h3>
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

// 필수/선택 배지 — "무엇을 꼭 해야 하는지"를 한눈에.
function Badge({ kind }: { kind: "required" | "optional" }) {
  return kind === "required" ? (
    <span className="text-[11px] font-bold px-2 py-0.5 rounded-full bg-red-50 text-red-600 border border-red-100">필수</span>
  ) : (
    <span className="text-[11px] font-bold px-2 py-0.5 rounded-full bg-zinc-100 text-zinc-500 border border-zinc-200">선택</span>
  );
}

// 화면 안내용 알약 모양 텍스트 — 예: [설정] → API 키
function Tag({ children }: { children: React.ReactNode }) {
  return <span className="inline-block font-semibold text-brand-900 bg-brand-100 rounded px-1.5 py-0.5 text-[13px]">{children}</span>;
}

// 번호 단계 목록
function Steps({ items }: { items: React.ReactNode[] }) {
  return (
    <ol className="space-y-2 mt-1">
      {items.map((it, i) => (
        <li key={i} className="flex gap-2.5">
          <span className="shrink-0 w-5 h-5 rounded-full bg-brand-900 text-white text-[11px] font-bold flex items-center justify-center mt-0.5">{i + 1}</span>
          <span className="flex-1">{it}</span>
        </li>
      ))}
    </ol>
  );
}

// 접기/펼치기 — 화면을 짧게 유지하고 필요할 때만 상세를 연다("스텝 많다" 부담 완화).
function Detail({ summary, children, defaultOpen }: { summary: React.ReactNode; children: React.ReactNode; defaultOpen?: boolean }) {
  return (
    <details open={defaultOpen} className="group border border-brand-100 rounded-xl overflow-hidden">
      <summary className="cursor-pointer list-none flex items-center gap-2 px-3 py-2.5 bg-brand-50/60 hover:bg-brand-50 text-sm font-semibold text-brand-800 select-none">
        <ChevronDown size={15} className="transition-transform group-open:rotate-180 text-brand-500 shrink-0" />
        {summary}
      </summary>
      <div className="px-4 py-3 text-[13px] text-brand-600 space-y-1.5 leading-relaxed">{children}</div>
    </details>
  );
}

function GoBtn({ onClick, children }: { onClick?: () => void; children: React.ReactNode }) {
  return <button onClick={onClick} className="text-brand-700 underline decoration-brand-300 hover:text-brand-900 font-medium">{children}</button>;
}

export default function Help({ onNavigate }: { onNavigate?: (v: NavTarget) => void }) {
  const openWizard = () => window.dispatchEvent(new Event("mm:open-onboarding"));

  return (
    <div className="max-w-4xl mx-auto px-1 md:px-0 pb-20 md:pb-0">
      <h2 className="text-2xl font-bold tracking-tight mb-1">도움말 · 사용법</h2>
      <p className="text-brand-500 mb-4 text-sm">처음이신가요? 걱정 마세요. <b>딱 한 가지(OpenAI 키)</b>만 넣으면 바로 회의록을 만들 수 있어요. 나머지는 전부 선택입니다.</p>

      {/* 최상단 — 안내 마법사/설정 바로가기 */}
      <div className="flex flex-col sm:flex-row gap-2 mb-2">
        <button
          onClick={openWizard}
          className="flex-1 flex items-center justify-center gap-2 py-3 bg-brand-950 text-white rounded-xl font-bold hover:bg-brand-900 transition-all shadow-lg active:scale-[0.98]"
        >
          <Wand2 size={18} /> 설정 마법사 다시 열기
        </button>
        <button
          onClick={() => onNavigate?.("settings")}
          className="flex-1 flex items-center justify-center gap-2 py-3 bg-white border border-brand-200 text-brand-800 rounded-xl font-bold hover:bg-brand-50 transition-all active:scale-[0.98]"
        >
          <SettingsIcon size={18} /> 설정 화면 열기
        </button>
      </div>
      <p className="text-xs text-brand-500 mb-4 text-center">설정이 헷갈리면 <b>[설정 마법사 다시 열기]</b>를 누르세요 — 필요한 것만 순서대로 물어봅니다.</p>

      {/* 1. 딱 하나만: OpenAI 키 */}
      <Card icon={<Rocket size={16} />} title="가장 빠른 시작 — 딱 1가지만" badge={<Badge kind="required" />}>
        <p>시작에 <b>꼭 필요한 건 OpenAI API 키 하나</b>뿐입니다. 이것만 넣으면 회의록·요약·액션아이템이 자동으로 만들어져요.</p>
        <Steps items={[
          <>왼쪽 메뉴(모바일은 아래 <Tag>더보기</Tag>)에서 <Tag>설정</Tag> 을 엽니다. — <GoBtn onClick={() => onNavigate?.("settings")}>바로 열기</GoBtn></>,
          <><Tag>API 키</Tag> 항목의 <b>OpenAI API 키</b> 칸에 키(<span className="font-mono">sk-proj-…</span>)를 붙여넣습니다.</>,
          <><b>저장</b> 후 <b>연결 테스트</b>를 눌러 <span className="text-emerald-600 font-semibold inline-flex items-center gap-1"><CheckCircle size={13} />성공</span> 이 뜨는지 확인합니다.</>,
          <>끝! 이제 <Tag>업로드</Tag> 에 녹음 파일을 올리면 회의록이 만들어집니다.</>,
        ]} />
        <p className="text-xs text-brand-500 mt-2">키는 이 PC에만 저장되고 화면에서는 ●●● 로 가려집니다. 키가 아직 없다면 아래 "OpenAI 키 발급"을 펼쳐 보세요.</p>
      </Card>

      {/* 2. 키 발급 */}
      <Card icon={<KeyRound size={16} />} title="OpenAI 키 발급 방법" badge={<Badge kind="required" />}>
        <p>OpenAI 키는 회의록 앱이 음성을 글자로 바꾸고 회의록을 쓰는 데 씁니다. 아래를 그대로 따라 하세요.</p>
        <Detail summary="OpenAI 키 발급 단계 (펼치기)">
          <Steps items={[
            <><A href="https://platform.openai.com/api-keys">platform.openai.com/api-keys</A> 접속 후 로그인(회원가입).</>,
            <><b>Create new secret key</b> 버튼 클릭 → 이름은 아무거나 → 생성.</>,
            <>화면에 뜬 <span className="font-mono">sk-proj-…</span> 키를 <b>복사</b>합니다. <span className="text-red-500">(이 화면을 닫으면 다시 못 보니 지금 복사)</span></>,
            <>회의록 앱 <Tag>설정</Tag> → <Tag>API 키</Tag> 에 붙여넣고 저장.</>,
            <>사용하려면 결제수단 등록이 필요합니다: <A href="https://platform.openai.com/account/billing">결제 설정</A> (선불 크레딧 소액 충전 가능).</>,
          ]} />
        </Detail>
        <div className="mt-2">
          <Detail summary="Claude(Anthropic)로 회의록을 만들고 싶다면 (선택)">
            <Steps items={[
              <><A href="https://console.anthropic.com/settings/keys">console.anthropic.com/settings/keys</A> → <b>Create Key</b> → <span className="font-mono">sk-ant-…</span> 복사.</>,
              <><Tag>설정</Tag> → <Tag>API 키</Tag> 의 Anthropic 키 칸에 붙여넣기.</>,
              <><Tag>설정</Tag> → <Tag>모델</Tag> → "회의록 생성 AI"를 <b>Claude</b>로 변경.</>,
            ]} />
            <p className="text-brand-500">음성 인식(STT)은 항상 OpenAI를 쓰므로, Claude를 써도 OpenAI 키는 필요합니다.</p>
          </Detail>
        </div>
      </Card>

      {/* 3. 회의록 만드는 3가지 방법 */}
      <Card icon={<Sparkles size={16} />} title="회의록 만드는 3가지 방법">
        <p>상황에 맞게 아무 방법이나 쓰면 됩니다. 결과는 모두 <Tag>대시보드</Tag> 에 모입니다.</p>

        <Detail summary={<span className="flex items-center gap-1.5"><FileAudio size={14} /> ① 녹음 파일이 있을 때 — 업로드</span>}>
          <Steps items={[
            <><Tag>업로드</Tag> 화면을 엽니다. — <GoBtn onClick={() => onNavigate?.("upload")}>바로 열기</GoBtn></>,
            <>녹음 파일(<b>mp3·m4a·wav·mp4·webm</b>)을 끌어다 놓거나 선택합니다.</>,
            <>제목·유형(회의/세미나/강의)을 정하고 시작하면, 음성 인식 → 회의록 → 요약 → 액션아이템이 자동 생성됩니다.</>,
            <>완료되면 <Tag>대시보드</Tag> 에서 열어 확인·복사·다운로드.</>,
          ]} />
          <p className="text-brand-500">긴 파일은 수 분 걸릴 수 있고, 진행 화면에서 <b>처리 취소</b>도 가능합니다.</p>
        </Detail>
        <div className="mt-2">
          <Detail summary={<span className="flex items-center gap-1.5"><Mic size={14} /> ② 지금 회의를 실시간으로 받아쓰기 — 녹음</span>}>
            <Steps items={[
              <><Tag>녹음</Tag> 화면을 엽니다. — <GoBtn onClick={() => onNavigate?.("recorder")}>바로 열기</GoBtn></>,
              <>언어·번역 여부를 고르고 <b>녹음 시작</b>. 말하는 내용이 화면에 실시간으로 뜹니다.</>,
              <><b>중지</b>하면 전체 전사를 정리해 회의록으로 만들어 줍니다.</>,
            ]} />
            <p className="text-brand-500">브라우저가 마이크 권한을 물으면 <b>허용</b>하세요. 처음 표시까지 2~6초 걸릴 수 있습니다.</p>
          </Detail>
        </div>
        <div className="mt-2">
          <Detail summary={<span className="flex items-center gap-1.5"><FileText size={14} /> ③ 이미 글로 된 메모/전사가 있을 때 — 텍스트 분석</span>}>
            <Steps items={[
              <><Tag>텍스트 분석</Tag> 화면을 엽니다. — <GoBtn onClick={() => onNavigate?.("text")}>바로 열기</GoBtn></>,
              <>회의 메모나 다른 곳에서 받은 전사 내용을 붙여넣습니다.</>,
              <>음성 인식을 건너뛰고 바로 회의록·요약·액션아이템을 만듭니다.</>,
            ]} />
          </Detail>
        </div>
      </Card>

      {/* 4. 회의록 열어보고 고치기 (세션 상세) — 사용자 요청: 메모 추가 재생성 */}
      <Card icon={<FileText size={16} />} title="만든 회의록 열어보고 고치기">
        <p><Tag>대시보드</Tag> 에서 항목을 클릭하면 상세 화면이 열립니다. 여기서 문서를 보고, 복사·저장·메일 공유하고, <b>지시를 넣어 다시 만들 수</b> 있습니다.</p>

        <Detail summary="① 문서 탭 — 회의록·요약·스크립트·액션 등" defaultOpen>
          <ul className="list-disc ml-4 space-y-1">
            <li><b>회의록</b> — 정리된 본문. <b>요약</b> — 핵심만 짧게.</li>
            <li><b>스크립트</b> — 시간·화자별 전체 전사. <b>액션</b> — 할 일(체크리스트).</li>
            <li><b>사실확인</b> — 회의 중 주장과 노트가 맞는지 검증(기능 켠 경우). <b>위키 맥락/제안/정제본</b> — 노트 폴더 연동 시 생성되는 참고 자료.</li>
            <li><b>그래프</b> — 이 회의에 등장한 인물·조직·주제·결정의 연결도(아래 지식그래프 참고).</li>
          </ul>
          <p className="text-brand-500">문서가 없는 탭은 자동으로 숨겨집니다. 회의 준비 브리핑 세션은 <b>회의록</b> 탭에만 내용이 있습니다.</p>
        </Detail>

        <div className="mt-2">
          <Detail summary="② 복사 · 다운로드 · 공유(메일)">
            <ul className="list-disc ml-4 space-y-1">
              <li><span className="inline-flex items-center gap-1"><Copy size={13} /> 복사</span> — 현재 탭 내용을 클립보드로.</li>
              <li><span className="inline-flex items-center gap-1"><Download size={13} /> 다운로드</span> — .md 파일로 저장(PC).</li>
              <li><span className="inline-flex items-center gap-1"><Share2 size={13} /> 공유</span> — 메일/메신저로 보내기(아래 "메일로 보내기" 참고).</li>
            </ul>
          </Detail>
        </div>

        <div className="mt-2">
          <Detail summary="③ 메모(지시) 추가해서 다시 만들기 — 재생성" defaultOpen>
            <p>결과가 아쉽거나 고치고 싶을 때, <b>지우고 다시 녹음할 필요 없이</b> 기존 전사를 그대로 재사용해 회의록을 다시 만듭니다.</p>
            <Steps items={[
              <>상세 화면에서 <b>회의록</b> 또는 <b>요약</b> 탭을 엽니다.</>,
              <>탭 아래쪽 <b>"노트 반영해 재생성"</b> 칸에 원하는 지시를 적습니다.<br /><span className="text-brand-500">예: "액션 아이템을 표로 정리하고, Q&A 부분을 더 자세히 다뤄주세요."</span></>,
              <><span className="inline-flex items-center gap-1"><RefreshCw size={13} /> <b>AI 문서 재생성</b></span> 버튼을 누르면 지시를 반영해 다시 작성됩니다(음성 인식은 다시 하지 않아 빠르고 저렴).</>,
            ]} />
            <p className="text-brand-500">여러 번 반복해도 됩니다. 원본 전사(스크립트)는 그대로 유지됩니다.</p>
          </Detail>
        </div>

        <div className="mt-2">
          <Detail summary="④ 예상 비용 · 처리 취소">
            <ul className="list-disc ml-4 space-y-1">
              <li>완료된 세션 상단에 <b>💵 예상 비용</b>(대략치)이 표시됩니다 — 음성 인식+번역+회의록 생성 합산.</li>
              <li>처리 중 화면에서 <b>처리 취소</b>를 누르면 현재 단계가 끝난 뒤 중단되고 그 세션은 삭제됩니다.</li>
            </ul>
          </Detail>
        </div>
      </Card>

      {/* 5. 메일로 보내기 — 사용자 요청 */}
      <Card icon={<Mail size={16} />} title="회의록 메일로 보내기">
        <p>두 가지 방법이 있습니다. 상황에 맞게 고르세요.</p>
        <Detail summary="방법 A — 만든 회의록을 그때그때 메일로 (간단)">
          <Steps items={[
            <><Tag>대시보드</Tag> 에서 회의록을 열고 원하는 탭(회의록/요약 등)을 선택.</>,
            <>오른쪽 위 <span className="inline-flex items-center gap-1"><Share2 size={13} /> <b>공유</b></span> 버튼을 누릅니다.</>,
            <>메일 앱이 열리면 받는 사람을 넣고 보냅니다. (본문이 길면 앞부분만 실리니, 전문은 <b>다운로드</b>해 첨부하세요.)</>,
          ]} />
          <p className="text-brand-500">받는 주소를 미리 정해두려면 마법사/설정의 이메일 항목을 채워두면 편합니다.</p>
        </Detail>
        <div className="mt-2">
          <Detail summary="방법 B — 완료되면 자동으로 메일 발송 (설정)">
            <Steps items={[
              <><Tag>설정</Tag> → <Tag>이메일</Tag> 에서 <b>보내는 주소</b>·<b>앱 비밀번호</b>·<b>받는 주소</b>를 입력.</>,
              <><b>테스트 메일 보내기</b>로 정상 발송을 확인.</>,
              <>실시간 녹음은 <Tag>설정</Tag> → 실시간 녹취 → "종료 후 이메일 자동발송"을 켜면 끝나는 즉시 메일이 옵니다.</>,
            ]} />
            <p className="text-red-500">주의: 평소 로그인 비밀번호가 아니라, 메일 서비스 보안설정에서 발급하는 <b>'앱 비밀번호'</b>입니다.</p>
            <p className="text-brand-500">Gmail: 2단계 인증 후 앱 비밀번호 / 네이버: 메일 설정 POP3·SMTP / 아웃룩·회사메일: 계정 보안→앱 암호(막혀 있으면 IT에 SMTP 허용 요청).</p>
          </Detail>
        </div>
      </Card>

      {/* 6. 대시보드 사용법 */}
      <Card icon={<ListChecks size={16} />} title="대시보드 — 만든 기록 관리">
        <ul className="list-disc ml-5">
          <li>모든 회의록·녹음·업로드·회의준비가 <b>최신순</b>으로 나열됩니다. 클릭하면 상세로 이동.</li>
          <li>상단 <span className="inline-flex items-center gap-1"><Search size={13} /> 검색</span> 과 <b>유형 필터</b>(회의/세미나/강의)로 빠르게 찾습니다.</li>
          <li>상태 아이콘: <span className="text-emerald-600">완료</span> · <span className="text-amber-600">처리 중</span> · <span className="text-red-500">오류</span>. (처리 중이면 자동으로 갱신됩니다)</li>
          <li>항목의 <span className="inline-flex items-center gap-1"><Trash2 size={13} /></span> 로 개별 삭제, <b>전체 삭제</b>로 모두 지웁니다.</li>
          <li>위쪽 <span className="inline-flex items-center gap-1"><Mic size={13} /> 녹음</span>·업로드 버튼으로 바로 새 회의록을 시작할 수 있습니다.</li>
        </ul>
      </Card>

      {/* 6.5 녹음 중 화면에 뜨는 것 — "이건 뭐고 어떻게 끄나"에 한 곳에서 답한다.
          소리 문의가 반복되는데 이 앱은 소리를 낼 수단 자체가 없다(참견도 4·5 미구현). */}
      <Card icon={<Mic size={16} />} title="녹음 중 화면에 뜨는 것 · 끄는 법">
        <p><b>어떤 것도 소리를 내지 않습니다.</b> 알림음·음성 읽기는 아직 만들지 않았습니다 —
          모든 안내는 화면에 조용히 나타났다 사라집니다. 팝업도, 포커스 이동도 없습니다.</p>
        <div className="space-y-2 mt-1">
          <Detail summary={<span className="flex items-center gap-1.5"><Search size={14} /> 초록 줄 — 관련 노트</span>} defaultOpen>
            <p>말한 내용과 관련된 <b>내 노트 폴더의 문서</b>를 찾아 <b>[[제목]]</b> 칩으로 보여줍니다
              (📄 노트 · 🎓 논문 · 🌐 웹 보완). <b>근거 보기</b>를 누르면 어느 대목이 걸렸는지 펼쳐집니다.</p>
            <ul className="list-disc ml-4 space-y-1">
              <li><b>끄기</b> — 그 줄 오른쪽 <Tag>이번 회의 끔</Tag>. 표시만이 아니라 <b>서버 검색과
                웹 보완 요금까지</b> 멈춥니다. 이미 찾은 노트는 회의록에 남습니다.</li>
              <li><b>아예 안 쓰기</b> — <Tag>설정</Tag> → <b>실시간 노트 검색</b>을 끄면 다음 회의부터 계속 꺼집니다.</li>
            </ul>
          </Detail>
          <Detail summary={<span className="flex items-center gap-1.5"><MessageCircleQuestion size={14} /> 회색 줄 — 페르소나 카드(검증·의견·질문)</span>}>
            <p>회의 내용을 계속 읽으면서 <b>확인이 필요한 대목</b>을 카드로 올립니다 —
              사실 대조(🔍), 지난 회의 결정과 어긋남(🧐), 빠진 담당자·기한(📝),
              물어볼 만한 질문(🧨🐣), 반대 시나리오(😈), 중간 정리(🧾).
              카드는 언제나 <b>초안</b>이고 판정이 아닙니다.</p>
            <ul className="list-disc ml-4 space-y-1">
              <li><b>근거</b> — 카드를 누르면 무엇과 대조했는지(노트·논문·웹·지난 결정) 함께 보입니다.
                🔍·🎓 카드에 <b>⚠ 미검증</b>이 붙으면 웹 검색 없이 사내 자료만 본 것입니다.</li>
              <li><b>확인 / 닫기</b> — 도움이 됐는지 눌러 주세요. 이 기록이 정확도를 재는 데 쓰입니다.</li>
              <li><b>끄기</b> — 그 줄의 <Tag>이번 회의 끔</Tag>. 카드 생성이 멈춰 추가 비용이 들지 않습니다
                (판정 기록만 남습니다). 다음 녹음에서는 다시 켜집니다.</li>
              <li><b>덜 뜨게 하기</b> — <Tag>설정</Tag> → <b>페르소나별 참견도</b>에서 역할마다
                0(금지)~3(옆 카드)을 고릅니다. 기본은 조용한 쪽입니다.</li>
            </ul>
          </Detail>
          <Detail summary={<span className="flex items-center gap-1.5"><FileText size={14} /> 회의가 끝난 뒤에는</span>}>
            <ul className="list-disc ml-4 space-y-1">
              <li>회의 중 뜬 <b>중간 정리</b>는 회의 상세의 <Tag>중간 정리</Tag> 탭에 남습니다.</li>
              <li>회의록에는 <b>🔗 관련 노트</b>가 함께 붙고, 확인이 필요한 주장은
                <Tag>사실확인</Tag> 탭으로 정리됩니다(노트 → 논문 → 웹 순서로 대조).</li>
              <li>메일 발송을 켜 두었다면 이 결과가 함께 전달됩니다.</li>
            </ul>
          </Detail>
        </div>
      </Card>

      {/* 7. 노트 폴더가 있을 때의 기능들 (선택) */}
      <Card icon={<Sparkles size={16} />} title="노트 폴더를 연결하면 쓸 수 있는 기능" badge={<Badge kind="optional" />}>
        <p>아래는 <b>없어도 회의록 생성에는 지장 없는</b> 부가 기능입니다. .md 노트 폴더를 연결하면 회의 기록이 쌓여 검색·질문·자동화가 가능해집니다.</p>

        <div className="space-y-2 mt-1">
          <Detail summary={<span className="flex items-center gap-1.5"><Sparkles size={14} /> 작동 방식 — 아주 간단히</span>} defaultOpen>
            <ul className="list-disc ml-4 space-y-1">
              <li><b>정리(쌓임)</b> — 회의록을 만들 때마다 노트 폴더에 <b>날짜와 함께</b> .md 한 건으로 자동 저장됩니다.</li>
              <li><b>색인</b> — <Tag>설정</Tag> → <b>검색 인덱스·그래프 재빌드</b>를 누르면, 앱이 폴더의 노트들을 검색·질문할 수 있게 목록으로 정리합니다. (노트를 추가/변경한 뒤 한 번씩 눌러 최신화)</li>
              <li><b>위키 질문(회의록 질의)</b> — 물어보면 ① 질문과 <b>관련된 노트</b>를 폴더에서 찾고 → ② 그 내용<b>만 근거</b>로 AI가 답하며 → ③ 사용한 <b>출처 노트</b>를 함께 보여줍니다(없는 내용은 지어내지 않고 "확인 불가"로 표시).</li>
              <li><b>날짜 인식</b> — 각 노트의 <b>작성일</b>을 함께 읽으므로 "가장 최근 회의는?", "언제 정해졌어?" 같은 <b>시점 질문</b>에도 답합니다.</li>
              <li><b>회의 준비</b> — 제목·주제를 넣으면 관련 노트·지난 결정·미완료 액션을 <b>한 장으로 모아</b> 브리핑을 만듭니다.</li>
            </ul>
            <p className="text-brand-500">요약: <b>회의록 생성 → 폴더에 날짜별로 쌓임 → 재빌드로 색인 → 질문하면 관련 노트를 근거로 출처와 함께 답변</b>. 표현이 달라도 뜻으로 찾는 <b>의미 검색</b>이 기본 적용되고, 노트가 많을수록 답이 정확해집니다. (Obsidian 앱 없이 <b>.md 폴더만</b>으로 동작)</p>
          </Detail>

          <Detail summary={<span className="flex items-center gap-1.5"><FolderOpen size={14} /> 먼저: 노트 폴더 연결 (Obsidian 앱 없어도 됨)</span>}>
            <Steps items={[
              <><Tag>설정</Tag> → <Tag>노트 폴더</Tag> 에 .md를 모아둘 폴더 경로를 넣습니다(찾아보기 버튼 사용).</>,
              <>저장 후 <b>검색 인덱스·그래프 재빌드</b>를 한 번 누릅니다.</>,
              <>이후 회의록이 그 폴더에 쌓이고, 아래 기능들이 그 기록을 활용합니다.</>,
            ]} />
            <p>Obsidian 앱에 <b>실시간</b> 반영이 필요할 때만 <A href="https://github.com/coddingtonbear/obsidian-local-rest-api">Local REST API</A> 플러그인을 설치하고 설정에서 API 키를 넣으세요.</p>
          </Detail>

          <Detail summary={<span className="flex items-center gap-1.5"><MessageCircleQuestion size={14} /> 위키 질문 — 쌓인 기록에 근거해 답변</span>}>
            <p>물어보면 노트 폴더의 회의·세미나 기록을 근거로 답하고 <b>출처 노트</b>를 함께 보여줍니다.</p>
            <GoBtn onClick={() => onNavigate?.("wiki")}>위키 질문 열기</GoBtn>
          </Detail>

          <Detail summary={<span className="flex items-center gap-1.5"><ClipboardList size={14} /> 회의 준비 — 관련 노트·지난 결정·미완료 액션 모으기</span>}>
            <p>제목/주제(+참석자·메모)를 넣으면 관련 기록과 지난 결정·미완료 액션을 모아 준비 자료를 만들어 줍니다. 저장하면 대시보드에도 남습니다.</p>
            <GoBtn onClick={() => onNavigate?.("prep")}>회의 준비 열기</GoBtn>
          </Detail>

          <Detail summary={<span className="flex items-center gap-1.5"><CalendarClock size={14} /> 회의 비서 — 일정·충돌 점검, 녹음↔계획 병합, 자동화</span>}>
            <ul className="list-disc ml-4 space-y-1">
              <li><b>일정·현황</b> — 다가오는 회의·시간 충돌·이중예약·준비 미비를 요약하고, <b>일정 대시보드</b>(_일정.md)를 볼트에 만들어 줍니다.</li>
              <li><span className="inline-flex items-center gap-1"><GitMerge size={13} /> 녹음↔계획 병합</span> — 계획 노트와 짝지어진 녹음을 확인 후 병합.</li>
              <li><span className="inline-flex items-center gap-1"><FileAudio size={13} /> 노트 첨부 오디오 처리</span> — 노트에 붙인 녹음을 찾아 회의록으로 정리.</li>
              <li><span className="inline-flex items-center gap-1"><Play size={13} /> 계획 자동화</span> — 켜 두면 planned 회의에 사전 리서치를 자동 작성하고 새 녹음을 자동 처리.</li>
            </ul>
            <GoBtn onClick={() => onNavigate?.("assistant")}>회의 비서 열기</GoBtn>
          </Detail>

          <Detail summary={<span className="flex items-center gap-1.5"><Network size={14} /> 지식그래프 — 인물·조직·주제의 연결 보기</span>}>
            <p>회의에 등장한 <b>인물·조직·주제·결정·액션</b>이 어떻게 이어지는지 그림으로 봅니다. 노드를 누르면 연결된 항목이 펼쳐지고, 회의록 속 <b>[[위키링크]]</b>를 눌러도 여기로 이동합니다.</p>
            <GoBtn onClick={() => onNavigate?.("graph")}>지식그래프 열기</GoBtn>
          </Detail>
        </div>
      </Card>

      {/* 8. 저장 위치 */}
      <Card icon={<FolderOpen size={16} />} title="내 데이터는 어디에 저장되나요?">
        <p>모든 결과물과 설정은 프로그램(exe) 옆 <b>MeetingMinutesData</b> 폴더에만 저장됩니다. (인터넷 서버에 올라가지 않음)</p>
        <ul className="list-disc ml-5 text-[13px] text-brand-600">
          <li><b>config.json</b> — 내 설정(키 포함)</li>
          <li><b>output</b> — 생성된 회의록·요약 파일 (<Tag>설정</Tag> → 저장 위치에서 변경 가능)</li>
          <li><b>data / web</b> — 검색 인덱스·DB·업로드·로그</li>
        </ul>
      </Card>

      {/* 9. 업데이트 */}
      <Card icon={<RefreshCw size={16} />} title="새 버전으로 업데이트">
        <Steps items={[
          <><Tag>설정</Tag> 맨 아래 <b>앱 종료</b>로 프로그램을 끕니다.</>,
          <><b>MeetingMinutesData</b> 폴더를 복사해 백업해 둡니다.</>,
          <>새 버전을 푼 뒤, 백업한 <b>MeetingMinutesData</b>를 새 폴더(exe 옆)에 넣습니다.</>,
          <>새 <b>MeetingMinutes.exe</b>를 실행하면 설정·회의록이 그대로 이어집니다(자동 마이그레이션).</>,
        ]} />
      </Card>

      {/* 10. 종료 */}
      <Card icon={<Power size={16} />} title="프로그램 종료 방법">
        <p><Tag>설정</Tag> 화면 맨 아래 <b>앱 종료</b> 버튼을 누르세요. (별도 콘솔 창은 없습니다)</p>
      </Card>

      {/* 11. 문제해결 */}
      <Card icon={<LifeBuoy size={16} />} title="잘 안 될 때 (문제 해결)">
        <div className="space-y-2">
          <Detail summary="회의록 생성이 실패해요">
            <p><Tag>설정</Tag> → API 키에서 <b>연결 테스트</b>가 성공인지 확인하세요. 실패하면 키가 틀렸거나 만료, 또는 OpenAI 결제수단이 없는 경우입니다. (<A href="https://platform.openai.com/account/billing">결제 설정</A>) 상세 화면 상단의 <span className="inline-flex items-center gap-1"><AlertCircle size={13} className="text-red-500" /> 빨간 오류 메시지</span>도 원인을 알려줍니다.</p>
          </Detail>
          <Detail summary="'ffmpeg 없음' 경고가 떠요">
            <p>오디오 변환 도구가 없어 업로드가 안 될 수 있습니다. 프로그램 폴더의 <span className="font-mono">vendor/ffmpeg/</span> 에 <span className="font-mono">ffmpeg.exe</span>가 있는지 확인하거나, 배포 담당자에게 <b>ffmpeg 포함 버전</b>을 요청하세요.</p>
          </Detail>
          <Detail summary="위키 질문·회의 준비·그래프가 결과를 못 찾아요">
            <p><Tag>설정</Tag>에서 <b>노트 폴더</b>가 올바른지 확인한 뒤 <b>검색 인덱스·그래프 재빌드</b>를 눌러 최신화하세요. 폴더에 회의 기록(.md)이 아직 없으면 결과가 비어 있을 수 있습니다.</p>
            <p className="text-brand-500">표현이 달라 정답을 못 찾을 때: <b>의미 검색(임베딩)</b>이 기본으로 켜져 있어, 질문과 노트의 말이 달라도 뜻으로 찾아줍니다(OpenAI 키 필요, 재빌드 시 자동 적용). 키가 없으면 글자 매칭만으로 동작하니, 핵심 <b>키워드·고유명사</b>를 넣어 물어보면 더 잘 찾습니다.</p>
          </Detail>
          <Detail summary="메일이 안 보내져요">
            <p>로그인 비밀번호가 아니라 <b>앱 비밀번호</b>를 넣었는지 확인하세요. <Tag>설정</Tag> → 이메일의 <b>테스트 메일 보내기</b>가 실패하면 메시지에 원인이 나옵니다(회사 메일은 SMTP가 막혀 IT 허용이 필요할 수 있음).</p>
          </Detail>
          <Detail summary="그 밖의 오류 — 로그 전달">
            <p>문제가 계속되면 <span className="font-mono">MeetingMinutesData\data\logs\web_exe.log</span> 파일을 배포/개발 담당자에게 전달하세요. 원인 파악에 큰 도움이 됩니다.</p>
          </Detail>
        </div>
      </Card>

      {/*
        녹취 정책 — 제품 정체성이라 도움말 안에 고정해 둔다(PRD_Natively FR-A3/A4).
        README 에만 있으면 배포본 사용자는 볼 일이 없다.
      */}
      <Card icon={<ShieldCheck size={16} />} title="녹취 정책 · 사용 수칙">
        <p>
          이 도구는 <b>참가자 고지·동의를 전제로 한 회의 기록 도구</b>이며, 탐지 회피(스텔스)
          기능 — 창 숨김, 프로세스 위장, 화면공유 회피 — 을 <b>의도적으로 제공하지 않습니다.</b>
        </p>
        <ul className="list-disc pl-5 space-y-1 mt-2">
          <li><b>녹음 전에 알리기.</b> 실시간 녹음·파일 업로드·폴더 감시 어느 경로든, 참석자에게 녹음과 자동 전사 사실을 먼저 알려 주세요.</li>
          <li><b>승인이 필요한 회의 구분.</b> 외부 참석자가 있거나 민감 정보가 오가는 회의는 사내 규정에 따라 사전 승인을 받으세요. 개인정보·인사 관련 논의는 녹취 대상에서 빼는 편이 안전합니다.</li>
          <li><b>산출물에 출처가 남습니다.</b> 만들어진 회의록 노트에는 녹취 방식(업로드/실시간/폴더감시)·처리 시각·사용한 STT·LLM 모델·도구 버전이 자동으로 기록됩니다. 나중에 "이 회의록이 어떻게 만들어졌나"를 확인할 수 있고, 몰래 만든 기록이 아니라는 증거가 됩니다.</li>
          <li><b>데이터는 이 PC 에 남습니다.</b> 오디오·회의록·검색 인덱스는 전부 내 PC 에 저장되고, 외부로 나가는 것은 STT·LLM API 호출 내용뿐입니다(내 API 키 사용). 다만 <b>로컬 실행이 곧 정책 준수는 아닙니다</b> — 위의 고지·승인은 별개로 지켜야 합니다.</li>
        </ul>
      </Card>

      <p className="text-center text-xs text-brand-500 mt-4">
        헷갈리면 언제든 <button onClick={openWizard} className="text-brand-700 underline font-semibold">설정 마법사</button>를 다시 열어 순서대로 따라 하세요.
      </p>
    </div>
  );
}
