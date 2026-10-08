"""성남 율동공원 오토캠핑장 조회 (camping.isdc.co.kr).

이 사이트는 예약 페이지 진입 시 mirihae 웹 대기열을 거쳐 gateToken 을
발급받아야 하므로 Playwright(headless Chromium)로 접근한다.

동작 방식 (2026-07 기준 확인):
  1. goto /ydpc/camping → 대기열 자동 통과 → /ydpc/camping/{pageId}?gateToken=...
  2. 페이지 안에서 POST /camping/calendar.do (searchForm + selectMonth=YYYY-MM)
     → 해당 월 달력 HTML 조각 반환
  3. <div id="YYYY-MM-DD"> 안의 <dl class="schedule"><dt>잔여수</dt><dd>구역명</dd>
     구조를 파싱. district 가 비어 있으면 그 날짜는 마감/미오픈.

레이아웃이 바뀌면 아래 SELECTORS 만 수정하면 된다.
"""
import logging
import random
import time

from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

ENTRY_URL = "https://camping.isdc.co.kr/ydpc/camping"

SELECTORS = {
    "search_form": "#searchForm",
    "date_cell": "div[id^='{month}-']",   # id="YYYY-MM-DD"
    "zone_entry": "dl.schedule",
    "zone_count": "dt",
    "zone_name": "dd",
}

# 페이지 컨텍스트에서 실행: searchForm 을 그대로 직렬화해 calendar.do 호출
JS_FETCH_MONTH = """
async (month) => {
  const f = document.querySelector('#searchForm');
  if (!f) return null;
  const p = new URLSearchParams();
  new FormData(f).forEach((v, k) => p.append(k, v));
  p.set('selectMonth', month);
  const r = await fetch('/camping/calendar.do', {
    method: 'POST',
    headers: {'Content-Type': 'application/x-www-form-urlencoded'},
    body: p.toString(),
  });
  if (!r.ok) return null;
  return await r.text();
}
"""


def fetch(target, months, delay_range=(3, 10)):
    """months: ["2026-08", ...] → {(YYYY-MM-DD, 구역명): 잔여수}"""
    from playwright.sync_api import sync_playwright

    result = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(ENTRY_URL, wait_until="domcontentloaded", timeout=60000)
            # 대기열을 거쳐 예약 페이지가 뜰 때까지 대기
            page.wait_for_selector(SELECTORS["search_form"], timeout=120000)

            for i, month in enumerate(months):
                if i > 0:
                    time.sleep(random.uniform(delay_range[0], delay_range[1]))
                html = page.evaluate(JS_FETCH_MONTH, month)
                if not html:
                    raise RuntimeError("calendar.do 응답 없음 (month={})".format(month))
                parsed = _parse_calendar(html, month)
                if not parsed:
                    log.info("[%s] %s 달력에 예약 가능 슬롯 정보 없음(미오픈/마감)", target["key"], month)
                result.update(parsed)
        finally:
            browser.close()
    return result


def _parse_calendar(html, month):
    """달력 HTML 조각 → {(YYYY-MM-DD, 구역명): 잔여수}"""
    soup = BeautifulSoup(html, "html.parser")
    slots = {}
    for cell in soup.select(SELECTORS["date_cell"].format(month=month)):
        date = cell.get("id", "")
        if len(date) != 10:
            continue
        for dl in cell.select(SELECTORS["zone_entry"]):
            dt = dl.select_one(SELECTORS["zone_count"])
            dd = dl.select_one(SELECTORS["zone_name"])
            if not dt or not dd:
                continue
            try:
                count = int(dt.get_text(strip=True))
            except ValueError:
                continue
            zone = dd.get_text(strip=True)
            if zone:
                slots[(date, zone)] = count
    return slots
