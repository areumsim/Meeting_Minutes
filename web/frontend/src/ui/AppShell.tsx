import React, { useState } from "react";
import { MoreHorizontal, Plus, PanelLeftClose, PanelLeftOpen, type LucideIcon } from "lucide-react";
import { Button, IconButton } from "./Button";
import BottomSheet from "./BottomSheet";

/**
 * 앱 셸 — 사이드바 + 탑바 + (모바일) 하단 탭바·FAB. PRD §4.1·§4.3·§5.4.
 *
 * IA 는 leaf 5 다(라이브러리 · 새로 만들기 · 지식 · 준비·비서 · 설정) + 하단 도움말.
 * **회의 상세는 내비 항목이 아니다** — 라이브러리 행·그래프·위키링크에서만 들어가는
 * 레코드 문맥 뷰다(리뷰 P1-3). 그래서 `activeKey` 와 실제 화면은 다를 수 있고, 그 매핑은
 * 호출부(App)가 정한다.
 *
 * 탑바는 최소로 둔다 — 제목 + ＋ 새 회의(§14-4). 전역 검색은 라이브러리에 있으므로
 * 중복 배치하지 않는다. `extra` 슬롯은 조용한 상태 배지(SSL 등)용이다.
 *
 * 모바일 내비를 이 파일이 함께 갖는 이유: 사이드바와 탭바가 **같은 항목 목록**에서
 * 나와야 한다. 두 벌로 두면 한쪽에만 화면이 추가돼 모바일에서 도달 경로가 0인 화면이
 * 생긴다(실제로 겪었다 — 회의 준비·비서·그래프·도움말이 모바일에서 열리지 않았다).
 */

export interface NavItem<K extends string = string> {
  key: K;
  label: string;
  icon: LucideIcon;
}

export interface AppShellProps<K extends string> {
  items: NavItem<K>[];
  /** 구분선 아래로 내릴 항목(설정). */
  footerItems?: NavItem<K>[];
  /** 사이드바 맨 아래 아이콘(도움말). */
  utilityItem?: NavItem<K>;
  activeKey: K;
  onNavigate: (key: K) => void;
  /** 하단 탭바에 직접 둘 항목 키. 나머지는 [더보기] 시트로 간다. */
  mobileTabs: K[];
  /** [더보기] 시트 내용 — 설정·도움말·PC 서버 연결·테마 등 호출부가 채운다. */
  moreContent?: (close: () => void) => React.ReactNode;
  title: string;
  subtitle?: React.ReactNode;
  /** 탑바 우측(＋ 새 회의 앞) — 조용한 상태 배지 자리. */
  extra?: React.ReactNode;
  onNewMeeting: () => void;
  /** 본문 위 전역 배너(config 손상·ffmpeg 없음). */
  banners?: React.ReactNode;
  children: React.ReactNode;
}

const SIDEBAR_KEY = "SIDEBAR_COLLAPSED";

export function AppShell<K extends string>({
  items, footerItems = [], utilityItem, activeKey, onNavigate, mobileTabs, moreContent,
  title, subtitle, extra, onNewMeeting, banners, children,
}: AppShellProps<K>) {
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem(SIDEBAR_KEY) === "1",
  );
  const [moreOpen, setMoreOpen] = useState(false);

  const toggleCollapsed = () => setCollapsed((c) => {
    const n = !c;
    try { localStorage.setItem(SIDEBAR_KEY, n ? "1" : "0"); } catch { /* ignore */ }
    return n;
  });

  const tabs = mobileTabs
    .map((k) => [...items, ...footerItems].find((i) => i.key === k))
    .filter((i): i is NavItem<K> => !!i);
  const moreActive = ![...tabs.map((t) => t.key)].includes(activeKey);

  return (
    <div className="flex h-[100dvh] flex-col md:flex-row">
      {/* ── 사이드바 (md 이상) ─────────────────────────────────── */}
      <nav
        aria-label="주요 메뉴"
        className={`hidden shrink-0 flex-col border-r border-line bg-surface pt-[env(safe-area-inset-top,0px)]
          ${collapsed ? "w-rail-collapsed" : "w-rail"} transition-[width] duration-150 md:flex`}
      >
        <div className={`flex items-center gap-2 px-3 pb-2 pt-3 ${collapsed ? "justify-center" : ""}`}>
          <span aria-hidden="true"
            className="grid h-6 w-6 shrink-0 place-items-center rounded-ctl bg-ink text-xs text-surface">
            ◈
          </span>
          {!collapsed && <b className="text-md font-bold tracking-tight">AI Minutes</b>}
          {!collapsed && (
            <span className="ml-auto">
              <IconButton icon={PanelLeftClose} label="사이드바 접기" size="sm" onClick={toggleCollapsed} />
            </span>
          )}
        </div>
        {collapsed && (
          <div className="flex justify-center pb-1">
            <IconButton icon={PanelLeftOpen} label="사이드바 펼치기" size="sm" onClick={toggleCollapsed} />
          </div>
        )}

        <div className="flex flex-col gap-0.5 px-2">
          {items.map((it) => (
            <NavButton key={it.key} item={it} active={it.key === activeKey}
              collapsed={collapsed} onClick={() => onNavigate(it.key)} />
          ))}
          {footerItems.length > 0 && <div className="mx-2 my-2 h-px bg-line" />}
          {footerItems.map((it) => (
            <NavButton key={it.key} item={it} active={it.key === activeKey}
              collapsed={collapsed} onClick={() => onNavigate(it.key)} />
          ))}
        </div>

        {utilityItem && (
          <div className="mt-auto px-2 pb-3">
            <NavButton item={utilityItem} active={utilityItem.key === activeKey}
              collapsed={collapsed} onClick={() => onNavigate(utilityItem.key)} />
          </div>
        )}
      </nav>

      {/* ── 본문 ───────────────────────────────────────────────── */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-topbar shrink-0 items-center gap-2.5 border-b border-line
          bg-surface px-3 pt-[env(safe-area-inset-top,0px)] md:px-4">
          <h2 className="truncate text-lg font-bold tracking-tight">{title}</h2>
          {subtitle && <span className="hidden truncate text-xs text-ink-3 sm:inline">{subtitle}</span>}
          <div className="flex-1" />
          {extra}
          <span className="hidden md:inline">
            <Button variant="primary" size="sm" icon={Plus} onClick={onNewMeeting}>새 회의</Button>
          </span>
        </header>

        {/* flex column 이어야 한다 — 녹음·회의 상세는 `flex-1 min-h-0` 으로 **자기 안에서만**
            스크롤하는 2-pane 이다. 여기가 블록 박스면 그 flex-1 이 아무 효과가 없어 전사
            패널이 내용만큼 늘어나고 페이지 전체가 길어진다(정지 버튼이 화면 밖으로 밀린다). */}
        <main className="flex min-h-0 flex-1 flex-col overflow-y-auto px-3 py-3 pb-24 md:px-4 md:pb-4">
          {/* 문서의 유일한 h1 — 모바일에도 존재해야 한다(사이드바는 display:none 이다). */}
          <h1 className="sr-only">AI Minutes — 회의록 자동화</h1>
          {banners && <div className="mb-3 space-y-2">{banners}</div>}
          {children}
        </main>
      </div>

      {/* ── 하단 탭바 + 중앙 FAB (md 미만) ─────────────────────── */}
      <nav aria-label="주요 메뉴"
        className="fixed inset-x-0 bottom-0 z-30 flex items-start border-t border-line
          bg-surface/95 px-1 pb-[env(safe-area-inset-bottom,0px)] pt-1 backdrop-blur md:hidden">
        {tabs.slice(0, 2).map((it) => (
          <TabButton key={it.key} item={it} active={it.key === activeKey}
            onClick={() => onNavigate(it.key)} />
        ))}
        <div className="flex flex-1 justify-center">
          <button type="button" onClick={onNewMeeting} aria-label="새 회의"
            className="-mt-3.5 grid h-12 w-12 place-items-center rounded-full bg-accent-solid
              text-on-accent shadow-pop">
            <Plus size={22} aria-hidden="true" />
          </button>
        </div>
        {tabs.slice(2).map((it) => (
          <TabButton key={it.key} item={it} active={it.key === activeKey}
            onClick={() => onNavigate(it.key)} />
        ))}
        <button type="button" onClick={() => setMoreOpen(true)}
          aria-haspopup="dialog" aria-expanded={moreOpen}
          className={`flex flex-1 flex-col items-center gap-0.5 py-1 text-2xs font-semibold
            ${moreActive ? "text-accent" : "text-ink-3"}`}>
          <MoreHorizontal size={20} aria-hidden="true" />
          더보기
        </button>
      </nav>

      {moreOpen && moreContent && (
        <BottomSheet labelledBy="more-sheet-title" title="더보기"
          heightClass="max-h-[70dvh]" onClose={() => setMoreOpen(false)}>
          {moreContent(() => setMoreOpen(false))}
        </BottomSheet>
      )}
    </div>
  );
}

function NavButton<K extends string>({ item, active, collapsed, onClick }: {
  item: NavItem<K>; active: boolean; collapsed: boolean; onClick: () => void;
}) {
  const Icon = item.icon;
  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={active ? "page" : undefined}
      title={collapsed ? item.label : undefined}
      aria-label={collapsed ? item.label : undefined}
      className={`flex items-center gap-2.5 rounded-ctl px-2.5 py-2 text-base transition-colors
        ${collapsed ? "justify-center" : ""}
        ${active ? "bg-accent-weak font-semibold text-accent" : "text-ink-2 hover:bg-hover"}`}
    >
      <Icon size={16} className="shrink-0" aria-hidden="true" />
      {!collapsed && <span className="truncate">{item.label}</span>}
    </button>
  );
}

function TabButton<K extends string>({ item, active, onClick }: {
  item: NavItem<K>; active: boolean; onClick: () => void;
}) {
  const Icon = item.icon;
  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={active ? "page" : undefined}
      className={`flex flex-1 flex-col items-center gap-0.5 py-1 text-2xs font-semibold
        ${active ? "text-accent" : "text-ink-3"}`}
    >
      <Icon size={20} aria-hidden="true" />
      <span className="truncate">{item.label}</span>
    </button>
  );
}

export default AppShell;
