"""텔레그램 알림 발송."""
import logging
import os

import requests

log = logging.getLogger(__name__)

API_URL = "https://api.telegram.org/bot{token}/sendMessage"


def send_telegram(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id or token.startswith("123456789:AAAA"):
        log.warning("텔레그램 토큰/챗ID 미설정 — 알림을 콘솔에만 출력합니다.\n%s", text)
        return False
    resp = requests.post(
        API_URL.format(token=token),
        json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
        timeout=15,
    )
    if resp.status_code != 200:
        log.error("텔레그램 발송 실패 %s: %s", resp.status_code, resp.text[:300])
        return False
    return True


def notify_availability(site_name, booking_url, slots):
    """빈자리 발견 알림. slots: [{date, zone, count}]"""
    lines = ["🏕 빈자리 발견! — {}".format(site_name), ""]
    for s in slots:
        lines.append("· {} | {} | 잔여 {}자리".format(s["date"], s["zone"], s["count"]))
    lines += ["", "지금 예약: {}".format(booking_url)]
    return send_telegram("\n".join(lines))


def notify_error(site_name, message):
    return send_telegram("⚠️ 모니터링 오류 — {}\n{}".format(site_name, message))
