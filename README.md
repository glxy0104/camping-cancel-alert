# 캠핑장 취소표 알림 봇 🏕

공영/국립 캠핑장의 예약 오픈과 취소표(마감 → 예약가능 전환)를 감시해서 텔레그램으로 알려주는 봇.

## 감시 대상

| 캠핑장 | 예약 시스템 | 조회 주기 |
|---|---|---|
| 광교호수공원 가족캠핑장 | forest.maketicket.co.kr | 55초 |
| 율동공원 오토캠핑장 | camping.isdc.co.kr | 55초 |
| 천왕산 가족캠핑장 | yeyak.seoul.go.kr | 55초 |
| 국립공원공단 야영장 | reservation.knps.or.kr | 5분 (config에서 야영장 추가 시) |

감시 날짜와 대상은 [config.json](config.json)에서 수정한다 (`target_dates`: 입실일 목록).

## 알림 종류

- 🏕 **예약 가능!** — 오픈 전이던 날짜가 예약 가능으로 바뀜
- 🔥 **취소표 발생!** — 마감이던 날짜에 빈자리가 생김
- ℹ️ **예약 오픈 감지 (이미 마감)** — 판매가 열렸지만 이미 다 찬 상태
- ⚠️ 특정 사이트 조회가 30회 연속 실패하면 경고 (사이트 구조 변경 의심)

## 동작 구조 (GitHub Actions)

- `.github/workflows/monitor.yml`이 30분마다 시동을 시도하고, `concurrency` 그룹 때문에 이미 실행 중이면 대기열에서 기다린다.
- 실행된 잡은 약 5시간 45분 동안 `monitor.py --loop 345`로 루프를 돌며 사이트별 주기로 조회한다. 잡이 끝나면 대기 중이던 다음 잡이 몇 초 안에 이어받아 사실상 24시간 감시가 유지된다.
- 상태(`state.json`)는 변화가 있을 때 저장소에 커밋되어 잡이 바뀌어도 중복 알림이 없다.

## 설정 방법

1. **텔레그램 봇 만들기**: 텔레그램에서 `@BotFather`에게 `/newbot` → 봇 이름/아이디 입력 → **토큰** 받기. 만든 봇에게 아무 메시지나 하나 보낸 뒤 `https://api.telegram.org/bot<토큰>/getUpdates` 를 브라우저로 열면 `"chat":{"id": 숫자}` 가 **chat_id**.
2. **GitHub Secrets 등록**: 저장소 → Settings → Secrets and variables → Actions에 `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` 등록. (터미널이라면 `gh secret set TELEGRAM_BOT_TOKEN` / `gh secret set TELEGRAM_CHAT_ID`)
3. Actions 탭에서 `camping-monitor` 워크플로를 `Run workflow`로 한 번 시작.

## 로컬 테스트

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python monitor.py --once --dry-run   # 알림 없이 현재 상태만 조회
```

## 국립공원 야영장 추가

`config.json`의 `sites.knps`를 `"enabled": true`로 바꾸고 `campgrounds`에 야영장을 추가:

```json
"campgrounds": { "설악동": "B031005", "북한산 사기막": "B141003" }
```

야영장별 dept_id는 config 하단 `_knps_dept_id_examples` 참고 (전체 48곳 목록은 reservation.knps.or.kr에서 확인 가능).

## 주의

- 조회 전용이며 예약은 직접 해야 한다. 알림이 오면 **빨리** 들어가서 잡아야 함 (취소표는 금방 사라짐).
- 국립공원 예약 조회는 "내일 ~ 다음 달 말"까지만 가능하므로 그보다 먼 날짜는 오픈 전으로 표시된다.
