# -*- coding: utf-8 -*-
"""
국립/공영 캠핑장 취소표 알림 모니터
- 광교호수공원 가족캠핑장 (forest.maketicket.co.kr)
- 율동공원 오토캠핑장 (camping.isdc.co.kr)
- 천왕산 가족캠핑장 (yeyak.seoul.go.kr)
- 국립공원공단 야영장 (reservation.knps.or.kr)

사용법:
  python monitor.py --once            # 1회 조회 후 현재 상태 출력
  python monitor.py --once --dry-run  # 텔레그램 발송/상태저장 없이 조회만
  python monitor.py --loop 340        # 340분 동안 주기적으로 감시 (GitHub Actions용)

환경변수: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
STATE_PATH = os.path.join(BASE_DIR, "state.json")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")

KST = timezone(timedelta(hours=9))
WEEKDAY_KR = ["월", "화", "수", "목", "금", "토", "일"]

# 상태값
AVAILABLE = "available"   # 예약 가능 (잔여 있음)
FULL = "full"             # 오픈됐지만 매진
NOT_OPEN = "not_open"     # 아직 판매/예약 오픈 전
CLOSED = "closed"         # 휴장일 등 이용 불가
ERROR = "error"


def now_kst():
    return datetime.now(KST)


def date_label(d):
    """'2026-10-02' -> '10/2(금)'"""
    dt = datetime.strptime(d, "%Y-%m-%d")
    return "%d/%d(%s)" % (dt.month, dt.day, WEEKDAY_KR[dt.weekday()])


def log(msg):
    print("[%s] %s" % (now_kst().strftime("%m-%d %H:%M:%S"), msg), flush=True)


# ---------------------------------------------------------------- 체커들
# 각 체커는 {"2026-10-02": {"status": ..., "detail": "..."}} 형태를 반환한다.

def check_gwanggyo(site_cfg, target_dates):
    """광교호수공원: forest.maketicket.co.kr 달력 (월 단위, 무인증)"""
    zone_names = {
        "CM000255": "오토캠핑(나무데크)",
        "CM000256": "오토캠핑(잔디데크)",
        "CM000254": "캐러반",
    }
    months = sorted({d[:7] for d in target_dates})
    results = {d: {"status": NOT_OPEN, "detail": ""} for d in target_dates}
    for month in months:
        ym = month.replace("-", "") + "01"
        r = requests.post(
            "https://forest.maketicket.co.kr/camp/reserve/calendar.jsp",
            data={"idkey": "5M4255", "gd_seq": "GD129",
                  "yyyymmdd": ym, "sd_date": ym},
            headers={"User-Agent": UA,
                     "Referer": "https://forest.maketicket.co.kr/ticket/GD129"},
            timeout=30)
        r.raise_for_status()
        html = r.text
        # f_SelectDateZone( "20261002" , "CM000255" , "SD..." , "1" , "17" )
        pat = re.compile(
            r'f_SelectDateZone\(\s*"(\d{8})"\s*,\s*"([A-Z0-9]+)"\s*,'
            r'\s*"[A-Z0-9]+"\s*,\s*"\d+"\s*,\s*"(\d+)"')
        by_date = {}
        for ymd, zone, remain in pat.findall(html):
            d = "%s-%s-%s" % (ymd[:4], ymd[4:6], ymd[6:8])
            by_date.setdefault(d, []).append(
                (zone_names.get(zone, zone), int(remain)))
        for d in target_dates:
            if d[:7] != month:
                continue
            zones = by_date.get(d)
            if zones is None:
                results[d] = {"status": NOT_OPEN, "detail": "판매 오픈 전"}
            else:
                avail = [(n, c) for n, c in zones if c > 0]
                if avail:
                    detail = ", ".join("%s %d자리" % (n, c) for n, c in avail)
                    results[d] = {"status": AVAILABLE, "detail": detail}
                else:
                    results[d] = {"status": FULL, "detail": "전 구역 마감"}
    return results


def check_yuldong(site_cfg, target_dates):
    """율동공원: camping.isdc.co.kr calendar.do (월 단위, 무인증)"""
    months = sorted({d[:7] for d in target_dates})
    results = {d: {"status": NOT_OPEN, "detail": ""} for d in target_dates}
    for month in months:
        r = requests.post(
            "https://camping.isdc.co.kr/camping/calendar.do",
            data={"pageId": "A98285584", "groupCode": "ydpc",
                  "selectMonth": month, "device": "pc",
                  "pageType": "reservation"},
            headers={"User-Agent": UA},
            timeout=30)
        r.raise_for_status()
        html = r.text
        # 날짜 id 등장 위치 기준으로 셀 블록을 슬라이스 (태그 속성 순서가 제각각이라)
        marks = [(m.group(1), m.start())
                 for m in re.finditer(r'id="(\d{4}-\d{2}-\d{2})"', html)]
        blocks = {}
        for i, (d, pos) in enumerate(marks):
            end = marks[i + 1][1] if i + 1 < len(marks) else len(html)
            blocks[d] = html[pos:end]
        for d in target_dates:
            if d[:7] != month:
                continue
            block = blocks.get(d)
            if block is None:
                results[d] = {"status": NOT_OPEN, "detail": "달력에 없음"}
                continue
            # 셀 여는 태그(첫 '>' 전)에 있는 class에서 click 여부 판단
            cls = ""
            mc = re.search(r'class="([^"]*)"', block.split(">", 1)[0])
            if mc:
                cls = mc.group(1)
            # 범례 영역까지 읽지 않도록 블록 내에서만 휴장 여부 확인
            if "stopReason" in block:
                results[d] = {"status": CLOSED, "detail": "휴장일"}
                continue
            cats = re.findall(r"<dt>\s*(\d+)\s*</dt>\s*<dd>([^<]+)</dd>", block)
            if "click" not in cls.split() and not cats:
                results[d] = {"status": NOT_OPEN, "detail": "예약 오픈 전"}
                continue
            avail = [(name.strip(), int(cnt)) for cnt, name in cats
                     if int(cnt) > 0]
            if avail:
                detail = ", ".join("%s %d자리" % (n, c) for n, c in avail)
                results[d] = {"status": AVAILABLE, "detail": detail}
            else:
                results[d] = {"status": FULL, "detail": "전 구역 마감"}
    return results


def check_cheonwangsan(site_cfg, target_dates):
    """천왕산 가족캠핑장: NOL(야놀자) stay tRPC API (무인증 JSON)
    1차로 달력(가벼움)에서 판매중 날짜를 거르고,
    판매중인 날짜만 상세 조회로 잔여 데크를 센다."""
    stay_id = 10070087
    base = "https://nol.yanolja.com/stay/api/trpc"
    headers = {"User-Agent": UA}
    nights = 1
    d_min, d_max = min(target_dates), max(target_dates)
    checkout = (datetime.strptime(d_min, "%Y-%m-%d")
                + timedelta(days=nights)).strftime("%Y-%m-%d")
    cal_input = json.dumps({"json": {"stayId": stay_id, "query": {
        "checkInDate": d_min, "checkOutDate": checkout,
        "from": d_min[:8] + "01", "to": d_max[:8] + "31",
        "adultPax": 2, "childrenAges": []}}})
    r = requests.get(base + "/stay.properties.calendar",
                     params={"input": cal_input}, headers=headers, timeout=30)
    r.raise_for_status()
    data = r.json()["result"]["data"]["json"]
    on_sale = {d["date"] for d in data.get("dates", []) if d.get("onSale")}

    results = {}
    for d in target_dates:
        if d not in on_sale:
            results[d] = {"status": FULL, "detail": "매진(판매 날짜에 없음)"}
            continue
        co = (datetime.strptime(d, "%Y-%m-%d")
              + timedelta(days=nights)).strftime("%Y-%m-%d")
        det_input = json.dumps({"json": {"stayId": stay_id, "query": {
            "checkInDate": d, "checkOutDate": co,
            "adultPax": 2, "childrenAges": []}}})
        rd = requests.get(base + "/stay.properties.detail",
                          params={"input": det_input}, headers=headers,
                          timeout=30)
        rd.raise_for_status()
        detail = rd.json()["result"]["data"]["json"]
        decks = []
        for rt in detail.get("roomTypes", []):
            for rp in rt.get("ratePlans", []):
                sold_out = (rp.get("price") or {}).get("soldOut", True)
                avail = (rp.get("validation") or {}).get("availability")
                if not sold_out and avail == "VALID":
                    decks.append((rt.get("roomTypeInfo") or {})
                                 .get("roomTypeName", "데크"))
                    break
        if decks:
            results[d] = {"status": AVAILABLE,
                          "detail": "데크 %d개 예약 가능 (%s)"
                                    % (len(decks), ", ".join(decks[:8]))}
        else:
            results[d] = {"status": FULL, "detail": "전 데크 매진"}
    return results


def check_knps(site_cfg, target_dates):
    """국립공원공단: campsiteList.do (야영장별, 무인증, 응답이 큼)
    config의 campgrounds: {"설악동": "B031005", ...}
    반환 키는 '야영장명|날짜' 형태."""
    results = {}
    target_ymd = {d.replace("-", ""): d for d in target_dates}
    for camp_name, dept_id in site_cfg.get("campgrounds", {}).items():
        r = requests.post(
            "https://reservation.knps.or.kr/reservation/campsiteList.do",
            data={"dept_id": dept_id},
            headers={"User-Agent": UA},
            timeout=120)
        r.raise_for_status()
        counts = {}  # ymd -> {"N": n, "C": n, "W": n}
        for ymd, code in re.findall(r'class="icon-[a-z-]+ (\d{8})_([A-Z])"',
                                    r.text):
            if ymd in target_ymd:
                counts.setdefault(ymd, {}).setdefault(code, 0)
                counts[ymd][code] += 1
        for ymd, d in target_ymd.items():
            key = "%s|%s" % (camp_name, d)
            c = counts.get(ymd)
            if not c:
                results[key] = {"status": NOT_OPEN,
                                "detail": "조회 기간 밖(오픈 전)"}
            elif c.get("N", 0) > 0:
                results[key] = {"status": AVAILABLE,
                                "detail": "%d개 영지 예약 가능" % c["N"]}
            elif c.get("W", 0) > 0:
                results[key] = {"status": FULL,
                                "detail": "매진 (대기신청 %d개 가능)" % c["W"]}
            else:
                results[key] = {"status": FULL, "detail": "전 영지 매진"}
    return results


# 숲나들e 익명 세션 캐시 (JSESSIONID + 짝이 맞는 CSRF 토큰)
_FT_SESSION = {"session": None, "csrf": None}


def _foresttrip_session(force=False):
    if _FT_SESSION["session"] is not None and not force:
        return _FT_SESSION["session"], _FT_SESSION["csrf"]
    s = requests.Session()
    s.headers["User-Agent"] = UA
    r = s.get("https://www.foresttrip.go.kr/rep/or/fcfsRsrvtMain.do"
              "?hmpgId=FRIP&menuId=001001", timeout=30)
    r.raise_for_status()
    m = re.search(r'name="_csrf" value="([^"]+)"', r.text)
    if not m:
        raise RuntimeError("숲나들e CSRF 토큰을 찾지 못함")
    _FT_SESSION["session"], _FT_SESSION["csrf"] = s, m.group(1)
    return s, m.group(1)


def check_foresttrip(site_cfg, target_dates):
    """국립자연휴양림 숲나들e: 날짜별 야영데크 선착순 잔여 수 (익명 세션 필요)
    날짜당 1회 검색으로 전국 카드가 오므로, 설정된 휴양림들을 insttId로 골라낸다.
    반환 키는 '휴양림명|날짜'."""
    forests = site_cfg.get("forests", {})  # {"중미산": "0108", ...}
    if not forests:
        return {}
    results = {}
    nights = 1
    for d in target_dates:
        bg = d.replace("-", "")
        ed = (datetime.strptime(d, "%Y-%m-%d")
              + timedelta(days=nights)).strftime("%Y%m%d")
        payload = {
            "srchInsttId": "", "srchRsrvtBgDt": bg, "srchRsrvtEdDt": ed,
            "srchStngNofpr": "2", "srchSthngCnt": str(nights),
            "houseCampSctin": "02", "rsrvtPssblYn": "N",
            "goodsClsscHouseCdArr": [], "goodsClsscCampCdArr": ["02002"],
            "srchInsttTpcd": [], "cmdogYn": "N", "bbqYn": "N",
            "dsprsYn": "N", "otsdWeterYn": "N", "wifiYn": "N",
            "snowPlaceYn": "N",
        }
        html = None
        for attempt in (1, 2):
            s, csrf = _foresttrip_session(force=(attempt == 2))
            r = s.post("https://www.foresttrip.go.kr/rep/or/"
                       "innerFcfsRcrfrDtlDetls.do?_csrf=" + csrf,
                       json=payload, timeout=30)
            if r.status_code == 200 and "rc_item" in r.text:
                html = r.text
                break
        if html is None:
            raise RuntimeError("숲나들e 검색 실패 (HTTP %s)" % r.status_code)
        # 카드 파싱: 각 rc_item 청크 끝에 insttId 스크립트가 붙는다
        by_instt = {}
        for chunk in html.split('<div class="rc_item">')[1:]:
            m_id = re.search(r'insttId:"(\d+)"', chunk)
            m_cnt = re.search(r"예약가능\s*객실\s*수\s*:\s*(\d+)", chunk)
            if m_id:
                by_instt[m_id.group(1)] = \
                    int(m_cnt.group(1)) if m_cnt else 0
        for f_name, instt_id in forests.items():
            key = "%s|%s" % (f_name, d)
            cnt = by_instt.get(instt_id)
            if cnt is None:
                results[key] = {"status": NOT_OPEN,
                                "detail": "검색 결과에 없음"}
            elif cnt > 0:
                results[key] = {"status": AVAILABLE,
                                "detail": "야영데크 %d개 예약 가능" % cnt}
            else:
                results[key] = {"status": FULL,
                                "detail": "선착순 잔여 없음"}
    return results


def check_donggang(site_cfg, target_dates):
    """동강전망자연휴양림 오토캠핑장: 정선군시설관리공단 위탁(huyang.co.kr)
    달력(무쿠키)으로 가능/마감 판별 후, 가능한 날짜만 상세로 잔여 데크 수 확인."""
    base = "https://jsimc.huyang.co.kr:453/reservation.asp"
    results = {}
    months = sorted({d[:7] for d in target_dates})
    day_status = {}  # 'YYYY-MM-DD' -> 'possible' | 'commit' | 'end'
    for month in months:
        yy, mm = month.split("-")
        r = requests.post(base + "?location=002",
                          data={"wh_year": yy, "wh_month": str(int(mm)),
                                "man": "1", "wloc": "C01", "change_stay": "0"},
                          headers={"User-Agent": UA}, timeout=30)
        r.raise_for_status()
        # <td class="open"> ... <span class="day...">2</span> ... possible
        for cell in r.text.split("<td")[1:]:
            cell = cell.split("</td>")[0]
            m = re.search(r'class="day[^"]*"\s*>\s*(\d+)\s*<', cell)
            if not m:
                continue
            d = "%s-%s-%02d" % (yy, mm, int(m.group(1)))
            if 'class="possible"' in cell or "예약가능" in cell:
                day_status[d] = "possible"
            elif 'class="commit"' in cell or "예약완료" in cell:
                day_status[d] = "commit"
            else:
                day_status[d] = "end"
    for d in target_dates:
        st = day_status.get(d)
        if st == "possible":
            # 상세 조회로 잔여 데크 수 (Referer 필수, 302면 전부 마감)
            dt = datetime.strptime(d, "%Y-%m-%d")
            rd = requests.post(
                base + "?location=002_01",
                data={"syyyy": dt.year, "smm": dt.month, "sdd": dt.day,
                      "edd": "0", "man": "1", "wloc": "C01"},
                headers={"User-Agent": UA,
                         "Referer": base + "?location=002&wloc=C01"},
                timeout=30, allow_redirects=False)
            if rd.status_code == 302:
                results[d] = {"status": FULL, "detail": "전 데크 마감"}
            else:
                decks = sorted(set(re.findall(r"데크\s*\d+", rd.text)))
                if decks:
                    results[d] = {"status": AVAILABLE,
                                  "detail": "데크 %d개 예약 가능 (%s)"
                                            % (len(decks),
                                               ", ".join(decks[:8]))}
                else:
                    results[d] = {"status": FULL, "detail": "전 데크 마감"}
        elif st == "commit":
            results[d] = {"status": FULL, "detail": "예약완료(마감)"}
        elif st == "end":
            results[d] = {"status": CLOSED, "detail": "예약종료/비운영"}
        else:
            results[d] = {"status": NOT_OPEN, "detail": "달력에 없음"}
    return results


CHECKERS = {
    "donggang": check_donggang,
    "gwanggyo": check_gwanggyo,
    "yuldong": check_yuldong,
    "cheonwangsan": check_cheonwangsan,
    "knps": check_knps,
    "foresttrip": check_foresttrip,
}


# ---------------------------------------------------------------- 텔레그램
def send_telegram(text, dry_run=False):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if dry_run or not token or not chat_id:
        log("(텔레그램 미발송%s) %s" %
            ("" if token else " - 토큰 없음", text.replace("\n", " | ")))
        return
    try:
        r = requests.post(
            "https://api.telegram.org/bot%s/sendMessage" % token,
            json={"chat_id": chat_id, "text": text,
                  "disable_web_page_preview": True},
            timeout=20)
        if r.status_code != 200:
            log("텔레그램 발송 실패: %s %s" % (r.status_code, r.text[:200]))
    except Exception as e:
        log("텔레그램 발송 오류: %s" % e)


# ---------------------------------------------------------------- 상태 관리
def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state, commit=False):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
    if commit and os.environ.get("GITHUB_ACTIONS") == "true":
        try:
            subprocess.run(["git", "config", "user.name", "camping-monitor-bot"],
                           cwd=BASE_DIR, check=False)
            subprocess.run(["git", "config", "user.email",
                            "bot@users.noreply.github.com"],
                           cwd=BASE_DIR, check=False)
            subprocess.run(["git", "add", "state.json"], cwd=BASE_DIR, check=False)
            rc = subprocess.run(["git", "commit", "-m", "상태 업데이트"],
                                cwd=BASE_DIR, check=False).returncode
            if rc == 0:
                subprocess.run(["git", "pull", "--rebase"], cwd=BASE_DIR,
                               check=False)
                subprocess.run(["git", "push"], cwd=BASE_DIR, check=False)
        except Exception as e:
            log("상태 커밋 실패(무시): %s" % e)


# ---------------------------------------------------------------- 비교/알림
def diff_and_alert(site_key, site_cfg, new_results, state, dry_run=False):
    """상태 전이를 감지해 알림을 보내고 state를 갱신. 변경 여부를 반환."""
    changed = False
    name = site_cfg["name"]
    url = site_cfg.get("booking_url", "")
    for key, res in sorted(new_results.items()):
        state_key = "%s|%s" % (site_key, key)
        prev = state.get(state_key, {}).get("status")
        cur = res["status"]
        # 표시용 라벨: knps는 '야영장명|날짜', 나머지는 '날짜'
        if "|" in key:
            camp, d = key.split("|", 1)
            label = "%s %s %s" % (name, camp, date_label(d))
        else:
            label = "%s %s" % (name, date_label(key))

        if cur != prev:
            changed = True
            # 알림은 "지금 잡을 수 있는 상태"가 됐을 때만 (오픈/마감 정보성 알림 없음)
            if cur == AVAILABLE:
                kind = "🔥 취소표 발생!" if prev == FULL else "🏕 예약 가능!"
                send_telegram("%s\n%s\n%s\n👉 %s"
                              % (kind, label, res["detail"], url), dry_run)
            log("변경: %s  %s -> %s (%s)" % (label, prev, cur, res["detail"]))
        state[state_key] = {"status": cur, "detail": res["detail"],
                            "checked_at": now_kst().isoformat()}
    return changed


# ---------------------------------------------------------------- 메인 루프
def run_pass(cfg, state, error_counts, dry_run=False, due_only=None):
    """모든(또는 due_only에 지정된) 사이트를 1회씩 조회."""
    any_change = False
    for site_key, site_cfg in cfg["sites"].items():
        if not site_cfg.get("enabled"):
            continue
        if due_only is not None and site_key not in due_only:
            continue
        checker = CHECKERS.get(site_key)
        if checker is None:
            continue
        try:
            results = checker(site_cfg, cfg["target_dates"])
            if error_counts.get(site_key, 0) >= 30:
                send_telegram("✅ %s 조회가 다시 정상화됐습니다."
                              % site_cfg["name"], dry_run)
            error_counts[site_key] = 0
            if diff_and_alert(site_key, site_cfg, results, state, dry_run):
                any_change = True
        except NotImplementedError:
            continue
        except Exception as e:
            error_counts[site_key] = error_counts.get(site_key, 0) + 1
            log("오류(%s, %d회 연속): %s"
                % (site_key, error_counts[site_key], e))
            if error_counts[site_key] == 30:
                send_telegram(
                    "⚠️ %s 조회가 30회 연속 실패 중입니다. "
                    "사이트 구조가 바뀌었을 수 있어요.\n마지막 오류: %s"
                    % (site_cfg["name"], e), dry_run)
    return any_change


def print_status(state):
    print("\n=== 현재 상태 ===")
    for key in sorted(state):
        s = state[key]
        print("  %-40s %-9s %s" % (key, s["status"], s.get("detail", "")))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="1회만 조회")
    ap.add_argument("--loop", type=int, metavar="MINUTES",
                    help="지정한 분 동안 반복 감시")
    ap.add_argument("--dry-run", action="store_true",
                    help="텔레그램 발송/상태 저장 없이 조회만")
    args = ap.parse_args()

    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    state = load_state()
    error_counts = {}

    if args.loop:
        deadline = time.time() + args.loop * 60
        next_check = {}  # site_key -> 다음 조회 시각
        log("감시 시작: %d분 동안, 대상 날짜 %s"
            % (args.loop, ", ".join(cfg["target_dates"])))
        while time.time() < deadline:
            now = time.time()
            due = [k for k, sc in cfg["sites"].items()
                   if sc.get("enabled") and next_check.get(k, 0) <= now]
            if due:
                changed = run_pass(cfg, state, error_counts,
                                   args.dry_run, due_only=set(due))
                for k in due:
                    next_check[k] = now + cfg["sites"][k].get("interval_sec", 60)
                if not args.dry_run:
                    save_state(state, commit=changed)
            wakeup = min([next_check.get(k, now + 60)
                          for k, sc in cfg["sites"].items()
                          if sc.get("enabled")] or [now + 60])
            time.sleep(max(1, min(wakeup - time.time(), 30)))
        log("감시 종료 (시간 만료)")
    else:
        run_pass(cfg, state, error_counts, args.dry_run)
        if not args.dry_run:
            save_state(state, commit=False)
        print_status(state)


if __name__ == "__main__":
    main()
