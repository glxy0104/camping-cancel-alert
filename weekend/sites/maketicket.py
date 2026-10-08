"""광교호수공원 가족캠핑장 등 MakeTicket(smartix) 계열 조회.

동작 방식 (2026-07 기준 확인):
  1. GET  /ticket/{gd_seq}            → 세션 쿠키(JSESSIONID) 발급
  2. POST /camp/reserve/calendar.jsp  → 해당 월 달력 HTML 반환
     body: idkey, gd_seq, yyyymmdd(YYYYMM15), sd_date(동일)
  3. HTML 안의 f_SelectDateZone("YYYYMMDD","구역코드","시퀀스","n","잔여수")
     와 <li><a><span>잔여수</span>구역명</a></li> 구조를 파싱.

레이아웃이 바뀌면 아래 SELECTORS/정규식만 수정하면 된다.
"""
import logging
import re
import time

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

BASE = "https://forest.maketicket.co.kr"
TICKET_URL = BASE + "/ticket/{gd_seq}"
CALENDAR_URL = BASE + "/camp/reserve/calendar.jsp"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

# onclick='f_SelectDateZone( "20260719" , "CM000255" , "SD68203" , "1" , "18" );'
RE_SELECT = re.compile(
    r'f_SelectDateZone\(\s*"(\d{8})"\s*,\s*"([^"]*)"\s*,\s*"[^"]*"\s*,\s*"\d+"\s*,\s*"(\d+)"'
)


def fetch(target, months, delay_range=(3, 10)):
    """months: ["2026-08", ...] → {(YYYY-MM-DD, 구역명): 잔여수}"""
    params = target["params"]
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    resp = session.get(TICKET_URL.format(gd_seq=params["gd_seq"]), timeout=30)
    resp.raise_for_status()

    result = {}
    for i, month in enumerate(months):
        if i > 0:
            time.sleep(_rand_delay(delay_range))
        yyyymmdd = month.replace("-", "") + "15"
        resp = session.post(
            CALENDAR_URL,
            data={
                "idkey": params["idkey"],
                "gd_seq": params["gd_seq"],
                "yyyymmdd": yyyymmdd,
                "sd_date": yyyymmdd,
            },
            headers={"Referer": TICKET_URL.format(gd_seq=params["gd_seq"])},
            timeout=30,
        )
        resp.raise_for_status()
        parsed = _parse_calendar(resp.text)
        if not parsed:
            log.info("[%s] %s 달력에 예약 가능 슬롯 정보 없음(미오픈/마감)", target["key"], month)
        result.update(parsed)
    return result


def _parse_calendar(html):
    """달력 HTML → {(YYYY-MM-DD, 구역명): 잔여수}"""
    soup = BeautifulSoup(html, "html.parser")
    slots = {}
    for a in soup.select("td li a[onclick*='f_SelectDateZone']"):
        m = RE_SELECT.search(a.get("onclick", ""))
        if not m:
            continue
        raw_date, _area_code, count = m.group(1), m.group(2), int(m.group(3))
        date = "{}-{}-{}".format(raw_date[:4], raw_date[4:6], raw_date[6:8])
        span = a.find("span")
        zone = a.get_text(strip=True)
        if span:
            zone = zone.replace(span.get_text(strip=True), "", 1).strip()
        if zone:
            slots[(date, zone)] = count
    return slots


def _rand_delay(delay_range):
    import random
    return random.uniform(delay_range[0], delay_range[1])
