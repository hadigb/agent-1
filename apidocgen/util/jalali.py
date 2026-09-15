"""Gregorian <-> Jalali (Solar Hijri) conversion (port of the jalaali algorithm)."""
from __future__ import annotations

import datetime as _dt
from typing import Tuple


def _div(a: int, b: int) -> int:
    """Integer division truncating toward zero (as in the reference JS implementation)."""
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b > 0) else -q


def _mod(a: int, b: int) -> int:
    return a - _div(a, b) * b


def _jal_cal(jy: int) -> Tuple[int, int, int]:
    breaks = [-61, 9, 38, 199, 426, 686, 756, 818, 1111, 1181, 1210, 1635, 2060, 2097, 2192, 2262, 2324, 2394, 2456, 3178]
    bl = len(breaks)
    gy = jy + 621
    leap_j = -14
    jp = breaks[0]
    if jy < jp or jy >= breaks[bl - 1]:
        raise ValueError(f"invalid Jalali year {jy}")
    jump = 0
    for i in range(1, bl):
        jm = breaks[i]
        jump = jm - jp
        if jy < jm:
            break
        leap_j = leap_j + _div(jump, 33) * 8 + _div(_mod(jump, 33), 4)
        jp = jm
    n = jy - jp
    leap_j = leap_j + _div(n, 33) * 8 + _div(_mod(n, 33) + 3, 4)
    if _mod(jump, 33) == 4 and jump - n == 4:
        leap_j += 1
    leap_g = _div(gy, 4) - _div((_div(gy, 100) + 1) * 3, 4) - 150
    march = 20 + leap_j - leap_g
    if jump - n < 6:
        n = n - jump + _div(jump + 4, 33) * 33
    leap = _mod(_mod(n + 1, 33) - 1, 4)
    if leap == -1:
        leap = 4
    return leap, gy, march


def _g2d(gy: int, gm: int, gd: int) -> int:
    d = _div((gy + _div(gm - 8, 6) + 100100) * 1461, 4) + _div(153 * _mod(gm + 9, 12) + 2, 5) + gd - 34840408
    d = d - _div(_div(gy + 100100 + _div(gm - 8, 6), 100) * 3, 4) + 752
    return d


def _j2d(jy: int, jm: int, jd: int) -> int:
    _leap, gy, march = _jal_cal(jy)
    return _g2d(gy, 3, march) + (jm - 1) * 31 - _div(jm, 7) * (jm - 7) + jd - 1


def _d2g(jdn: int) -> Tuple[int, int, int]:
    j = 4 * jdn + 139361631
    j = j + _div(_div(4 * jdn + 183187720, 146097) * 3, 4) * 4 - 3908
    i = _div(_mod(j, 1461), 4) * 5 + 308
    gd = _div(_mod(i, 153), 5) + 1
    gm = _mod(_div(i, 153), 12) + 1
    gy = _div(j, 1461) - 100100 + _div(8 - gm, 6)
    return gy, gm, gd


def to_jalali(gy: int, gm: int, gd: int) -> Tuple[int, int, int]:
    jdn = _g2d(gy, gm, gd)
    gy2 = _d2g(jdn)[0]
    jy = gy2 - 621
    leap, _gy, march = _jal_cal(jy)
    jdn1f = _g2d(gy2, 3, march)
    k = jdn - jdn1f
    if k >= 0:
        if k <= 185:
            return jy, 1 + _div(k, 31), _mod(k, 31) + 1
        k -= 186
    else:
        jy -= 1
        k += 179
        if leap == 1:  # last year had an extra day
            k += 1
    return jy, 7 + _div(k, 30), _mod(k, 30) + 1


def to_gregorian(jy: int, jm: int, jd: int) -> Tuple[int, int, int]:
    return _d2g(_j2d(jy, jm, jd))


def today_jalali() -> str:
    d = _dt.date.today()
    jy, jm, jd = to_jalali(d.year, d.month, d.day)
    return f"{jy:04d}/{jm:02d}/{jd:02d}"


def format_jalali(d: _dt.date) -> str:
    jy, jm, jd = to_jalali(d.year, d.month, d.day)
    return f"{jy:04d}/{jm:02d}/{jd:02d}"
