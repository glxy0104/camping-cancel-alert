#!/usr/bin/env python3
"""캠핑장 빈자리(취소분) 모니터링 — 진입점.

사용법:
  python monitor.py            # 1회 실행 (cron 용)
  python monitor.py --no-jitter  # 시작 랜덤 지연 없이 즉시 (테스트용)
  python monitor.py --dry-run    # 알림 없이 조회 결과만 출력
"""
import argparse
import logging
import random
import sys
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from core import config as cfg
from core import notify, state as st
from sites import foresttrip, maketicket, naverbooking, yuldong

FETCHERS = {
    "maketicket": maketicket.fetch,
    "yuldong": yuldong.fetch,
    "foresttrip": foresttrip.fetch,
    "naverbooking": naverbooking.fetch,
}

EXIT_ALL_DATES_PASSED = 10       # 남은 목표 날짜가 없을 때 종료 코드 (GitHub 루프가 보고 멈춘다)
ERROR_ALERT_THRESHOLD = 3          # 연속 실패 N회째에 오류 알림
ERROR_ALERT_COOLDOWN_SEC = 6 * 3600  # 오류 알림 최소 간격

log = logging.getLogger("monitor")


def setup_logging():
    log_dir = BASE_DIR / "logs"
    log_dir.mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fh = RotatingFileHandler(log_dir / "monitor.log", maxBytes=1_000_000,
                             backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(fh)
    root.addHandler(sh)


def check_target(target, conf, state, dry_run=False):
    """한 캠핑장 조회 → 목표 날짜 필터 → 신규 빈자리 알림."""
    key = target["key"]
    fetcher = FETCHERS.get(target["fetcher"])
    if fetcher is None:
        log.error("[%s] 알 수 없는 fetcher: %s", key, target["fetcher"])
        return

    months = cfg.target_months(target["dates"])
    dates = set(cfg.target_dates(target["dates"]))
    zones = target.get("zones") or []
    delay = conf.get("request_delay", {})
    delay_range = (delay.get("min", 3), delay.get("max", 10))

    all_slots = fetcher(target, months, delay_range)

    # 목표 날짜 + 목표 구역만 남기고, 조회됐지만 목표가 아닌 것은 버림
    current = {}
    for d in dates:
        found_zones = {z: c for (dt, z), c in all_slots.items() if dt == d}
        if zones:
            found_zones = {z: c for z, c in found_zones.items()
                           if any(want in z for want in zones)}
        if not found_zones:
            current[(d, "전체")] = 0     # 미오픈/마감 날짜도 0으로 기록해 오픈 감지
        else:
            for z, c in found_zones.items():
                current[(d, z)] = c

    fresh = st.diff_new_availability(state, key, current)
    open_count = sum(1 for c in current.values() if c > 0)
    log.info("[%s] 조회 완료 — 목표일 %d개 중 예약 가능 슬롯 %d건, 신규 %d건",
             key, len(dates), open_count, len(fresh))

    if fresh and not dry_run:
        notify.notify_availability(target["name"], target["booking_url"], fresh)
    elif fresh:
        log.info("[%s] (dry-run) 알림 생략: %s", key, fresh)

    # 성공했으니 오류 카운터 리셋
    state["meta"].setdefault("errors", {})[key] = {"count": 0}


def handle_error(target, state, exc, dry_run=False):
    key = target["key"]
    errors = state["meta"].setdefault("errors", {})
    info = errors.get(key, {"count": 0})
    info["count"] = info.get("count", 0) + 1
    info["last_error"] = str(exc)[:300]
    info["last_error_at"] = datetime.now().isoformat(timespec="seconds")
    log.exception("[%s] 조회 실패 (연속 %d회): %s", key, info["count"], exc)

    last_alert = info.get("alerted_at_ts", 0)
    if (info["count"] >= ERROR_ALERT_THRESHOLD
            and time.time() - last_alert > ERROR_ALERT_COOLDOWN_SEC
            and not dry_run):
        notify.notify_error(
            target["name"],
            "연속 {}회 조회 실패. 사이트 구조 변경/차단 여부를 확인하세요.\n마지막 오류: {}".format(
                info["count"], info["last_error"]),
        )
        info["alerted_at_ts"] = time.time()
    errors[key] = info


def finish(conf, state, dry_run=False):
    """모든 목표 날짜가 지났을 때: 조회하지 않고, 종료 알림을 한 번만 보낸다."""
    log.info("모든 목표 날짜가 지나 조회하지 않습니다. config.yaml 에 새 날짜를 넣으면 다시 시작합니다.")
    if state["meta"].get("finished_notified") or dry_run:
        return
    names = ", ".join(t["name"] for t in conf["targets"] if t.get("enabled"))
    notify.send_telegram("🛑 캠핑 알리미 종료 — 목표 날짜가 모두 지났습니다.\n({})\n새 날짜를 알려주시면 다시 켜드려요.".format(names))
    state["meta"]["finished_notified"] = True
    st.save_state(state)


def main():
    parser = argparse.ArgumentParser(description="캠핑장 빈자리 모니터링")
    parser.add_argument("--no-jitter", action="store_true",
                        help="시작 시 랜덤 지연(5~60초) 생략")
    parser.add_argument("--dry-run", action="store_true",
                        help="알림 발송 없이 조회/비교만 수행")
    parser.add_argument("--check-active", action="store_true",
                        help="조회 없이 남은 목표 날짜가 있는지만 확인 (있으면 0, 없으면 10으로 종료)")
    args = parser.parse_args()

    conf = cfg.load_config()
    targets = [t for t in conf["targets"] if t.get("enabled") and cfg.target_dates(t["dates"])]
    if args.check_active:
        sys.exit(0 if targets else EXIT_ALL_DATES_PASSED)

    setup_logging()
    load_dotenv(BASE_DIR / ".env")

    if not args.no_jitter:
        wait = random.uniform(5, 60)
        log.info("랜덤 지연 %.0f초 후 시작 (사이트 부담 방지)", wait)
        time.sleep(wait)

    state = st.load_state()
    if not targets:
        finish(conf, state, dry_run=args.dry_run)
        sys.exit(EXIT_ALL_DATES_PASSED)
    state["meta"].pop("finished_notified", None)  # 새 날짜가 생기면 종료 알림을 다시 보낼 수 있게

    for i, target in enumerate(targets):
        if i > 0:
            time.sleep(random.uniform(conf.get("request_delay", {}).get("min", 3),
                                      conf.get("request_delay", {}).get("max", 10)))
        try:
            check_target(target, conf, state, dry_run=args.dry_run)
        except Exception as exc:  # noqa: BLE001 — 한 사이트 실패가 전체를 막지 않도록
            handle_error(target, state, exc, dry_run=args.dry_run)

    st.save_state(state)


if __name__ == "__main__":
    main()
