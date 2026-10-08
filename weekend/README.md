# 캠핑장 빈자리(취소분) 모니터링

30분마다 예약 사이트를 확인해서, 예약 취소로 생긴 빈자리("불가 → 가능" 전환)를
감지하면 텔레그램으로 즉시 알려주는 도구입니다.

## 모니터링 대상

| 캠핑장 | 시스템 | 방식 |
|---|---|---|
| 광교호수공원 가족캠핑장 | MakeTicket ([forest.maketicket.co.kr/ticket/GD129](https://forest.maketicket.co.kr/ticket/GD129)) | requests (세션 쿠키) |
| 성남 율동공원 오토캠핑장 | [camping.isdc.co.kr](https://camping.isdc.co.kr/ydpc/camping) | Playwright (웹 대기열 통과 필요) |

- **목표 날짜**: 2026년 8월 매주 금·토 (체크인 기준 1박) — `config.yaml`에서 변경
- **감시 구역**: 전체 (특정 구역만 원하면 `zones: ["캐러반"]` 처럼 지정)
- 8월이 아직 예약 오픈 전이므로, **오픈되는 순간에도 알림**이 옵니다 (미오픈 = 0 으로 기록되기 때문).

## 설치

```bash
cd "05 예약알리미"
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium
```

## 텔레그램 설정 (.env)

1. 텔레그램에서 **@BotFather** 검색 → `/newbot` → 봇 이름 지정 → **토큰** 복사
2. 만든 봇을 검색해 **아무 메시지나 1개 전송** (봇이 나에게 말을 걸 수 있게 하는 절차)
3. 브라우저에서 `https://api.telegram.org/bot<토큰>/getUpdates` 접속 →
   `"chat":{"id": 123456789}` 의 숫자가 **챗 ID**
4. 설정 파일 생성:

```bash
cp .env.example .env
# .env 를 열어 TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID 입력
```

토큰이 없으면 알림 대신 로그에만 출력됩니다(모니터링 자체는 동작).

## 실행

```bash
# 1회 실행 (테스트 — 시작 지연 없이, 알림 없이)
.venv/bin/python monitor.py --no-jitter --dry-run

# 1회 실행 (실전 — cron/launchd 가 부르는 형태)
.venv/bin/python monitor.py
```

- 실행 결과·오류는 `logs/monitor.log` 에 기록됩니다.
- 직전 상태는 `data/state.json` 에 저장되어 **같은 빈자리로 중복 알림이 오지 않습니다**
  (다시 매진됐다가 또 빈자리가 나면 다시 알림).
- 연속 3회 조회 실패 시 오류 알림을 보냅니다 (6시간에 1번으로 제한).

## 30분 주기 자동 실행

### 방법 A — launchd (macOS 권장, 이미 등록됨)

`~/Library/LaunchAgents/com.camping.monitor.plist` 가 매시 00분/30분에 실행합니다.

```bash
# 상태 확인
launchctl list | grep com.camping.monitor
# 즉시 1회 실행해보기
launchctl kickstart gui/$(id -u)/com.camping.monitor
# 중지(해제)
launchctl bootout gui/$(id -u)/com.camping.monitor
# 재등록
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.camping.monitor.plist
```

### 방법 B — cron

```bash
crontab -e
# 아래 한 줄 추가 (경로는 실제 위치에 맞게)
*/30 * * * * cd "/Users/medilooks/Desktop/바이브코딩/16 알리미/05 예약알리미" && .venv/bin/python monitor.py >> logs/cron.log 2>&1
```

> **주의**: 두 방법 모두 **Mac이 켜져 있고 깨어 있을 때만** 실행됩니다.
> 잠자기 방지: `시스템 설정 > 배터리/에너지` 또는 터미널에서 `caffeinate -s` 실행.
> 8월 예약 오픈일 전후처럼 중요한 시기엔 Mac을 켜두세요.

## 구조

```
monitor.py          # 진입점: 조회 → 비교 → 알림 → 상태 저장
config.yaml         # 대상 캠핑장 / 목표 날짜 / 구역
core/config.py      # 설정 로드, 날짜 규칙(월+요일) 확장
core/state.py       # data/state.json 저장·비교 (중복 알림 방지)
core/notify.py      # 텔레그램 발송
sites/maketicket.py # 광교(MakeTicket) 조회 — 셀렉터/정규식 상단에 모음
sites/yuldong.py    # 율동(isdc) 조회 — 셀렉터 상단에 모음
```

새 캠핑장 추가 = `sites/` 에 fetcher 모듈 작성 → `monitor.py` 의 `FETCHERS` 에 등록
→ `config.yaml` 에 target 추가. (숲나들e/국립공원은 대상 확정 후 이 방식으로 추가 예정)

## 지키는 것

- 30분 간격 + 시작 시 5~60초 랜덤 지연 + 요청 사이 3~10초 랜덤 지연으로 사이트 부담 최소화
- 조회 실패 시 조용히 죽지 않고 로그 + (반복 시) 텔레그램 오류 알림
- `.env`(토큰) / `data/` / `logs/` 는 `.gitignore` 로 커밋 제외
