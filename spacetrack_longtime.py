import requests
from skyfield.api import load, wgs84, EarthSatellite
import matplotlib.pyplot as plt
import datetime
import math
import os
import json

# ======== 配置部分 ========
USERNAME = "################"
PASSWORD = "################"
CATNR = 25544  # NORAD编号
STEP_HOURS = 4  # 高度计算步长（小时）
HOURS_EACH_TLE = 12  # 每条TLE前后计算的时间范围（小时）
MU_EARTH = 398600.4418  # 地球标准引力参数 μ (km^3 / s^2)
# ==========================

# 创建会话并登录 Space-Track
cache_dir = os.path.dirname(os.path.abspath(__file__))
cache_file = os.path.join(cache_dir, f"spacetrack_tle_{CATNR}.json")

if os.path.exists(cache_file):
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
        f"NORAD_CAT_ID/{CATNR}/orderby/EPOCH%20desc/format/json"
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

# 按时间升序排序
sorted_tles = sorted(
    tle_records,
    key=lambda rec: datetime.datetime.strptime(rec["EPOCH"], "%Y-%m-%d %H:%M:%S")
)

ts = load.timescale()
times_all = []
altitudes_all = []  

altitudes_a = []  #储存平均半长轴的高度

# 遍历每条历史TLE
#for rec in sorted_tles:
# 访问最后30条数据
for rec in sorted_tles[-2000:]:
    line1 = rec["TLE_LINE1"]
    line2 = rec["TLE_LINE2"]
    complete_tle = line1 + "\n" + line2
    # 创建卫星对象
    sat = EarthSatellite(line1, line2,'STARLINK', ts)
    #sat = load.tle_file(complete_tle)
    # 解析EPOCH时间
    epoch_dt = datetime.datetime.strptime(rec["EPOCH"], "%Y-%m-%d %H:%M:%S")

    # 读取 TLE 中的 Mean Motion（单位：revs per day）
    # TLE line2 中 mean motion 通常位于字符索引 52:63（0-based slice 52:63）
    mm_str = line2[52:63].strip()
    try:
        mean_motion_rev_per_day = float(mm_str)
    except ValueError:
        print(f"[WARN] 无法解析 Mean Motion: '{mm_str}' for EPOCH {rec.get('EPOCH')}")
        # 继续处理下一个 TLE
        continue

    n_rad_s = mean_motion_rev_per_day * 2.0 * math.pi / 86400.0  # 86400 s/day
    # 开普勒第三定律： n^2 = μ / a^3  => a = (μ / n^2)^(1/3)
    a_km = (MU_EARTH / (n_rad_s ** 2)) ** (1.0 / 3.0)  # 半长轴 (km)
    R_EARTH_KM = 6371.0  # 地球平均半径 (km)
    altitude_a_km = a_km - R_EARTH_KM  # 轨道高度 (km)

    # # 以该EPOCH为中心，计算轨道高度
    # for offset_h in range(-HOURS_EACH_TLE // 2, HOURS_EACH_TLE // 2 + 1, STEP_HOURS):
    #     t = ts.utc(
    #         epoch_dt.year, epoch_dt.month, epoch_dt.day,
    #         epoch_dt.hour + offset_h, epoch_dt.minute, epoch_dt.second
    #     )
    #     geo = sat.at(t)
    #     subpoint = wgs84.subpoint(geo)
    #     alt_km = subpoint.elevation.km
    #     times_all.append(t.utc_datetime())
    #     altitudes_all.append(alt_km)
    times_all.append(epoch_dt)
    altitudes_a.append(altitude_a_km)


# 绘制长时间曲线
plt.figure(figsize=(20, 6))
plt.plot(times_all, altitudes_a, marker='.', linestyle='-',linewidth=0.8, alpha=0.9)
plt.xlabel("UTC time")
plt.ylabel("altitude (km)")
plt.title(f"NORAD {CATNR} longtime altitude change (source: Space-Track)")
plt.grid(True)
plt.tight_layout()
plt.show()
