"""네이버 예약(booking.naver.com) 조회 — 로그인 불필요. 궁평오솔로캠핑장 등.

동작 방식 (2026-10 기준 확인):
  POST https://booking.naver.com/graphql?opName=...  (쿠키/토큰 없음)
  1. searchBizItem → 사이트(bizItem) 목록. 사이트 1개 = bizItem 1개.
  2. schedule(bizItemId, 기간) → daily.date[YYYY-MM-DD] 에 stock / bookingCount / occupiedBookingCount
     잔여 = stock - bookingCount - occupiedBookingCount (isBusinessDay·isSaleDay 가 아니면 0).

비공식 내부 API라 응답에 errors 가 있거나 사이트 목록이 비면 예외 → 오류 알림 대상.
"""
import logging
import random
import time

import requests

from core import config as cfg

log = logging.getLogger(__name__)

GQL = "https://booking.naver.com/graphql?opName="
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0 Safari/537.36"
)
Q_ITEMS = ("query searchBizItem($bizItemSearchParams: BizItemSearchParams) { searchBizItem(input: $bizItemSearchParams)"
           " { bizItems { bizItemId name isClosedBooking isImp } } }")
Q_SCHEDULE = ("query schedule($scheduleParams: ScheduleParams) { schedule(input: $scheduleParams)"
              " { bizItemSchedule { daily { date } } } }")


def fetch(target, months, delay_range=(3, 10)):
    """→ {(YYYY-MM-DD, 사이트명): 잔여}. months 는 쓰지 않고 target 의 목표 날짜를 직접 사용."""
    dates = cfg.target_dates(target["dates"])
    if not dates:
        return {}
    biz_id = target["params"]["businessId"]
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Origin": "https://booking.naver.com",
                            "Referer": "https://booking.naver.com/booking/3/bizes/{}".format(biz_id)})

    def gql(op, variables, query):
        resp = session.post(GQL + op, json={"operationName": op, "variables": variables, "query": query}, timeout=30)
        resp.raise_for_status()
        body = resp.json()
        if body.get("errors"):
            raise RuntimeError("네이버 예약 GraphQL 오류: {}".format(str(body["errors"])[:200]))
        return body["data"]

    items = gql("searchBizItem", {"bizItemSearchParams": {"businessId": biz_id, "lang": "ko",
                                                          "projections": "RESOURCE"}}, Q_ITEMS)
    items = [it for it in items["searchBizItem"]["bizItems"] or []
             if it.get("isImp", True) and not it.get("isClosedBooking")]
    if not items:
        raise RuntimeError("네이버 예약 사이트 목록이 비어 있음 (businessId={})".format(biz_id))

    slots = {}
    for it in items:
        time.sleep(random.uniform(0.5, 1.5))  # 사이트당 1요청 — 짧게 간격만 둔다
        days = gql("schedule", {"scheduleParams": {
            "businessTypeId": 3, "businessId": biz_id, "bizItemId": it["bizItemId"],
            "startDateTime": "{}T00:00:00".format(min(dates)), "endDateTime": "{}T23:59:59".format(max(dates)),
            "fixedTime": True, "includesHolidaySchedules": True}}, Q_SCHEDULE)
        days = days["schedule"]["bizItemSchedule"]["daily"]["date"] or {}
        for d in dates:
            slots[(d, it["name"])] = _remaining(days.get(d, {}))
    return slots


def _remaining(day):
    if not (day.get("isBusinessDay") and day.get("isSaleDay")):
        return 0
    return max(0, (day.get("stock") or 0) - (day.get("bookingCount") or 0) - (day.get("occupiedBookingCount") or 0))


if __name__ == "__main__":
    assert _remaining({"isBusinessDay": True, "isSaleDay": True, "stock": 1, "bookingCount": 0}) == 1
    assert _remaining({"isBusinessDay": True, "isSaleDay": True, "stock": 1, "bookingCount": 1}) == 0
    assert _remaining({"isBusinessDay": False, "isSaleDay": True, "stock": 1, "bookingCount": 0}) == 0
    print("ok")
