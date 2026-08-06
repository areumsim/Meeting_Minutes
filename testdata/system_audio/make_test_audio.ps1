<#
make_test_audio.ps1 — 시스템 오디오 캡처 테스트용 한국어 음원 만들기
=====================================================================
Windows 내장 음성 합성(SAPI, Microsoft Heami ko-KR)으로 `회의_테스트.wav` 를 만든다.
네트워크·API 키·비용이 필요 없고, 같은 대본이므로 **몇 번을 돌려도 같은 음원**이다.

왜 유튜브 영상을 내려받지 않는가 — 유튜브 이용약관이 다운로드를 금지하고, 무엇보다
**정답 대본이 없어서** 전사 정확도를 비교할 수 없다. 이 방식은 대본이 곧 정답이다
(`회의_테스트_대본.txt`). "실제 사람 목소리"가 필요하면 그때는 아무 영상을 **재생만**
하면서 녹음하면 된다 — 캡처 여부 확인에는 저장이 필요하지 않다.

사용:
    powershell -ExecutionPolicy Bypass -File make_test_audio.ps1
    powershell -ExecutionPolicy Bypass -File make_test_audio.ps1 -Rate -1 -Out 느린판.wav

인수:
    -Rate  낭독 속도(-10~10, 기본 0). 느리게 하면 STT 가 유리해진다.
    -Out   출력 파일명(기본 회의_테스트.wav)
#>
param(
    [int]$Rate = 0,
    [string]$Out = '회의_테스트.wav'
)
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$scriptFile = Join-Path $here '회의_테스트_대본.txt'
$outFile = Join-Path $here $Out

if (-not (Test-Path $scriptFile)) { throw "대본이 없습니다: $scriptFile" }

# 대본에서 낭독 구간만 뽑는다 — 파일 앞부분의 설명·기대결과는 읽지 않는다.
# 구분선을 기준으로 자르므로, 대본을 고칠 때 이 스크립트는 건드릴 필요가 없다.
$lines = Get-Content $scriptFile -Encoding UTF8
$start = ($lines | Select-String -SimpleMatch '여기서부터 낭독 내용' | Select-Object -First 1).LineNumber
$end = ($lines | Select-String -SimpleMatch '낭독 끝' | Select-Object -First 1).LineNumber
if (-not $start -or -not $end) { throw '대본에서 낭독 구간 표시를 찾지 못했습니다.' }
$body = $lines[$start..($end - 2)] | Where-Object { $_.Trim() -ne '' }
if (-not $body) { throw '낭독할 문장이 없습니다.' }
Write-Host ("낭독 문장: " + $body.Count + "개")

Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    # 한국어 음성을 명시적으로 고른다. 없으면 기본 음성으로 진행하되 경고한다 —
    # 캡처 여부 확인에는 언어가 상관없지만 전사 비교에는 한국어가 필요하다.
    $ko = $synth.GetInstalledVoices() |
        Where-Object { $_.Enabled -and $_.VoiceInfo.Culture.Name -eq 'ko-KR' } |
        Select-Object -First 1
    if ($ko) {
        $synth.SelectVoice($ko.VoiceInfo.Name)
        Write-Host ("음성: " + $ko.VoiceInfo.Name + " (ko-KR)")
    } else {
        Write-Host '[경고] 한국어 음성이 없습니다 — 기본 음성으로 만듭니다(전사 비교는 부정확).' -ForegroundColor Yellow
    }
    $synth.Rate = $Rate
    $synth.SetOutputToWaveFile($outFile)

    # 앞뒤 무음 3초 — 무음 청크 드롭(realtime.drop_silent_chunks)이 도는지 보는 구간이다.
    # 문장 사이 1초 쉼은 발화 경계 분할(silence_hold)이 동작하게 한다.
    $ssmlBody = ($body | ForEach-Object {
        '<s>' + ([System.Security.SecurityElement]::Escape($_.Trim())) + '</s><break time="1000ms"/>'
    }) -join "`n"
    $ssml = @"
<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="ko-KR">
<break time="3000ms"/>
$ssmlBody
<break time="3000ms"/>
</speak>
"@
    $synth.SpeakSsml($ssml)
} finally {
    $synth.SetOutputToNull()
    $synth.Dispose()
}

$info = Get-Item $outFile
# WAV 헤더에서 실제 길이를 읽는다(파일 크기 나눗셈은 포맷이 바뀌면 틀린다).
$bytes = [System.IO.File]::ReadAllBytes($outFile)
$sampleRate = [BitConverter]::ToInt32($bytes, 24)
$byteRate = [BitConverter]::ToInt32($bytes, 28)
$dataLen = $info.Length - 44
$sec = if ($byteRate -gt 0) { [math]::Round($dataLen / $byteRate, 1) } else { 0 }
Write-Host ""
Write-Host ("만들었습니다: " + $outFile)
Write-Host ("  " + [math]::Round($info.Length / 1KB) + " KB · " + $sec + "초 · " + $sampleRate + " Hz")
Write-Host ""
Write-Host '이제 README.md 의 절차대로 [내 마이크 + 이 PC 소리] 로 녹음하며 이 파일을 재생하세요.'
