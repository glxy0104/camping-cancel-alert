"""config.yaml 로드 + 목표 날짜 계산."""
import calendar
import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

import yaml

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config.yaml"

WEEKDAY_MAP = {"MON": 0, "TUE": 1, "WED": 2, "THU": 3, "FRI": 4, "SAT": 5, "SUN": 6}


def load_config(path=CONFIG_PATH):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def today_kst():
    return datetime.datetime.now(ZoneInfo("Asia/Seoul")).date()


def target_dates(dates_cfg):
    """dates 설정(months + weekdays + extra)을 YYYY-MM-DD 리스트로 확장. 오늘(한국 시간) 이전 날짜는 뺀다."""
    result = set()
    weekdays = [WEEKDAY_MAP[w.upper()] for w in dates_cfg.get("weekdays", [])]
    for month in dates_cfg.get("months", []):
        year, mon = int(month[:4]), int(month[5:7])
        _, last_day = calendar.monthrange(year, mon)
        for day in range(1, last_day + 1):
            d = datetime.date(year, mon, day)
            if d.weekday() in weekdays:
                result.add(d.isoformat())
    for extra in dates_cfg.get("extra", []) or []:
        result.add(str(extra))
    today = today_kst().isoformat()
    return sorted(d for d in result if d >= today)


def target_months(dates_cfg):
    """조회해야 할 달(YYYY-MM) 목록."""
    return sorted({d[:7] for d in target_dates(dates_cfg)})
