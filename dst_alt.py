import os
import re
import glob
import json
import math
import datetime
import argparse
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import requests
from skyfield.api import load, wgs84, EarthSatellite

# 可选：使用 skyfield 将 TLE 转为状态向量样本
from skyfield.api import load, EarthSatellite

# ========== 配置 ==========
DATA_DIR = os.path.join(os.path.dirname(__file__), "dst_data")
DST_GLOB = os.path.join(DATA_DIR, "dst_*.txt")
# 将 plots_combined 目录放在和代码同级
DST_PLOT_DIR = os.path.join(os.path.dirname(__file__), "plots_combined")
os.makedirs(DST_PLOT_DIR, exist_ok=True)

# Space-Track / TLE 缓存模板（在 main 中按传入的 NORAD 编号生成具体路径）
CACHE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CATNR = 25544
TLE_CACHE_TEMPLATE = os.path.join(CACHE_DIR, "spacetrack_tle_{catnr}.json")

# 物理常数
MU_EARTH = 398600.4418  # km^3 / s^2
R_EARTH_KM = 6371.0

# 登录部分
USERNAME = "##YOUR_USERNAME_HERE##"
PASSWORD = "##YOUR_PASSWORD_HERE##"
# ===========================

def load_dst_timeseries():
    times = []
    vals = []
    files = sorted(glob.glob(DST_GLOB))
    if not files:
        print("[WARN] 未找到 DST 文件:", DST_GLOB)

    for fp in files:
        fname = os.path.basename(fp)
        with open(fp, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or not line.upper().startswith("DST"):
                    continue
                # 解析头部 DST<YYMM>*<DD>，或退回使用文件名
                m = re.search(r"DST(\d{4})\*(\d{2})", line, re.IGNORECASE)
                if m:
                    ym = m.group(1)
                    yy = int(ym[:2]); mm = int(ym[2:])
                    year = 2000 + yy; month = mm
                    day = int(m.group(2))
                else:
                    mfn = re.search(r"dst[_\-]?(\d{2})[_\-]?(\d{2})", fname, re.IGNORECASE)
                    if mfn:
                        yy = int(mfn.group(1)); mm = int(mfn.group(2))
                        year = 2000 + yy; month = mm
                        mday = re.search(r"\*(\d{2})", line)
                        if not mday:
                            continue
                        day = int(mday.group(1))
                    else:
                        continue

                nums = list(map(int, re.findall(r"-?\d+", line)))
                if len(nums) >= 24:
                    hour_vals = nums[-24:]
                else:
                    if not nums:
                        continue
                    hour_vals = nums[:]
                    # Pad hour_vals to 24 elements efficiently
                    if len(hour_vals) < 24:
                        hour_vals.extend([hour_vals[-1]] * (24 - len(hour_vals)))

                for h, v in enumerate(hour_vals):
                    try:
                        dt = datetime.datetime(year, month, day, h, tzinfo=datetime.timezone.utc)
                    except ValueError:
                        continue
                    times.append(dt)
                    vals.append(v)
    # 排序
    paired = sorted(zip(times, vals), key=lambda x: x[0])
    if not paired:
        return [], []
    times_sorted = [t for t, _ in paired]
    vals_sorted = [v for _, v in paired]
    return times_sorted, vals_sorted


def load_tles_from_cache_or_raise(catnr):

    # 创建会话并登录 Space-Track
    cache_dir = os.path.dirname(os.path.abspath(__file__))
    cache_file = os.path.join(cache_dir, f"spacetrack_tle_{catnr}.json")

    tle_cache = TLE_CACHE_TEMPLATE.format(catnr=catnr)
    if os.path.exists(tle_cache):
        print(f"[INFO] 使用缓存文件: {cache_file}")
        with open(cache_file, "r", encoding="utf-8") as f:
            tle_records = json.load(f)
    
    else:
        session = requests.Session()
        login_url = "https://www.space-track.org/ajaxauth/login"
        payload = {'identity': USERNAME, 'password': PASSWORD}
        resp = session.post(login_url, data=payload)

        if resp.status_code == 200:
            print("[INFO] 登录成功")
        else:
            raise RuntimeError("登录失败，请检查用户名密码")

        # 请求历史TLE数据
        url = (
            f"https://www.space-track.org/basicspacedata/query/class/tle/"
            f"NORAD_CAT_ID/{catnr}/orderby/EPOCH%20desc/format/json"
        )
        response = session.get(url)
        if response.status_code != 200:
            raise RuntimeError(f"获取TLE失败: {response.status_code}")

        tle_records = response.json()
        print(f"[INFO] 获取到 {len(tle_records)} 条历史TLE")
        # 保存到本地缓存，避免下次重复访问
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(tle_records, f, ensure_ascii=False, indent=2)
        print(f"[INFO] 已保存 TLE 到缓存: {cache_file}")
    
    return tle_records


def compute_a_mean_from_tles(tle_records):
    ts = load.timescale()
    times_a = []
    a_means = []
    
    # Parse epochs once and create sortable list
    parsed_records = []
    for rec in tle_records:
        try:
            epoch_dt = datetime.datetime.strptime(rec["EPOCH"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=datetime.timezone.utc)
            parsed_records.append((epoch_dt, rec))
        except (ValueError, KeyError):
            continue
    
    # Sort by parsed epoch
    parsed_records.sort(key=lambda x: x[0])
    
    # 遍历按 EPOCH 排序的 TLE 记录，计算对应的平均半长轴高度（基于线速/平均运动）
    for epoch_dt, rec in parsed_records:
        line1 = rec["TLE_LINE1"]; line2 = rec["TLE_LINE2"]
        sat = EarthSatellite(line1, line2, "SAT", ts)

        # 尝试从第二行提取平均运动（rev/day），作为计算半长轴的回退方法
        mm_str = line2[52:63].strip()
        try:
            mean_motion_rev_per_day = float(mm_str)
        except Exception:
            mean_motion_rev_per_day = None

        # 如果没有可用的平均运动则跳过该记录
        if not mean_motion_rev_per_day or mean_motion_rev_per_day <= 0:
            continue

        n_rad_s = mean_motion_rev_per_day * 2.0 * math.pi / 86400.0  # 86400 s/day
        # 开普勒第三定律： n^2 = μ / a^3  => a = (μ / n^2)^(1/3)
        a_km = (MU_EARTH / (n_rad_s ** 2)) ** (1.0 / 3.0)  # 半长轴 (km)
        altitude_a_km = a_km - R_EARTH_KM  # 轨道高度 (km)

        times_a.append(epoch_dt)
        a_means.append(altitude_a_km)

    return times_a, a_means


def plot_combined(dst_times, dst_vals, a_times, a_vals, catnr, start_dt, end_dt):
    if not dst_times and not a_times:
        print("[WARN] 没有数据可绘制")
        return

    fig, ax1 = plt.subplots(figsize=(14, 6))
    # 左轴显示 DST
    if dst_times:
        ax1.plot(dst_times, dst_vals, '-', linewidth=0.8, markersize=2, label="DST (nT)", color='tab:blue')
    ax1.set_xlabel("UTC time")
    ax1.set_ylabel("DST (nT)", color='tab:blue')
    ax1.tick_params(axis='y', labelcolor='tab:blue')
    ax1.grid(True, which='both', linestyle=':', alpha=0.5)

    # 右轴显示半长轴
    if a_times:
        ax2 = ax1.twinx()
        ax2.plot(a_times, a_vals, 'o-', linewidth=0.9, markersize=4, color='tab:red', label="semi-major axis (km)")
        ax2.set_ylabel("Semi-major axis (km)", color='tab:red')
        ax2.tick_params(axis='y', labelcolor='tab:red')
    else:
        ax2 = None

    # 格式化 x 轴
    locator = mdates.AutoDateLocator()
    formatter = mdates.ConciseDateFormatter(locator)
    ax1.xaxis.set_major_locator(locator)
    ax1.xaxis.set_major_formatter(formatter)
    fig.autofmt_xdate()

    # 图例处理
    lines = []
    labels = []
    l1, = ax1.get_lines()
    lines.append(l1); labels.append(l1.get_label())
    if ax2 and ax2.get_lines():
        l2 = ax2.get_lines()[0]
        lines.append(l2); labels.append(l2.get_label())
    if lines:
        ax1.legend(lines, labels, loc="upper left")

    # 根据传入的 NORAD 编号和起止时间生成文件名：NORAD-YYYYMMDD_YYYYMMDD.png
    start_str = start_dt.strftime("%Y%m%d") if start_dt else "all"
    end_str = end_dt.strftime("%Y%m%d") if end_dt else "all"
    out_png = os.path.join(DST_PLOT_DIR, f"{catnr}-{start_str}_{end_str}.png")

    plt.tight_layout()
    plt.savefig(out_png, dpi=150)
    plt.close()
    print(f"[INFO] 已保存合并图像: {out_png}")


def parse_time_arg(s):
    if not s:
        return None
    # 尝试 ISO 8601 或常见格式，返回带时区信息的 UTC datetime
    for fmt in (None, "%Y-%m-%d", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            if fmt is None:
                # datetime.fromisoformat 支持带或不带 T 的形式，但不支持尾随 Z
                dt = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
            else:
                dt = datetime.datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            else:
                dt = dt.astimezone(datetime.timezone.utc)
            return dt
        except Exception:
            continue
    raise ValueError(f"无法解析时间参数: {s}. 支持格式示例: 2021-01-15 或 2021-01-15T12:00")


def filter_by_range(times, vals, start_dt, end_dt):
    if start_dt is None and end_dt is None:
        return times, vals
    out_t = []
    out_v = []
    # 根据指定的起止时间对时序进行过滤
    for t, v in zip(times, vals):
        if start_dt and t < start_dt:
            continue
        if end_dt and t > end_dt:
            continue
        out_t.append(t)
        out_v.append(v)
    return out_t, out_v


def main():
    parser = argparse.ArgumentParser(description="绘制 DST 与卫星平均半长轴的合并图（可指定起止时间和 NORAD 编号）")
    parser.add_argument("--start", "-s", help="绘图起始时间（UTC），例如 2021-01-05 或 2021-01-05T12:00")
    parser.add_argument("--end", "-e", help="绘图结束时间（UTC），例如 2021-01-20 或 2021-01-20T12:00")
    parser.add_argument("--catnr", type=int, default=DEFAULT_CATNR, help=f"NORAD 编号，默认 {DEFAULT_CATNR}")
    args = parser.parse_args()

    catnr = args.catnr

    try:
        start_dt = parse_time_arg(args.start)
    except Exception as ex:
        print("[ERROR] start 参数解析失败：", ex)
        return
    try:
        end_dt = parse_time_arg(args.end)
    except Exception as ex:
        print("[ERROR] end 参数解析失败：", ex)
        return

    if start_dt and end_dt and start_dt > end_dt:
        print("[ERROR] start 时间晚于 end 时间，请调整参数。")
        return

    dst_times, dst_vals = load_dst_timeseries()
    if not dst_times:
        print("[WARN] 未加载到 DST 数据")
    else:
        # 将 DST 时序过滤到指定时间范围
        dst_times, dst_vals = filter_by_range(dst_times, dst_vals, start_dt, end_dt)

    try:
        tle_records = load_tles_from_cache_or_raise(catnr)
    except Exception as e:
        print("[ERROR] 无法加载 TLE 缓存:", e)
        print("请确保缓存文件存在，或先运行获取 TLE 的脚本。")
        tle_records = []

    if tle_records:
        # 可在此先筛选出在范围内的 TLE 记录（根据 EPOCH 字段）
        if start_dt or end_dt:
            filtered_tles = []
            for rec in tle_records:
                try:
                    rec_epoch = datetime.datetime.strptime(rec["EPOCH"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=datetime.timezone.utc)
                except Exception:
                    continue
                if start_dt and rec_epoch < start_dt:
                    continue
                if end_dt and rec_epoch > end_dt:
                    continue
                filtered_tles.append(rec)
        else:
            filtered_tles = tle_records

        a_times, a_means = compute_a_mean_from_tles(filtered_tles)
        # 计算后再严格按时间范围过滤（以防万一）
        a_times, a_means = filter_by_range(a_times, a_means, start_dt, end_dt)
    else:
        a_times, a_means = [], []

    plot_combined(dst_times, dst_vals, a_times, a_means, catnr, start_dt, end_dt)


if __name__ == "__main__":
    main()