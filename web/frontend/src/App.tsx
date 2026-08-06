import React, { useState, useEffect, lazy, Suspense } from "react";
import {
  List, Plus, Network, Clock, Settings as SettingsIcon, HelpCircle, Loader2,
} from "lucide-react";
import { MotionConfig } from "motion/react";
import AppShell, { type NavItem } from "./ui/AppShell";
import { Banner, QuietBadge } from "./ui/Banner";
import { Button } from "./ui/Button";
import { SegmentedControl } from "./ui/Tabs";
import Onboarding from "./components/Onboarding";
import Library from "./screens/library/Library";
import {
  type View, type CreateTab, type KnowledgeTab, type PrepareTab, type Destination,
  navKeyOf, VIEW_TITLE, CREATE_TABS, KNOWLEDGE_TABS, PREPARE_TABS,
} from "./lib/nav";
import { getThemeChoice, setThemeChoice, type ThemeChoice } from "./lib/theme";
import { getApiKey, getConfig, isPackagedMode } from "./lib/api";

// 초기 로딩 번들을 줄이기 위해 라이브러리(기본 화면)·온보딩 외 뷰는 지연 로드.
const Recorder = lazy(() => import("./components/Recorder"));
const Detail = lazy(() => import("./screens/detail/Detail"));
const UploadForm = lazy(() => import("./screens/create/UploadForm"));
const TextForm = lazy(() => import("./screens/create/TextForm"));
const WikiAsk = lazy(() => import("./components/WikiAsk"));
const PrepBrief = lazy(() => import("./components/PrepBrief"));
const Help = lazy(() => import("./components/Help"));
const SettingsView = lazy(() => import("./components/Settings"));
const GraphExplorer = lazy(() => import("./components/GraphExplorer"));
const Assistant = lazy(() => import("./components/Assistant"));

/** 내비 leaf 5 — 회의 상세는 여기 없다(PRD §4.1, 리뷰 P1-3). */
const NAV: NavItem<View>[] = [
  { key: "library", label: "라이브러리", icon: List },
  { key: "create", label: "새로 만들기", icon: Plus },
  { key: "knowledge", label: "지식", icon: Network },
  { key: "prepare", label: "준비 · 비서", icon: Clock },
];
const NAV_FOOTER: NavItem<View>[] = [{ key: "settings", label: "설정", icon: SettingsIcon }];
const NAV_UTILITY: NavItem<View> = { key: "help", label: "도움말", icon: HelpCircle };
/** 하단 탭 3 + 중앙 FAB(AppShell) + [더보기]. 설정·도움말은 시트로 간다. */
const MOBILE_TABS: View[] = ["library", "knowledge", "prepare"];

/** 도움말이 쓰던 옛 화면 이름 → 새 IA. 본문 카피는 도움말 재구현 커밋에서 다시 쓴다. */
const LEGACY_TARGET: Record<string, Destination> = {
  dashboard: { view: "library" },
  recorder: { view: "create", tab: "record" },
  upload: { view: "create", tab: "upload" },
  text: { view: "create", tab: "text" },
  wiki: { view: "knowledge", tab: "ask" },
  graph: { view: "knowledge", tab: "graph" },
  prep: { view: "prepare", tab: "prep" },
  assistant: { view: "prepare", tab: "assistant" },
  settings: { view: "settings" },
};

export default function App() {
  const [view, setViewState] = useState<View>("library");
  const [createTab, setCreateTab] = useState<CreateTab>("record");
  const [knowledgeTab, setKnowledgeTab] = useState<KnowledgeTab>("ask");
  const [prepareTab, setPrepareTab] = useState<PrepareTab>("prep");
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [graphQuery, setGraphQuery] = useState("");
  const [ffmpegMissing, setFfmpegMissing] = useState(false);
  // config.json 을 읽지 못한 상태. 이때는 서버가 저장을 막으므로(기존 설정 보호)
  // 사용자에게 반드시 알려야 한다 — 포터블은 콘솔이 없어 stderr 경고가 안 보인다.
  const [configError, setConfigError] = useState<string | null>(null);
  const [recovering, setRecovering] = useState(false);
  // ssl.verify 를 끈 상태. 기본값이 안전(ON)해진 뒤로 상시 배너는 "늘 뭔가 잘못된 앱"이라는
  // 인상만 남기고 아무도 읽지 않았다(PRD §1.2·§10) → topbar 의 조용한 배지로 내린다.
  const [sslInsecure, setSslInsecure] = useState(false);
  const [showOnboarding, setShowOnboarding] = useState(false);
  const [theme, setTheme] = useState<ThemeChoice>(getThemeChoice);

  // 손상된 config 에서 빠져나오기. 어느 쪽이든 손상 파일은 지우지 않고 보관한다.
  const recoverConfig = async (restoreBackup: boolean) => {
    if (!window.confirm(restoreBackup
      ? "마지막 정상 설정(config.json.bak)으로 되돌립니다. 손상된 파일은 지우지 않고 따로 보관합니다. 계속할까요?"
      : "손상된 설정을 따로 보관하고 빈 설정으로 시작합니다. API 키를 다시 입력해야 합니다. 계속할까요?")) return;
    setRecovering(true);
    try {
      const res = await fetch("/api/config/recover", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ restore_backup: restoreBackup }),
      });
      const r = await res.json().catch(() => ({}));
      if (res.ok && r.ok) {
        setConfigError(null);
        alert(r.message || "복구했습니다.");
        window.location.reload();       // 설정이 바뀌었으니 화면 전체를 다시 읽는다
      } else {
        alert(r.message || r.detail || "복구에 실패했습니다.");
      }
    } catch (e: any) {
      alert(`복구에 실패했습니다: ${e?.message || e}`);
    } finally {
      setRecovering(false);
    }
  };

  /** 화면 이동 — 녹음 중이면 먼저 묻는다(전역 플래그는 Recorder 가 세운다). */
  const go = (dest: Destination) => {
    if ((window as any).isRecordingActive) {
      if (!window.confirm("실시간 녹음이 진행 중입니다. 나가면 녹음이 중지됩니다. 계속할까요?")) return;
      (window as any).stopActiveRecording?.();
    }
    if (dest.tab) {
      if (dest.view === "create") setCreateTab(dest.tab as CreateTab);
      if (dest.view === "knowledge") setKnowledgeTab(dest.tab as KnowledgeTab);
      if (dest.view === "prepare") setPrepareTab(dest.tab as PrepareTab);
    }
    if (dest.view === "knowledge") setGraphQuery(dest.query || "");
    setViewState(dest.view);
  };

  // 최초 실행 유도(OpenAI 키 미설정) + ffmpeg·config·SSL 상태 확인
  useEffect(() => {
    (async () => {
      try {
        const res = await fetch("/api/health");
        if (res.ok) {
          const h = await res.json();
          setFfmpegMissing(h.ffmpeg_available === false);
          setConfigError(h.config_error || null);
          setSslInsecure(h.ssl_insecure === true);
        }
      } catch { /* 백엔드 없음(모바일) — 무시 */ }

      try {
        const packaged = await isPackagedMode();
        const cfg = await getConfig();
        const hasKey = !!(cfg?.api?.openai_api_key) || !!getApiKey();
        const dismissed = localStorage.getItem("ONBOARDING_DISMISSED") === "1";
        // 패키지 모드 + OpenAI 키 미설정 + 아직 마법사를 닫지 않았으면 온보딩 표시.
        if (packaged && !hasKey && !dismissed) setShowOnboarding(true);
        else if (!hasKey) setViewState("settings");
      } catch {
        if (!getApiKey()) setViewState("settings");
      }
    })();

    // [설정]의 '설정 마법사 다시 열기' 버튼에서 발생시키는 이벤트.
    const openHandler = () => setShowOnboarding(true);
    window.addEventListener("mm:open-onboarding", openHandler);
    return () => window.removeEventListener("mm:open-onboarding", openHandler);
  }, []);

  const openDetail = (id: string) => { setSelectedSessionId(id); setViewState("detail"); };
  /** 위키링크·노드 클릭 → 지식 그래프(그 대상을 자동 검색). */
  const openGraph = (query?: string) => go({ view: "knowledge", tab: "graph", query });

  const meta = VIEW_TITLE[view];
  const banners = (
    <>
      {configError && (
        <Banner tone="err" title="설정 파일(config.json)을 읽지 못했습니다."
          actions={
            <>
              <Button size="sm" variant="secondary" disabled={recovering}
                onClick={() => recoverConfig(true)}>마지막 정상 설정으로 되돌리기</Button>
              <Button size="sm" variant="secondary" disabled={recovering}
                onClick={() => recoverConfig(false)}>보관하고 새로 시작</Button>
            </>
          }>
          <p>({configError})</p>
          <p>
            기본값으로 동작 중이며, 기존 설정을 보호하기 위해 <b>설정 저장이 차단</b>돼 있습니다.
            파일을 직접 고치거나 아래에서 복구하세요.
          </p>
        </Banner>
      )}
      {ffmpegMissing && (
        <Banner title="ffmpeg가 설치되어 있지 않습니다.">
          오디오 파일 업로드/변환 기능이 동작하지 않을 수 있습니다. 프로그램 폴더의{" "}
          <code className="num">vendor/ffmpeg/</code> 에 <code className="num">ffmpeg.exe</code>를
          넣거나 시스템 PATH에 ffmpeg를 추가하세요.
        </Banner>
      )}
    </>
  );

  return (
    // reducedMotion="user": OS 의 '동작 줄이기'를 켠 사용자에게는 motion 애니메이션을
    // 생략한다. CSS 쪽은 index.css 의 전역 미디어쿼리가 담당한다.
    <MotionConfig reducedMotion="user">
      {showOnboarding && (
        <Onboarding onClose={() => { setShowOnboarding(false); setViewState("settings"); }} />
      )}

      <AppShell
        items={NAV}
        footerItems={NAV_FOOTER}
        utilityItem={NAV_UTILITY}
        activeKey={navKeyOf(view)}
        onNavigate={(key) => go({ view: key })}
        mobileTabs={MOBILE_TABS}
        moreContent={(close) => (
          <MoreSheet
            theme={theme}
            onTheme={(c) => { setTheme(c); setThemeChoice(c); }}
            onGo={(d) => { close(); go(d); }}
          />
        )}
        title={meta.title}
        subtitle={meta.subtitle}
        extra={sslInsecure ? (
          // 상시 배너가 아니라 조용한 배지 — 기본값이 안전해진 뒤로 배너는 읽히지 않는다.
          // 대신 눌렀을 때 위험과 **되돌리는 방법**을 함께 준다(방법이 없으면 그대로 둔다).
          <QuietBadge label="SSL 검증 꺼짐" title="SSL 인증서 검증이 꺼져 있습니다.">
            <p>API 키와 회의 내용이 검증 없는 연결로 전송됩니다.</p>
            <p>
              이 앱은 Windows 인증서 저장소를 신뢰하므로 사내망에서도 대개 켠 상태로
              동작합니다 — [설정] → 고급에서 <code className="num">SSL 인증서 검증</code>을
              다시 켜 보세요.
            </p>
            <Button size="sm" variant="secondary" onClick={() => go({ view: "settings" })}>
              설정 열기
            </Button>
          </QuietBadge>
        ) : undefined}
        onNewMeeting={() => go({ view: "create", tab: "record" })}
        banners={banners}
      >
        <Suspense fallback={
          <div className="flex justify-center py-24">
            <Loader2 className="animate-spin text-ink-3" size={28} />
          </div>
        }>
          {view === "library" && (
            <Library
              onSelectSession={openDetail}
              onNewUpload={() => go({ view: "create", tab: "upload" })}
              onNewRecord={() => go({ view: "create", tab: "record" })}
            />
          )}

          {view === "create" && (
            <>
              <SegmentedControl id="create" label="입력 방식" value={createTab}
                onChange={setCreateTab} items={CREATE_TABS} className="mb-3" />
              {createTab === "record" && (
                <Recorder onComplete={openDetail} onExit={() => go({ view: "library" })} />
              )}
              {createTab === "upload" && <UploadForm onComplete={openDetail} />}
              {createTab === "text" && <TextForm onComplete={openDetail} />}
            </>
          )}

          {view === "knowledge" && (
            <>
              <SegmentedControl id="knowledge" label="지식 보기" value={knowledgeTab}
                onChange={setKnowledgeTab} items={KNOWLEDGE_TABS} className="mb-3" />
              {knowledgeTab === "ask" && <WikiAsk />}
              {knowledgeTab === "graph" && <GraphExplorer initialQuery={graphQuery} />}
            </>
          )}

          {view === "prepare" && (
            <>
              <SegmentedControl id="prepare" label="준비·비서" value={prepareTab}
                onChange={setPrepareTab} items={PREPARE_TABS} className="mb-3" />
              {prepareTab === "prep" && <PrepBrief onSaved={openDetail} />}
              {prepareTab === "assistant" && <Assistant />}
            </>
          )}

          {view === "settings" && <SettingsView />}
          {view === "help" && (
            <Help onNavigate={(t) => go(LEGACY_TARGET[t] || { view: "library" })} />
          )}
          {view === "detail" && selectedSessionId && (
            <Detail id={selectedSessionId} onBack={() => go({ view: "library" })}
              onOpenGraph={openGraph} />
          )}
        </Suspense>
      </AppShell>
    </MotionConfig>
  );
}

/** 모바일 [더보기] — 탭바에 없는 화면과 테마 전환이 여기 모인다(PRD §4.3). */
function MoreSheet({
  theme, onTheme, onGo,
}: {
  theme: ThemeChoice;
  onTheme: (c: ThemeChoice) => void;
  onGo: (d: Destination) => void;
}) {
  return (
    <div className="space-y-2">
      <button type="button" onClick={() => onGo({ view: "settings" })}
        className="flex w-full items-center gap-2 rounded-card border border-line bg-surface px-3 py-3 text-base font-semibold">
        <SettingsIcon size={16} aria-hidden="true" /> 설정
      </button>
      <button type="button" onClick={() => onGo({ view: "help" })}
        className="flex w-full items-center gap-2 rounded-card border border-line bg-surface px-3 py-3 text-base font-semibold">
        <HelpCircle size={16} aria-hidden="true" /> 도움말
      </button>
      <div className="rounded-card border border-line bg-surface px-3 py-2.5">
        <p className="mb-1.5 text-sm font-semibold text-ink">화면 테마</p>
        <SegmentedControl id="theme" label="화면 테마" value={theme} onChange={onTheme}
          items={[
            { key: "system" as const, label: "시스템" },
            { key: "light" as const, label: "라이트" },
            { key: "dark" as const, label: "다크" },
          ]} />
      </div>
    </div>
  );
}
