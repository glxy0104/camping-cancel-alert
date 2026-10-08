"""숲나들e(foresttrip.go.kr) 휴양림 조회 — 로그인 불필요.

동작 방식 (2026-10 기준 확인):
  1. GET  /main.do → JSESSIONID 쿠키 + HTML 안의 _csrf 토큰
  2. POST /rep/or/selectRsrvtAvailInfoListForMonthRsrvtSmpl.do (JSON, X-CSRF-Token 헤더)
     body: insttId, upperGoodsClsscCd(01 숙박 / 02 야영), srchDate, lastDay, inqurSctin=02
     → 호실/데크 단위 × 날짜 행 목록 (오늘부터 lastDay 까지)
  3. rsrvtAvail=="Y" 이고 rsrvtCnt==0 이면 빈자리.
     취소분이 대기자에게 먼저 갈 수 있어서, 대기 정원(goodsMxmmWtngCnt)에 여유가 생기는 것도 따로 감지.

토큰/쿠키 없으면 302 → accessDenied, 응답이 JSON 이 아니거나 비면 예외로 처리(오류 알림 대상).
"""
import logging
import re

import requests

from core import config as cfg

log = logging.getLogger(__name__)

BASE = "https://www.foresttrip.go.kr"
MONTH_URL = BASE + "/rep/or/selectRsrvtAvailInfoListForMonthRsrvtSmpl.do"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)
CATEGORIES = ("01", "02")  # 01 숙박(숲속의집 등), 02 야영(데크 등)


def fetch(target, months, delay_range=(3, 10)):
    """→ {(YYYY-MM-DD, 시설명): 빈자리 수}. months 는 쓰지 않고 target 의 목표 날짜를 직접 사용."""
    dates = {d.replace("-", "") for d in cfg.target_dates(target["dates"])}
    if not dates:
        return {}

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    resp = session.get(BASE + "/main.do", timeout=30)
    resp.raise_for_status()
    m = re.search(r'name="_csrf" value="([^"]+)"', resp.text)
    if not m:
        raise RuntimeError("main.do 에서 _csrf 토큰을 찾지 못함")

    rows = []
    for cat in CATEGORIES:
        resp = session.post(
            MONTH_URL,
            json={"insttId": target["params"]["insttId"], "upperGoodsClsscCd": cat,
                  "srchDate": min(dates), "lastDay": max(dates), "inqurSctin": "02"},
            headers={"X-CSRF-Token": m.group(1), "X-Requested-With": "XMLHttpRequest",
                     "Referer": BASE + "/main.do"},
            timeout=30,
            allow_redirects=False,
        )
        if resp.status_code != 200:
            raise RuntimeError("월별현황 API HTTP {} (category={})".format(resp.status_code, cat))
        rows += resp.json()

    rows = [r for r in rows if r.get("useDt") in dates]
    if not rows:
        raise RuntimeError("월별현황 API 응답에 목표 날짜 행이 없음 (구조 변경 의심)")

    slots = {}
    for r in rows:
        date = "{}-{}-{}".format(r["useDt"][:4], r["useDt"][4:6], r["useDt"][6:8])
        unit = "{} {}".format(r.get("goodsClsscNm", ""), r.get("goodsNm", "")).strip()
        open_ = r.get("rsrvtAvail") == "Y"
        booked = int(r.get("rsrvtCnt") or 0)
        slots[(date, unit)] = 1 if open_ and booked == 0 else 0
        wait_free = int(r.get("goodsMxmmWtngCnt") or 0) - int(r.get("wtngCnt") or 0)
        slots[(date, unit + " 대기신청")] = max(wait_free, 0) if open_ and booked > 0 else 0
    return slots
