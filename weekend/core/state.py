"""직전 조회 상태 저장/비교 — 중복 알림 방지."""
import json
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
STATE_PATH = BASE_DIR / "data" / "state.json"


def load_state():
    if STATE_PATH.exists():
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"slots": {}, "meta": {}}


def save_state(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["meta"]["updated_at"] = datetime.now().isoformat(timespec="seconds")
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def slot_key(site_key, date, zone):
    return "{}|{}|{}".format(site_key, date, zone)


def diff_new_availability(state, site_key, current):
    """current: {(date, zone): count}
    직전 상태에서 0(또는 미존재)이었다가 이번에 1 이상이 된 슬롯을 반환.
    상태도 갱신한다."""
    newly_available = []
    for (date, zone), count in current.items():
        key = slot_key(site_key, date, zone)
        prev = state["slots"].get(key, {}).get("count", 0)
        if count > 0 and prev == 0:
            newly_available.append({"date": date, "zone": zone, "count": count})
        state["slots"][key] = {
            "count": count,
            "checked_at": datetime.now().isoformat(timespec="seconds"),
        }
    return newly_available
