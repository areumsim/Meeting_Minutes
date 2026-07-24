import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'com.meetingminutes.app',
  appName: 'AI 회의록',
  webDir: 'dist',
  server: {
    androidScheme: 'https',
    // iosScheme는 http로 둔다: 앱이 http://localhost 에서 로드되면(localhost는 http여도
    // 보안 컨텍스트라 마이크 getUserMedia 정상) 같은 WiFi PC의 평문 http://192.168.x.x:8501
    // 백엔드로 fetch/WebSocket이 혼합 콘텐츠 차단 없이 통한다. https 스킴이면 WKWebView가
    // https 페이지 → http 요청을 막아 PC 연결 모드가 깨진다. OpenAI(https/wss) 직접 연결은
    // 보안 컨텍스트에서 그대로 허용된다.
    iosScheme: 'http',
    hostname: 'localhost',
    allowNavigation: [
      'api.openai.com',
      '*.openai.com',
    ],
  },
  ios: {
    contentInset: 'automatic',
    backgroundColor: '#ffffff',
    preferredContentMode: 'mobile',
    allowsLinkPreview: false,
  },
  plugins: {
    SplashScreen: {
      launchAutoHide: true,
      androidScaleType: 'CENTER_CROP',
    },
    Keyboard: {
      resize: 'body',
      resizeOnFullScreen: true,
    },
    StatusBar: {
      style: 'dark',
    },
  },
};

export default config;
