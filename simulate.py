"""Stand-in for the ESP32 node: posts simulated sensor readings to the server, as the firmware would.

Soil moisture follows the server's pump command (closed loop): it rises while the pump is on
and falls slowly otherwise. All values are simulated; no field data is involved.
"""
import math
import sys

import requests


def run(base, date='2026-11-11', session=None):
    s = session or requests.Session()
    s.trust_env = False
    soil = 62.0
    lines = []
    for k in range(21):
        hour = 6 + k * 0.5
        x = math.sin(math.pi * (hour - 6) / 12)
        r = dict(temperature=round(22 + 9 * x, 1), humidity=round(90 - 40 * x, 1),
                 leaf_wetness=round(5 + 80 * (1 - x) ** 2, 1), soil_moisture=round(soil, 1))
        hh, mm = int(hour), int(round((hour % 1) * 60))
        r['timestamp'] = f'{date}T{hh:02d}:{mm:02d}:00'
        a = s.post(base + '/api/sensor', json=r, timeout=5).json()
        lines.append(f"{hh:02d}:{mm:02d}  T={r['temperature']:4.1f}C  RH={r['humidity']:4.1f}%  "
                     f"soil={r['soil_moisture']:4.1f}%  leaf={r['leaf_wetness']:4.1f}%  ->  "
                     f"risk={a['risk']:<6}  pump={'ON ' if a['pump'] else 'OFF'}  "
                     f"sprayer={'ON ' if a['sprayer'] else 'OFF'}  {a['note']}")
        soil = soil + 15 if a['pump'] else soil - 2.6
    return lines


if __name__ == '__main__':
    print('\n'.join(run(sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:5000')))
