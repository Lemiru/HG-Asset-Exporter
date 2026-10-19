"""Rotation and matrix helpers shared by all exporters.

Haunting Ground stores rotations as XYZ Euler angles applied X first, then Y,
then Z (column-vector matrix ``Rz @ Ry @ Rx``). This was verified against the
bind pose of the Noesis reference exports of HEW_000/FIS_000 (all bones match
to 1e-7) and is shared by PCK bones, PCK/MTN/DP animation tracks.
"""
from __future__ import annotations

import math
from typing import Sequence

Quat = tuple[float, float, float, float]          # glTF order: x, y, z, w
Vec3 = tuple[float, float, float]
Mat4 = list[float]                                # column-major, 16 floats (glTF)


def euler_to_quat(rx: float, ry: float, rz: float) -> Quat:
    """Quaternion for ``Rz(rz) * Ry(ry) * Rx(rx)``."""
    cx, sx = math.cos(rx * 0.5), math.sin(rx * 0.5)
    cy, sy = math.cos(ry * 0.5), math.sin(ry * 0.5)
    cz, sz = math.cos(rz * 0.5), math.sin(rz * 0.5)
    return (sx * cy * cz - cx * sy * sz,
            cx * sy * cz + sx * cy * sz,
            cx * cy * sz - sx * sy * cz,
            cx * cy * cz + sx * sy * sz)


def quat_mul(a: Quat, b: Quat) -> Quat:
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz)


def quat_normalize(q: Quat) -> Quat:
    n = math.sqrt(sum(c * c for c in q))
    return (0.0, 0.0, 0.0, 1.0) if n == 0 else tuple(c / n for c in q)  # type: ignore[return-value]


def quat_rotate(q: Quat, v: Sequence[float]) -> Vec3:
    x, y, z, w = q
    # v' = v + 2w(q x v) + 2 q x (q x v)
    cx = y * v[2] - z * v[1]
    cy = z * v[0] - x * v[2]
    cz = x * v[1] - y * v[0]
    ccx = y * cz - z * cy
    ccy = z * cx - x * cz
    ccz = x * cy - y * cx
    return (v[0] + 2 * (w * cx + ccx), v[1] + 2 * (w * cy + ccy), v[2] + 2 * (w * cz + ccz))


def make_continuous(quats: list[Quat]) -> list[Quat]:
    """Flip signs so consecutive quaternions take the short path when lerped."""
    out: list[Quat] = []
    for q in quats:
        if out and sum(a * b for a, b in zip(out[-1], q)) < 0:
            q = (-q[0], -q[1], -q[2], -q[3])
        out.append(q)
    return out


def quat_to_mat3(q: Quat) -> list[list[float]]:
    x, y, z, w = q
    return [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]


def mat3_to_quat(m: Sequence[Sequence[float]]) -> Quat:
    """Row-major 3x3 rotation matrix to quaternion."""
    trace = m[0][0] + m[1][1] + m[2][2]
    if trace > 0:
        s = 0.5 / math.sqrt(trace + 1.0)
        q = ((m[2][1] - m[1][2]) * s, (m[0][2] - m[2][0]) * s, (m[1][0] - m[0][1]) * s, 0.25 / s)
    elif m[0][0] > m[1][1] and m[0][0] > m[2][2]:
        s = 2.0 * math.sqrt(1.0 + m[0][0] - m[1][1] - m[2][2])
        q = (0.25 * s, (m[0][1] + m[1][0]) / s, (m[0][2] + m[2][0]) / s, (m[2][1] - m[1][2]) / s)
    elif m[1][1] > m[2][2]:
        s = 2.0 * math.sqrt(1.0 + m[1][1] - m[0][0] - m[2][2])
        q = ((m[0][1] + m[1][0]) / s, 0.25 * s, (m[1][2] + m[2][1]) / s, (m[0][2] - m[2][0]) / s)
    else:
        s = 2.0 * math.sqrt(1.0 + m[2][2] - m[0][0] - m[1][1])
        q = ((m[0][2] + m[2][0]) / s, (m[1][2] + m[2][1]) / s, 0.25 * s, (m[1][0] - m[0][1]) / s)
    return quat_normalize(q)


def trs_matrix(t: Sequence[float], q: Quat, s: Sequence[float] = (1.0, 1.0, 1.0)) -> Mat4:
    """Column-major 4x4 from translation, rotation and scale."""
    r = quat_to_mat3(q)
    return [r[0][0] * s[0], r[1][0] * s[0], r[2][0] * s[0], 0.0,
            r[0][1] * s[1], r[1][1] * s[1], r[2][1] * s[1], 0.0,
            r[0][2] * s[2], r[1][2] * s[2], r[2][2] * s[2], 0.0,
            t[0], t[1], t[2], 1.0]


def mat4_mul(a: Mat4, b: Mat4) -> Mat4:
    """Column-major product a @ b."""
    out = [0.0] * 16
    for c in range(4):
        for r in range(4):
            out[c * 4 + r] = sum(a[k * 4 + r] * b[c * 4 + k] for k in range(4))
    return out


def mat4_rigid_inverse(m: Mat4) -> Mat4:
    """Inverse of a rotation+translation column-major matrix."""
    r = [[m[0], m[4], m[8]], [m[1], m[5], m[9]], [m[2], m[6], m[10]]]
    t = (m[12], m[13], m[14])
    rt = [[r[c][rr] for c in range(3)] for rr in range(3)]
    it = [-sum(rt[row][k] * t[k] for k in range(3)) for row in range(3)]
    return [rt[0][0], rt[1][0], rt[2][0], 0.0,
            rt[0][1], rt[1][1], rt[2][1], 0.0,
            rt[0][2], rt[1][2], rt[2][2], 0.0,
            it[0], it[1], it[2], 1.0]


def mat4_point(m: Mat4, p: Sequence[float]) -> Vec3:
    return (m[0] * p[0] + m[4] * p[1] + m[8] * p[2] + m[12],
            m[1] * p[0] + m[5] * p[1] + m[9] * p[2] + m[13],
            m[2] * p[0] + m[6] * p[1] + m[10] * p[2] + m[14])


def look_at_quat(position: Sequence[float], target: Sequence[float], roll: float = 0.0,
                 up: Sequence[float] = (0.0, 1.0, 0.0)) -> Quat:
    """Rotation of a glTF camera (looking down -Z, +Y up) aimed at ``target``
    with an additional roll (radians) around its viewing axis."""
    f = [target[i] - position[i] for i in range(3)]
    n = math.sqrt(sum(c * c for c in f))
    if n < 1e-12:
        return (0.0, 0.0, 0.0, 1.0)
    f = [c / n for c in f]
    if abs(sum(f[i] * up[i] for i in range(3))) > 0.999:
        up = (0.0, 0.0, 1.0)
    r = [f[1] * up[2] - f[2] * up[1], f[2] * up[0] - f[0] * up[2], f[0] * up[1] - f[1] * up[0]]
    rn = math.sqrt(sum(c * c for c in r))
    r = [c / rn for c in r]
    u = [r[1] * f[2] - r[2] * f[1], r[2] * f[0] - r[0] * f[2], r[0] * f[1] - r[1] * f[0]]
    c, s = math.cos(roll), math.sin(roll)
    rr = [r[i] * c + u[i] * s for i in range(3)]
    uu = [-r[i] * s + u[i] * c for i in range(3)]
    back = [-x for x in f]
    m = [[rr[i], uu[i], back[i]] for i in range(3)]
    return mat3_to_quat(m)
