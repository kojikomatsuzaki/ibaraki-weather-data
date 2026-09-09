#!/usr/bin/env python3

import html
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup


BASE_URL = "https://kasen-pref-ibaraki.jp"
ENTRY_STA_ID = 1
OUTPUT_DIR = Path("data/raw/rainfall")
MASTER_DIR = Path("data/master")
REQUEST_INTERVAL_SEC = 0.3
TEST_LIMIT = int(os.environ.get("TEST_LIMIT", "3"))
FALLBACK_STEPS = int(os.environ.get("FALLBACK_STEPS", "6"))
DIAGNOSTIC_STATION_ID = int(os.environ.get("DIAGNOSTIC_STATION_ID", "0"))
DIAGNOSTIC_STEPS = int(os.environ.get("DIAGNOSTIC_STEPS", "36"))


def get_latest_times(session, station_id=ENTRY_STA_ID):
    """雨量詳細ページHTMLから最新時刻情報を取得する。"""
    url = f"{BASE_URL}/Sta/RainfallSta?obsStaId={station_id}"
    response = session.get(url, timeout=30)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    node = soup.select_one("#latest-times")
    if node is None:
        raise RuntimeError("#latest-times が見つかりません")

    raw_value = node.get("value")
    if not raw_value:
        raise RuntimeError("#latest-times の value が空です")

    latest = json.loads(html.unescape(raw_value))
    return latest, soup, url


def get_station_master(session, obs_data_chg_time):
    """観測局マスタを取得する。"""
    url = (
        f"{BASE_URL}/Static/Common/"
        f"GetStaMasterList_{obs_data_chg_time}.json"
    )
    response = session.get(url, timeout=30)
    response.raise_for_status()
    return response.json(), url


def extract_rainfall_stations(master):
    """staKind=1 の雨量局だけを抽出する。"""
    stations = [station for station in master if station.get("staKind") == 1]
    stations.sort(key=lambda station: station.get("staId", 0))
    return stations


def rainfall_url(station_id, latest_time, obs_data_chg_time):
    return (
        f"{BASE_URL}/Static/Sta/"
        f"GetRainfallStaData_{latest_time}_{station_id}_{obs_data_chg_time}.json"
    )


def validate_station_id(data, station_id):
    actual_station_id = data.get("tableData", {}).get("staId")
    if actual_station_id != station_id:
        raise RuntimeError(
            f"staId mismatch: expected={station_id}, actual={actual_station_id}"
        )


def get_rainfall_with_fallback(session, station_id, start_time, obs_data_chg_time):
    """最新時刻を試し、404時のみ10分刻みで少し遡る。"""
    current = datetime.strptime(start_time, "%Y%m%d%H%M")

    for step in range(FALLBACK_STEPS + 1):
        candidate = (current - timedelta(minutes=10 * step)).strftime("%Y%m%d%H%M")
        url = rainfall_url(station_id, candidate, obs_data_chg_time)
        response = session.get(url, timeout=30)

        if response.status_code == 200:
            data = response.json()
            validate_station_id(data, station_id)
            return {
                "resolved_latest_time": candidate,
                "fallback_steps": step,
                "source_url": url,
                "data": data,
            }

        if response.status_code != 404:
            response.raise_for_status()

        if step < FALLBACK_STEPS:
            time.sleep(REQUEST_INTERVAL_SEC)

    raise RuntimeError(
        f"rainfall JSON not found within {FALLBACK_STEPS * 10} minutes "
        f"from {start_time}"
    )


def collect_page_indicators(soup):
    """欠測・閉局等の表示手掛かりになりそうな要素を収集する。"""
    keywords = ("欠測", "閉局", "未収集", "休止", "停止", "観測", "雨量")
    indicators = []
    seen = set()

    for tag in soup.find_all(["input", "div", "span", "td", "th", "p"]):
        value = tag.get("value")
        text = value if value is not None else tag.get_text(" ", strip=True)
        if not text:
            continue
        compact = " ".join(str(text).split())
        if not any(keyword in compact for keyword in keywords):
            continue
        if len(compact) > 240:
            compact = compact[:240]
        key = (tag.name, tag.get("id") or "", compact)
        if key in seen:
            continue
        seen.add(key)
        indicators.append(
            {
                "tag": tag.name,
                "id": tag.get("id") or None,
                "class": tag.get("class") or None,
                "text": compact,
            }
        )
        if len(indicators) >= 30:
            break

    return indicators


def diagnose_station(session, station_id):
    """特定局のマスタ・ページ状態・直近JSON有無を診断する。"""
    latest, soup, entry_url = get_latest_times(session, station_id)
    system_latest_time = latest["systemLatestTime"]
    rain_sta_latest_time = latest.get("rainStaLatestTime", system_latest_time)
    obs_data_chg_time = latest["obsDataChgTime"]
    retrieved_at = datetime.now(timezone.utc).isoformat()

    master, master_url = get_station_master(session, obs_data_chg_time)
    station = next((item for item in master if item.get("staKind") == 1 and item.get("staId") == station_id), None)

    print(f"=== station diagnostic: staId={station_id} ===")
    print("entryUrl:", entry_url)
    print("rainStaLatestTime:", rain_sta_latest_time)
    print("obsDataChgTime:", obs_data_chg_time)
    print("master:")
    print(json.dumps(station, ensure_ascii=False, indent=2))

    attempts = []
    found = None
    current = datetime.strptime(rain_sta_latest_time, "%Y%m%d%H%M")

    print(f"=== probe up to {DIAGNOSTIC_STEPS * 10} minutes ===")
    for step in range(DIAGNOSTIC_STEPS + 1):
        candidate = (current - timedelta(minutes=10 * step)).strftime("%Y%m%d%H%M")
        url = rainfall_url(station_id, candidate, obs_data_chg_time)
        response = session.get(url, timeout=30)
        attempts.append({"time": candidate, "status": response.status_code, "url": url})
        print(f"{candidate}: HTTP {response.status_code}")

        if response.status_code == 200:
            data = response.json()
            validate_station_id(data, station_id)
            found = {
                "resolved_latest_time": candidate,
                "source_url": url,
                "obs_time": data.get("tableData", {}).get("obsTime"),
                "data": data,
            }
            print("FOUND:", url)
            print("obsTime:", found["obs_time"])
            break

        if response.status_code != 404:
            response.raise_for_status()

        if step < DIAGNOSTIC_STEPS:
            time.sleep(REQUEST_INTERVAL_SEC)

    indicators = collect_page_indicators(soup)
    print("=== page indicators ===")
    for item in indicators:
        print(item)

    save_json(
        OUTPUT_DIR / "diagnostic" / f"{station_id}.json",
        {
            "retrieved_at": retrieved_at,
            "entry_url": entry_url,
            "master_url": master_url,
            "station": station,
            "latest_times": latest,
            "page_indicators": indicators,
            "probe_window_minutes": DIAGNOSTIC_STEPS * 10,
            "attempts": attempts,
            "found": found,
        },
    )


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.write("\n")


def main():
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "ibaraki-weather-data/0.1 "
                "(+https://github.com/kojikomatsuzaki/ibaraki-weather-data)"
            )
        }
    )

    if DIAGNOSTIC_STATION_ID > 0:
        diagnose_station(session, DIAGNOSTIC_STATION_ID)
        return

    latest, _, entry_url = get_latest_times(session)
    system_latest_time = latest["systemLatestTime"]
    rain_sta_latest_time = latest.get("rainStaLatestTime", system_latest_time)
    obs_data_chg_time = latest["obsDataChgTime"]
    retrieved_at = datetime.now(timezone.utc).isoformat()

    print("entryUrl:", entry_url)
    print("systemLatestTime:", system_latest_time)
    print("rainStaLatestTime:", rain_sta_latest_time)
    print("obsDataChgTime:", obs_data_chg_time)
    print("fallbackWindowMinutes:", FALLBACK_STEPS * 10)

    master, master_url = get_station_master(session, obs_data_chg_time)
    rainfall_stations = extract_rainfall_stations(master)

    print("全マスタ件数:", len(master))
    print("雨量局件数:", len(rainfall_stations))

    if TEST_LIMIT > 0:
        rainfall_stations = rainfall_stations[:TEST_LIMIT]
        print("テスト取得件数:", len(rainfall_stations))

    save_json(
        MASTER_DIR / f"rainfall_stations_{obs_data_chg_time}.json",
        {
            "source_url": master_url,
            "retrieved_via": entry_url,
            "retrieved_at": retrieved_at,
            "system_latest_time": system_latest_time,
            "rain_sta_latest_time": rain_sta_latest_time,
            "obs_data_chg_time": obs_data_chg_time,
            "station_count": len(rainfall_stations),
            "stations": rainfall_stations,
        },
    )

    success = 0
    failed = []
    fallback_used = 0
    resolved_times = {}
    output_date = rain_sta_latest_time[:8]

    for station in rainfall_stations:
        station_id = station["staId"]
        station_name = station.get("staName", "")
        print(f"[{station_id}] {station_name}", end=" ... ", flush=True)

        try:
            result = get_rainfall_with_fallback(
                session,
                station_id,
                rain_sta_latest_time,
                obs_data_chg_time,
            )

            resolved_time = result["resolved_latest_time"]
            fallback_steps = result["fallback_steps"]
            resolved_times[str(station_id)] = resolved_time

            if fallback_steps:
                fallback_used += 1
                print(f"OK (fallback {fallback_steps * 10} min -> {resolved_time})")
            else:
                print("OK (latest)")

            save_json(
                OUTPUT_DIR / output_date / f"{station_id}.json",
                {
                    "entry_url": entry_url,
                    "source_url": result["source_url"],
                    "retrieved_at": retrieved_at,
                    "system_latest_time": system_latest_time,
                    "rain_sta_latest_time": rain_sta_latest_time,
                    "resolved_latest_time": resolved_time,
                    "fallback_steps": fallback_steps,
                    "obs_data_chg_time": obs_data_chg_time,
                    "station": station,
                    "data": result["data"],
                },
            )
            success += 1

        except Exception as error:
            print("FAILED:", error)
            failed.append(
                {
                    "staId": station_id,
                    "staName": station_name,
                    "error": str(error),
                }
            )

        time.sleep(REQUEST_INTERVAL_SEC)

    summary = {
        "retrieved_at": retrieved_at,
        "system_latest_time": system_latest_time,
        "rain_sta_latest_time": rain_sta_latest_time,
        "obs_data_chg_time": obs_data_chg_time,
        "fallback_window_minutes": FALLBACK_STEPS * 10,
        "requested": len(rainfall_stations),
        "success": success,
        "failed": len(failed),
        "fallback_used": fallback_used,
        "resolved_latest_times": resolved_times,
        "failures": failed,
    }
    save_json(OUTPUT_DIR / output_date / "_summary.json", summary)

    print()
    print("取得成功:", success)
    print("取得失敗:", len(failed))
    print("フォールバック使用:", fallback_used)

    # 本番運用では欠測局等があっても取得済みデータを保存できるよう、
    # 局単位の失敗だけでは workflow 全体を失敗させない。


if __name__ == "__main__":
    main()
