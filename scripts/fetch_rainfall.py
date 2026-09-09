#!/usr/bin/env python3

import html
import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup


BASE_URL = "https://kasen-pref-ibaraki.jp"
ENTRY_STA_ID = 1
OUTPUT_DIR = Path("data/raw/rainfall")
MASTER_DIR = Path("data/master")
REQUEST_INTERVAL_SEC = 0.3
TEST_LIMIT = int(os.environ.get("TEST_LIMIT", "3"))
PROBE_MODE = os.environ.get("PROBE_MODE", "0") == "1"
PROBE_STEPS = int(os.environ.get("PROBE_STEPS", "18"))


def get_latest_times(session):
    """雨量詳細ページHTMLから最新時刻情報を取得する。"""
    url = f"{BASE_URL}/Sta/RainfallSta?obsStaId={ENTRY_STA_ID}"
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
    return latest, url


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


def get_rainfall_data(session, station_id, latest_time, obs_data_chg_time):
    """1観測局分の雨量JSONを取得する。"""
    url = rainfall_url(station_id, latest_time, obs_data_chg_time)
    response = session.get(url, timeout=30)
    response.raise_for_status()
    data = response.json()

    actual_station_id = data.get("tableData", {}).get("staId")
    if actual_station_id != station_id:
        raise RuntimeError(
            f"staId mismatch: expected={station_id}, actual={actual_station_id}"
        )

    return data, url


def probe_latest_available_time(session, station_id, start_time, obs_data_chg_time):
    """開始時刻から10分刻みで遡り、最初に存在する雨量JSONを探す。"""
    current = datetime.strptime(start_time, "%Y%m%d%H%M")

    print(f"=== probe staId={station_id} from {start_time} ===")
    for step in range(PROBE_STEPS + 1):
        candidate = (current - timedelta(minutes=10 * step)).strftime("%Y%m%d%H%M")
        url = rainfall_url(station_id, candidate, obs_data_chg_time)
        response = session.get(url, timeout=30)
        print(f"{candidate}: HTTP {response.status_code}")

        if response.status_code == 200:
            data = response.json()
            actual_station_id = data.get("tableData", {}).get("staId")
            if actual_station_id != station_id:
                raise RuntimeError(
                    f"staId mismatch during probe: expected={station_id}, actual={actual_station_id}"
                )
            print("FOUND:", url)
            print("obsTime:", data.get("tableData", {}).get("obsTime"))
            return candidate, data, url

        if response.status_code != 404:
            response.raise_for_status()

        time.sleep(REQUEST_INTERVAL_SEC)

    print("NOT FOUND within probe window")
    return None, None, None


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

    latest, entry_url = get_latest_times(session)
    system_latest_time = latest["systemLatestTime"]
    rain_sta_latest_time = latest.get("rainStaLatestTime", system_latest_time)
    obs_data_chg_time = latest["obsDataChgTime"]

    print("entryUrl:", entry_url)
    print("systemLatestTime:", system_latest_time)
    print("rainStaLatestTime:", rain_sta_latest_time)
    print("obsDataChgTime:", obs_data_chg_time)

    if PROBE_MODE:
        candidate, data, source_url = probe_latest_available_time(
            session,
            ENTRY_STA_ID,
            rain_sta_latest_time,
            obs_data_chg_time,
        )
        if candidate is None:
            raise SystemExit(1)

        save_json(
            OUTPUT_DIR / "probe" / f"{ENTRY_STA_ID}.json",
            {
                "entry_url": entry_url,
                "source_url": source_url,
                "system_latest_time": system_latest_time,
                "rain_sta_latest_time": rain_sta_latest_time,
                "resolved_latest_time": candidate,
                "obs_data_chg_time": obs_data_chg_time,
                "data": data,
            },
        )
        return

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
            "system_latest_time": system_latest_time,
            "rain_sta_latest_time": rain_sta_latest_time,
            "obs_data_chg_time": obs_data_chg_time,
            "station_count": len(rainfall_stations),
            "stations": rainfall_stations,
        },
    )

    success = 0
    failed = []
    output_date = rain_sta_latest_time[:8]

    for station in rainfall_stations:
        station_id = station["staId"]
        station_name = station.get("staName", "")
        print(f"[{station_id}] {station_name}", end=" ... ", flush=True)

        try:
            data, source_url = get_rainfall_data(
                session,
                station_id,
                rain_sta_latest_time,
                obs_data_chg_time,
            )
            save_json(
                OUTPUT_DIR / output_date / f"{station_id}.json",
                {
                    "source_url": source_url,
                    "system_latest_time": system_latest_time,
                    "rain_sta_latest_time": rain_sta_latest_time,
                    "obs_data_chg_time": obs_data_chg_time,
                    "station": station,
                    "data": data,
                },
            )
            print("OK")
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
        "system_latest_time": system_latest_time,
        "rain_sta_latest_time": rain_sta_latest_time,
        "obs_data_chg_time": obs_data_chg_time,
        "requested": len(rainfall_stations),
        "success": success,
        "failed": len(failed),
        "failures": failed,
    }
    save_json(OUTPUT_DIR / output_date / "_summary.json", summary)

    print()
    print("取得成功:", success)
    print("取得失敗:", len(failed))

    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
