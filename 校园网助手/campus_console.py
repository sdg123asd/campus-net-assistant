# -*- coding: utf-8 -*-
"""
校园网助手 · 路由器控制台
=====================================================================
给「路由器自动登录校园网」那套脚本配的图形界面：状态一眼可见、
回宿舍点一下开启、出宿舍点一下关闭，还能设置自动开关时段。

适配场景：学校限制「一个账号同时只能一台设备在线」时，路由器一直在线会
把你在教室的登录顶掉 —— 所以本程序的核心就是那个总开关。

功能
  · 状态总览   开关状态 / 定时任务 / 登录脚本 / crond / 最近日志
  · 一键开关   开启（回宿舍）· 关闭（去教室）· 立即登录一次
  · 自动时段   例如 08:00 自动关闭、22:00 自动开启 —— 不用再惦记手动切
  · 实时日志   增量拉取路由器 /tmp/campus_login.log，按级别着色
  · 一键部署   上传程序、迁移定时任务、加密保存账号密码、校验（密码走 stdin）
  · 常驻托盘   开机自启、缩到托盘、托盘菜单可直接开关
  · 凭据安全   路由器/校园网密码用 Windows DPAPI 加密后存盘（换机不可解）

依赖：paramiko（SSH）。缺了程序会提示，可点「一键安装」自动装。
      本机若装了 PuTTY（plink/pscp）也能当备用后端。
运行：python campus_console.py      自检：python campus_console.py --selftest
"""

import os
import re
import sys
import io
import json
import glob
import math
import time
import queue
import struct
import base64
import ctypes
import tempfile
import threading
import subprocess
import tkinter as tk
from ctypes import wintypes
from tkinter import ttk, filedialog, messagebox

# ---------------------------------------------------------------- 常量

APP_NAME = '校园网助手'
APP_DESC = '路由器控制台 · 自动登录校园网'
# Windows 任务栏身份。Python 脚本进程默认没有自己的 AppUserModelID，
# 任务栏就按 pythonw.exe 这个可执行文件取图标 —— 于是无论窗口图标怎么换，
# 任务栏上永远是 Python 的图标。必须在**创建任何窗口之前**声明一个自己的。
# 名字用「厂商.产品.版本」形式，不能有空格。
APP_AUMID = 'CampusNetAssistant.Console.v1'
REMOTE_ROOT = '/data/campus/v2'
REMOTE_SWITCH = REMOTE_ROOT + '/bin/campus_switch.sh'
REMOTE_LOGIN = REMOTE_ROOT + '/bin/campus_login.sh'
REMOTE_CRED = REMOTE_ROOT + '/bin/cred_admin.sh'
REMOTE_LOG = '/tmp/campus_login.log'
CONFIG_FILE = os.path.join(os.path.expanduser('~'), '.campusnet_console.json')
APP_LOG_FILE = os.path.join(os.path.expanduser('~'), '.campusnet_console.log')
AUTOSTART_KEY = 'CampusNetConsole'
AUTOSTART_FLAG = '--autostart'
DPAPI_ENTROPY = b'campusnet-console-v1'

CRON_LINE = ("*/2 7-23 * * * [ -f %s/enabled ] && CAMPUS_ROOT=%s %s >/dev/null 2>&1"
             % (REMOTE_ROOT, REMOTE_ROOT, REMOTE_LOGIN))
SCHED_MARK = '# CAMPUS-CONSOLE-SCHEDULE'
CRONTABS = ('/etc/crontabs/root', '/data/etc/crontabs/root')

# ================================================================ 设计令牌
# 这一层是界面唯一的"真相来源"。改配色/间距只改这里，不要在组件里写死颜色。
# 对比度已按 WCAG AA 校验：正文类文字 ≥4.5:1（括号内为实测值）。

# ---- 中性色 / 表面 ----
COL_CANVAS = '#eef2f7'      # 页面底色（比卡片暗，让白卡"浮"起来）
COL_BG = COL_CANVAS         # 旧名兼容
COL_CARD = '#ffffff'        # 卡片表面
COL_SOFT = '#f7f9fc'        # 次级表面（凹陷区）
COL_SUNKEN = '#f7f9fc'
COL_LOG_BG = '#fbfcfe'      # 日志区（比卡片再亮一点点的冷白）
COL_HOVER = '#f1f4f9'       # 悬停
COL_PRESS = '#e6ebf3'       # 按下

# ---- 描边 ----
COL_BORDER = '#e3e8ef'      # 卡片外框（发丝线）
COL_BORDER_STRONG = '#cfd8e3'
COL_RAIL = '#dde3ec'

# ---- 文字（括号内为白底对比度）----
COL_TEXT = '#0f1b2d'        # 主文字 (16.8:1)
COL_MUTED = '#4a5a70'       # 次要文字 (7.0:1)
COL_FAINT = '#5a6a80'       # 辅助文字 (5.5:1 白底 / 4.9:1 页面底)
                            # 上一版 #64748b 在白底刚好 4.76，但放到 #eef2f7 页面底
                            # 就只有 4.23 —— 自检把这个坑抓出来了
COL_DISABLED = '#a3aec0'    # 仅用于禁用控件（WCAG 对此豁免）

# ---- 品牌色 ----
COL_ACCENT = '#2563eb'
COL_ACCENT_D = '#1d4ed8'
COL_ACCENT_PRESS = '#1e40af'
COL_ACCENT_L = '#eaf1ff'
COL_ACCENT_BORDER = '#c7dbff'

# ---- 语义色：kind -> (文字色, 底色, 描边色) ----
SEM = {
    'ok':   ('#157f3d', '#e6f7ec', '#bfe6cd'),   # 5.1:1
    'off':  ('#a25c06', '#fdf3e3', '#f2ddb4'),   # 4.7:1
    'err':  ('#b42318', '#fdeceb', '#f5c9c5'),   # 5.8:1
    'info': ('#1d4ed8', '#eaf1ff', '#c7dbff'),   # 6.1:1
    'idle': ('#526175', '#f1f4f9', '#dde3ec'),   # 5.7:1
}
COL_GREEN, COL_GREEN_BG = SEM['ok'][0], SEM['ok'][1]
COL_AMBER, COL_AMBER_BG = SEM['off'][0], SEM['off'][1]
COL_RED, COL_RED_BG = SEM['err'][0], SEM['err'][1]

# ---- 字体族 ----
F_UI = 'Microsoft YaHei UI'
F_MONO = 'Consolas'

# ---- 字号（pt；Tk 已按 DPI 设置 scaling，pt 会自动跟随缩放）----
T_MICRO = 8      # 极小辅助说明
T_CAPTION = 9    # 说明文字 / 标签
T_BODY = 10      # 正文 / 控件
T_SUB = 11       # 卡片标题
T_H3 = 12        # 小节标题
T_H2 = 15        # 区域标题
T_H1 = 19        # 状态主标题
T_MONO_SIZE = 9  # 日志

# ---- 间距标尺（逻辑像素，4 的倍数；用 u() 换算成物理像素）----
SP1, SP2, SP3, SP4, SP5, SP6, SP7 = 4, 8, 12, 16, 20, 24, 32

# ---- 圆角 ----
R_SM, R_MD, R_LG = 6, 8, 10

UI_SCALE = 1.0


def u(n):
    """逻辑像素 → 物理像素。高 DPI 下让间距跟着文字一起放大，
    否则 200% 缩放时字很大、padding 还是 16px，界面会显得又挤又廉价。"""
    return int(round(n * UI_SCALE))


def _srgb_lin(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(hexcolor):
    h = hexcolor.lstrip('#')
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _srgb_lin(r) + 0.7152 * _srgb_lin(g) + 0.0722 * _srgb_lin(b)


def contrast_ratio(fg, bg):
    """WCAG 2.1 对比度。正文要 ≥4.5:1，大字号 ≥3:1。
    把配色写进自检里，以后改色就不会悄悄把可读性改坏。"""
    a, b = luminance(fg), luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


FG_BY_KIND = {'ok': COL_GREEN, 'off': COL_AMBER, 'err': COL_RED,
              'info': COL_ACCENT_D, 'idle': COL_MUTED}

STATUS_ICON = {'ok': 'check_circle', 'off': 'pause_circle',
               'err': 'alert_circle', 'info': 'info_circle',
               'idle': 'dot_circle'}


# ================================================================ 图标系统
# 全部图标都用 Pillow 现画：24 单位网格 → 4 倍超采样 → LANCZOS 缩回目标尺寸。
# 好处：不带任何图片资源、可任意着色以匹配语义色、任意 DPI 都清晰。
# 没有 Pillow 时优雅降级（icon_label 返回 0 尺寸占位，界面照常可用）。

_ICON_CACHE = {}
_PIL_STATE = {'checked': False, 'ok': False}
# 运行期登记用过的图标名，并记录没画过的名字：
# 这样自检/冒烟测试就能抓到 "icon='refrsh'" 这类拼写错误，而不是默默画个圆圈了事。
_ICON_USED = set()
_ICON_UNKNOWN = set()

ICON_NAMES = (
    'wifi', 'power', 'bolt', 'refresh', 'clock', 'file', 'pulse', 'plug',
    'gear', 'folder', 'download', 'upload', 'pause', 'play', 'trash', 'shield',
    'key', 'check', 'x', 'alert', 'info', 'check_circle', 'alert_circle',
    'pause_circle', 'info_circle', 'dot_circle', 'dot', 'door',
    'checkbox_on', 'checkbox_off', 'radio_on', 'radio_off',
)


# ---------------------------------------------------------------- 打包适配
# 用 PyInstaller 打成 exe 后，运行环境与源码运行有三处本质差别，统一在这里收口，
# 免得 sys.frozen 的判断散落各处：
#   1. 代码与随包资源在临时解包目录（sys._MEIPASS），只读、程序退出即删；
#   2. __file__ 不再指向项目目录，靠它推同级文件（campus.ico / payload）会失效；
#   3. --windowed 的 exe 没有控制台，sys.stdout 是 None —— 此时任何 print()
#      都抛 AttributeError，自检就没法在打包版上验证了。
# 另：「通用版」清空 PRESET 账号密码是构建脚本干的事，与这里的路径适配无关。

def _frozen():
    """是否运行在 PyInstaller 打出来的 exe 里。"""
    return bool(getattr(sys, 'frozen', False))


def res_dir():
    """只读资源目录：随包打进来的东西（payload 等）从这里找。"""
    if _frozen():
        return getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def data_dir():
    """可写、能持久保存的目录。

    打包后**绝不能**写 _MEIPASS：那是临时解包目录，退出即删，
    于是每次启动都要重画一遍 .ico，而且任务栏读到的路径每次都不一样。
    源码运行时仍用本文件所在目录，保持「campus.ico 挨着 .py」的老习惯。
    """
    if not _frozen():
        return os.path.dirname(os.path.abspath(__file__))
    base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
    d = os.path.join(base, 'CampusNetAssistant')
    try:
        os.makedirs(d, exist_ok=True)
        return d
    except OSError:
        return os.path.expanduser('~')


if sys.stdout is None or sys.stderr is None:
    # --windowed 的 exe 没有控制台。把输出落到文件，否则
    # `校园网助手.exe --selftest` 会因为 print 到 None 直接崩掉，
    # 打包版就永远验不了。普通文件对象即可，不必自定义类。
    _OUT_FALLBACK = os.path.join(data_dir(), 'console_output.txt')
    try:
        _fallback_out = open(_OUT_FALLBACK, 'w', encoding='utf-8', errors='replace')
    except OSError:
        _fallback_out = open(os.devnull, 'w', encoding='utf-8', errors='replace')
    if sys.stdout is None:
        sys.stdout = _fallback_out
    if sys.stderr is None:
        sys.stderr = _fallback_out


def _pil():
    """惰性探测 Pillow。返回 (Image, ImageDraw, ImageTk) 或 None。"""
    if not _PIL_STATE['checked']:
        _PIL_STATE['checked'] = True
        try:
            from PIL import Image, ImageDraw, ImageTk  # noqa: F401
            _PIL_STATE['ok'] = True
        except Exception:
            _PIL_STATE['ok'] = False
    if not _PIL_STATE['ok']:
        return None
    from PIL import Image, ImageDraw, ImageTk
    return Image, ImageDraw, ImageTk


# 24 单位网格上的图标定义。统一 2.2 的描边、圆头圆角，保证一族图标观感一致。
def _icon_image(name, px, color):
    m = _pil()
    if m is None:
        return None
    Image, ImageDraw, _ = m
    SS = 4                                   # 超采样倍数
    side = px * SS
    img = Image.new('RGBA', (side, side), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    k = side / 24.0

    def P(x, y):
        return (x * k, y * k)

    def W(units):
        return max(1, int(round(units * k)))

    def line(pts, w=2.2, cap=True):
        pp = [P(*q) for q in pts]
        d.line(pp, fill=color, width=W(w), joint='curve')
        if cap:                              # 圆头：在两端补小圆
            r = W(w) / 2.0
            for cx, cy in (pp[0], pp[-1]):
                d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)

    def ring(cx, cy, r, w=2.2):
        d.ellipse([P(cx - r, cy - r), P(cx + r, cy + r)],
                  outline=color, width=W(w))

    def disc(cx, cy, r):
        d.ellipse([P(cx - r, cy - r), P(cx + r, cy + r)], fill=color)

    def arc(cx, cy, r, a0, a1, w=2.2):
        d.arc([P(cx - r, cy - r), P(cx + r, cy + r)], a0, a1,
              fill=color, width=W(w))

    def rrect(x0, y0, x1, y1, r=2.5, w=2.2, solid=False):
        box = [P(x0, y0), P(x1, y1)]
        if solid:
            d.rounded_rectangle(box, radius=r * k, fill=color)
        else:
            d.rounded_rectangle(box, radius=r * k, outline=color, width=W(w))

    def poly(pts):
        d.polygon([P(*q) for q in pts], fill=color)

    def arrow(cx, cy, r, ang, size=2.4, half=1.7):
        """在圆周 ang 角度处画一个沿切线方向的箭头（用于刷新/循环图标）。"""
        a = math.radians(ang)
        tx, ty = cx + r * math.cos(a), cy + r * math.sin(a)
        dx, dy = -math.sin(a), math.cos(a)
        nx, ny = math.cos(a), math.sin(a)
        poly([(tx + dx * size, ty + dy * size),
              (tx + nx * half, ty + ny * half),
              (tx - nx * half, ty - ny * half)])

    # ---- 业务图标 ----
    if name == 'wifi':
        arc(12, 17.6, 5.0, 208, 332, 2.3)
        arc(12, 17.6, 9.6, 208, 332, 2.3)
        disc(12, 17.9, 1.9)
    elif name == 'power':
        arc(12, 12.6, 7.6, 300, 360, 2.3)
        arc(12, 12.6, 7.6, 0, 240, 2.3)
        line([(12, 3.2), (12, 11.4)], 2.3)
    elif name == 'bolt':
        poly([(13.6, 2.4), (6.0, 13.4), (11.4, 13.4), (10.4, 21.6),
              (18.0, 10.6), (12.6, 10.6)])
    elif name == 'refresh':
        arc(12, 12, 7.6, 250, 360, 2.3)
        arc(12, 12, 7.6, 0, 205, 2.3)
        arrow(12, 12, 7.6, 205, 2.4, 1.7)
    elif name == 'clock':
        ring(12, 12, 8.4, 2.2)
        line([(12, 12), (12, 6.6)], 2.2)
        line([(12, 12), (16.0, 14.0)], 2.2)
    elif name == 'file':
        rrect(5.2, 3.4, 18.8, 20.6, 2.6, 2.2)
        line([(9.2, 10.0), (14.8, 10.0)], 2.0)
        line([(9.2, 14.4), (14.8, 14.4)], 2.0)
    elif name == 'pulse':
        line([(3.0, 12.6), (7.2, 12.6), (9.4, 6.6), (12.2, 18.0),
              (14.6, 12.6), (21.0, 12.6)], 2.3)
    elif name == 'plug':
        line([(9.6, 2.8), (9.6, 7.4)], 2.3)
        line([(14.4, 2.8), (14.4, 7.4)], 2.3)
        rrect(7.0, 7.2, 17.0, 14.4, 2.6, 2.3)
        line([(12.0, 14.4), (12.0, 21.2)], 2.3)
    elif name == 'gear':
        # 用"滑杆"而不是齿轮：16px 下齿轮必然糊成太阳，滑杆在极小尺寸依然可辨
        for gy, kx in ((6.8, 9.4), (12.0, 15.2), (17.2, 7.8)):
            line([(3.6, gy), (kx - 3.2, gy)], 2.2)
            line([(kx + 3.2, gy), (20.4, gy)], 2.2)
            ring(kx, gy, 2.8, 2.2)
    elif name == 'folder':
        line([(3.4, 19.6), (3.4, 6.6), (9.2, 6.6), (11.4, 9.4),
              (20.6, 9.4), (20.6, 19.6), (3.4, 19.6)], 2.2)
    elif name == 'download':
        line([(12, 3.2), (12, 14.6)], 2.3)
        line([(7.6, 10.4), (12, 14.8), (16.4, 10.4)], 2.3)
        line([(4.4, 17.4), (4.4, 20.4), (19.6, 20.4), (19.6, 17.4)], 2.3)
    elif name == 'upload':
        line([(12, 20.6), (12, 9.2)], 2.3)
        line([(7.6, 13.4), (12, 9.0), (16.4, 13.4)], 2.3)
        line([(4.4, 6.4), (4.4, 3.4), (19.6, 3.4), (19.6, 6.4)], 2.3)
    elif name == 'pause':
        rrect(8.4, 5.0, 10.9, 19.0, 1.2, solid=True)
        rrect(13.1, 5.0, 15.6, 19.0, 1.2, solid=True)
    elif name == 'play':
        poly([(8.0, 4.8), (19.0, 12.0), (8.0, 19.2)])
    elif name == 'trash':
        line([(3.8, 6.6), (20.2, 6.6)], 2.2)
        line([(9.2, 6.4), (9.2, 3.6), (14.8, 3.6), (14.8, 6.4)], 2.2)
        line([(6.0, 6.8), (7.2, 20.6), (16.8, 20.6), (18.0, 6.8)], 2.2)
    elif name == 'shield':
        line([(12, 2.8), (20.2, 6.2), (20.2, 12.4), (12, 21.2),
              (3.8, 12.4), (3.8, 6.2), (12, 2.8)], 2.2)
        line([(8.4, 11.8), (11.0, 14.4), (15.8, 9.0)], 2.2)
    elif name == 'key':
        ring(8.6, 8.6, 4.2, 2.2)
        line([(11.6, 11.6), (20.2, 20.2)], 2.2)
        line([(16.6, 16.6), (14.2, 19.0)], 2.2)
        line([(19.0, 19.0), (17.0, 21.0)], 2.2)
    # ---- 状态 / 记号 ----
    elif name == 'check':
        line([(5.0, 12.6), (9.8, 17.4), (19.0, 6.6)], 2.6)
    elif name == 'x':
        line([(6.2, 6.2), (17.8, 17.8)], 2.4)
        line([(17.8, 6.2), (6.2, 17.8)], 2.4)
    elif name == 'alert':
        line([(12, 3.4), (12, 14.0)], 2.4)
        disc(12, 18.2, 1.5)
    elif name == 'info':
        ring(12, 12, 8.6, 2.2)
        disc(12, 8.0, 1.4)
        line([(12, 11.4), (12, 16.8)], 2.2)
    elif name == 'check_circle':
        ring(12, 12, 8.6, 2.2)
        line([(7.8, 12.2), (10.8, 15.2), (16.4, 9.0)], 2.2)
    elif name == 'alert_circle':
        ring(12, 12, 8.6, 2.2)
        line([(12, 7.0), (12, 13.2)], 2.2)
        disc(12, 16.4, 1.3)
    elif name == 'pause_circle':
        ring(12, 12, 8.6, 2.2)
        line([(10.0, 8.0), (10.0, 16.0)], 2.2)
        line([(14.0, 8.0), (14.0, 16.0)], 2.2)
    elif name == 'info_circle':
        ring(12, 12, 8.6, 2.2)
        disc(12, 8.0, 1.3)
        line([(12, 11.2), (12, 16.6)], 2.2)
    elif name == 'dot_circle':
        ring(12, 12, 8.6, 2.2)
        disc(12, 12, 2.4)
    elif name == 'dot':
        disc(12, 12, 4.6)
    elif name == 'checkbox_on':
        rrect(1.8, 1.8, 22.2, 22.2, 5.2, solid=True)
        # 用全透明笔"挖"出对勾：PIL 的 ImageDraw 是直接覆写像素而非混合，
        # 所以单色图标也能做出双色（填充+镂空）效果，且缩放后抗锯齿正常
        d.line([P(6.6, 12.4), P(10.4, 16.2), P(17.6, 7.6)],
               fill=(0, 0, 0, 0), width=W(2.8), joint='curve')
    elif name == 'checkbox_off':
        rrect(1.8, 1.8, 22.2, 22.2, 5.2, 2.2)
    elif name == 'radio_on':
        disc(12, 12, 10.4)
        r = 4.4
        d.ellipse([P(12 - r, 12 - r), P(12 + r, 12 + r)], fill=(0, 0, 0, 0))
    elif name == 'radio_off':
        ring(12, 12, 9.3, 2.2)
    elif name == 'door':
        line([(14.4, 3.4), (5.0, 3.4), (5.0, 20.6), (14.4, 20.6)], 2.2)
        line([(10.0, 12.0), (21.0, 12.0)], 2.3)
        line([(17.4, 8.4), (21.0, 12.0), (17.4, 15.6)], 2.3)
    else:
        _ICON_UNKNOWN.add(name)
        ring(12, 12, 8.6, 2.2)
    _ICON_USED.add(name)
    return img.resize((px, px), Image.LANCZOS)


_BADGE_CACHE = {}


def badge_image(glyph, px, color, glyph_frac=0.54, radius_frac=0.30,
                glyph_color=None):
    """实心圆角徽章。glyph_color=None 时把图形"挖空"（露出底下的卡片色），
    否则在徽章上叠加绘制图形——两种模式都只需要一个颜色参数起步。"""
    key = (glyph, px, color, round(glyph_frac, 3), round(radius_frac, 3),
           glyph_color)
    if key in _BADGE_CACHE:
        return _BADGE_CACHE[key]
    m = _pil()
    if m is None:
        return None
    Image, ImageDraw, _ = m
    from PIL import ImageChops
    SS = 4
    side = px * SS
    img = Image.new('RGBA', (side, side), (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle(
        [0, 0, side - 1, side - 1], radius=int(side * radius_frac),
        fill=color)
    gs = max(6, int(round(px * glyph_frac))) * SS
    off = (side - gs) // 2
    white = (255, 255, 255, 255)
    if glyph_color is None:
        g = _icon_image(glyph, gs, white)
        if g is None:
            return None
        hole = Image.new('L', (side, side), 0)
        hole.paste(g.split()[3], (off, off))
        img.putalpha(ImageChops.subtract(img.split()[3], hole))
    else:
        g = _icon_image(glyph, gs, glyph_color)
        if g is None:
            return None
        img.paste(g, (off, off), g)
    out = img.resize((px, px), Image.LANCZOS)
    _BADGE_CACHE[key] = out
    return out


def badge_photo(glyph, px, color, glyph_color=None, glyph_frac=0.54):
    key = ('ph', glyph, px, color, glyph_color, round(glyph_frac, 3))
    if key in _ICON_CACHE:
        return _ICON_CACHE[key]
    img = badge_image(glyph, px, color, glyph_frac=glyph_frac,
                      glyph_color=glyph_color)
    if img is None:
        return None
    try:
        ph = _pil()[2].PhotoImage(img)
    except Exception:
        return None
    _ICON_CACHE[key] = ph
    return ph


def badge_label(parent, glyph, px, color, bg=COL_CARD, glyph_color=None):
    ph = badge_photo(glyph, px, color, glyph_color)
    if ph is None:
        return tk.Frame(parent, bg=bg, width=0, height=0, bd=0,
                        highlightthickness=0)
    lbl = tk.Label(parent, image=ph, bg=bg, bd=0, highlightthickness=0)
    lbl._icon_ref = ph
    return lbl


def icon_photo(name, px, color=COL_MUTED):
    """取（并缓存）图标的 Tk PhotoImage。无 Pillow 时返回 None。"""
    key = (name, px, color)
    if key in _ICON_CACHE:
        return _ICON_CACHE[key]
    img = _icon_image(name, px, color)
    if img is None:
        return None
    try:
        ph = _pil()[2].PhotoImage(img)
    except Exception:
        return None
    _ICON_CACHE[key] = ph                   # 必须持引用，否则会被 GC 掉变空白
    return ph


def icon_label(parent, name, px, color=COL_MUTED, bg=COL_CARD, **kw):
    """一个只放图标的 Label。没有 Pillow 时退化成 0 尺寸 Frame，调用方无需分支。"""
    ph = icon_photo(name, px, color)
    if ph is None:
        return tk.Frame(parent, bg=bg, width=0, height=0, bd=0,
                        highlightthickness=0)
    lbl = tk.Label(parent, image=ph, bg=bg, bd=0, highlightthickness=0, **kw)
    lbl._icon_ref = ph
    return lbl


# ---------------------------------------------------------------- 应用图标

def brand_image(size=64):
    """画应用标识：蓝色渐变圆角方块 + WiFi 弧线（小尺寸）或 + 绿色对勾徽章（大尺寸）。
    小尺寸减少细节，是图标设计的基本功——16px 塞徽章只会糊成一团。"""
    m = _pil()
    if m is None:
        return None
    Image, ImageDraw, _ = m
    SS = 8 if size <= 48 else 4
    side = size * SS
    badge = size >= 32

    def q(v):
        return v * side

    top, bot = (59, 130, 246), (29, 78, 216)
    grad = Image.new('RGBA', (side, side), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for y in range(side):
        t = y / max(1, side - 1)
        gd.line([(0, y), (side, y)],
                fill=tuple(int(top[i] + (bot[i] - top[i]) * t)
                           for i in range(3)) + (255,))
    mask = Image.new('L', (side, side), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, side - 1, side - 1], radius=int(side * 0.225), fill=255)
    img = Image.new('RGBA', (side, side), (0, 0, 0, 0))
    img.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(img)

    white = (255, 255, 255, 255)
    if badge:
        cx, cy, r1, r2, dr = 0.420, 0.440, 0.160, 0.295, 0.047
    else:
        cx, cy, r1, r2, dr = 0.500, 0.585, 0.215, 0.395, 0.064
    sw = max(1, int(round(size * 0.075 * SS)))
    for r in (r1, r2):
        d.arc([q(cx - r), q(cy - r), q(cx + r), q(cy + r)],
              208, 332, fill=white, width=sw)
    rr = q(dr)
    d.ellipse([q(cx) - rr, q(cy) - rr, q(cx) + rr, q(cy) + rr], fill=white)

    if badge:
        bx, by, br = 0.735, 0.735, 0.205
        pad = max(1, int(round(size * 0.045 * SS)))
        d.ellipse([q(bx - br) - pad, q(by - br) - pad,
                   q(bx + br) + pad, q(by + br) + pad], fill=white)
        cr = q(br)
        d.ellipse([q(bx) - cr, q(by) - cr, q(bx) + cr, q(by) + cr],
                  fill=(21, 127, 61, 255))
        w2 = max(1, int(round(size * 0.055 * SS)))
        pts = [(bx - br * 0.44, by + br * 0.02),
               (bx - br * 0.10, by + br * 0.36),
               (bx + br * 0.48, by - br * 0.34)]
        d.line([(q(a), q(b)) for a, b in pts], fill=white, width=w2,
               joint='curve')
    return img.resize((size, size), Image.LANCZOS)


def make_ico(path, sizes=(16, 20, 24, 32, 40, 48, 64, 128, 256)):
    """逐尺寸单独渲染后手写 ICO 容器。
    不能让 Pillow 从 256 缩放生成所有尺寸——小尺寸会糊；
    每个尺寸都用为该尺寸优化的画法，16px 才够锐利。"""
    m = _pil()
    if m is None:
        return None
    images = []
    for s in sizes:
        im = brand_image(s)
        if im is None:
            return None
        images.append(im)
    try:
        blobs = []
        for im in images:
            buf = io.BytesIO()
            im.save(buf, format='PNG')
            blobs.append(buf.getvalue())
        n = len(images)
        entries = b''
        offset = 6 + 16 * n
        for im, blob in zip(images, blobs):
            s = im.width
            dim = 0 if s >= 256 else s          # ICO 里 256 记作 0
            entries += struct.pack('<BBBBHHII', dim, dim, 0, 0, 1, 32,
                                   len(blob), offset)
            offset += len(blob)
        with open(path, 'wb') as f:
            f.write(struct.pack('<HHH', 0, 1, n) + entries + b''.join(blobs))
        return path
    except Exception as e:
        app_log('生成 .ico 失败: %s' % e)
        return None


APP_ICON_FILE = os.path.join(data_dir(), 'campus.ico')
APP_ICON_VER = APP_ICON_FILE + '.ver'
# 改动品牌图标（brand_image）的画法时把这个数字 +1，程序会自动重画 .ico。
# 只判断「文件存不存在」是不够的：老用户机器上那个 campus.ico 会一直留着旧图案，
# 于是「图标明明改了却看不到变化」—— 任务栏读的正是这个文件。
ICON_VERSION = 1


def ensure_app_icon():
    """保证磁盘上的 .ico 和当前代码画出来的一致，返回可用路径。"""
    try:
        ver = str(ICON_VERSION)
        if os.path.exists(APP_ICON_FILE):
            try:
                with open(APP_ICON_VER, encoding='utf-8') as f:
                    if f.read().strip() == ver:
                        return APP_ICON_FILE
            except OSError:
                pass
        if make_ico(APP_ICON_FILE):
            try:
                with open(APP_ICON_VER, 'w', encoding='utf-8') as f:
                    f.write(ver)
            except OSError:
                pass
            app_log('已重新生成 campus.ico（版本 %s）' % ver)
            return APP_ICON_FILE
    except Exception as e:
        app_log('刷新 .ico 失败: %s' % e)
    return APP_ICON_FILE if os.path.exists(APP_ICON_FILE) else None

# 小米 / 红米路由器的出厂默认管理地址（与域名 miwifi.com 双向映射）。
# 这里以前写的是 192.168.99.1 —— 那并不是任何小米机型的默认值，按默认值去连必然失败。
# 现在统一引用这个常量，兜底值、错误提示、演示数据都从它取，不会再各写各的。
DEFAULT_ROUTER_HOST = '192.168.31.1'

DEFAULT_CONFIG = {
    'host': DEFAULT_ROUTER_HOST,
    'port': 22,
    'user': 'root',
    'router_password_enc': '',
    'campus_user': '',
    'campus_password_enc': '',
    'payload_dir': '',
    'host_fingerprint': '',
    'backend': 'auto',
    'sched_enabled': False,
    'sched_off': '08:00',
    'sched_on': '22:00',
    'autostart': False,
    'minimize_to_tray': True,
    'log_pause': False,
    # 预置信息迁移标记，不是用户选项：见下面的 PRESET / apply_preset()。
    'preset_version': 0,
}


# ---------------------------------------------------------------- 预置信息
# 本机固定好的连接信息：程序一打开就把这些值填进设置面板，不用每次手输。
#
# 取值来源：
#   * SSH 侧 —— 实测填过的路由器连接参数。小米/红米路由器开启 SSH 后，dropbear
#     里只有 root 一个账号（xmir-patcher 开锁工具全流程也用 root）；
#     Xiaomi_xxxx 那种格式是路由器**后台网管页**的登录账号，不是 SSH 账号，
#     填进去会认证失败。
#   * 校园网侧 —— Downloads\CampusNet-AutoLogin-main\...\school_network.py 里的
#     USERNAME / PASSWORD 两个常量（学号 + 校园网密码）。
#
# 凭据说明：本仓库是公开版本，下面三个凭据字段一律留空（真实密码不进版本库）。
#   程序首次运行会把它们灌进配置一次；只要在设置面板里填一次并点「保存」，
#   密码就以 Windows DPAPI 密文写进 ~/.campusnet_console.json，明文不落任何文件。
#   想在本机恢复「打开就预填好」：把你的值填回下面三项，并把 PRESET_VERSION 加 1
#   （版本号不变的话，旧配置还在，新值不会生效）。
PRESET = {
    # 本机 WLAN 的默认网关就是这个地址（本机 192.168.31.137 / DNS 也是它）→
    # 它才是这台小米路由器。之前填过 172.17.113.25，不在本网段，本机根本到不了。
    'host': '192.168.31.1',
    'port': 22,
    'user': 'root',
    'router_password': '',
    'campus_user': '',             # 校园网账号（学号）—— 公开版本留空
    'campus_password': '',
}

# 为什么需要版本号：本机已经有一份 ~/.campusnet_console.json，load_config 里
# 文件的值会盖住默认值 —— 光改上面的常量根本改不动界面（改完打开还是旧值）。
# 所以用 preset_version 做一次性迁移：只有版本号变大时才灌预置。
PRESET_VERSION = 2   # v2：把预置地址从 172.17.113.25 纠正为网关 192.168.31.1


class OpsError(Exception):
    """面向用户的可读错误"""


# ---------------------------------------------------------------- 小工具

def app_log(msg):
    try:
        with open(APP_LOG_FILE, 'a', encoding='utf-8') as f:
            f.write('[%s] %s\n' % (time.strftime('%Y-%m-%d %H:%M:%S'), msg))
    except Exception:
        pass


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    exists = os.path.exists(CONFIG_FILE)
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict):
            cfg.update(data)
    except Exception:
        pass
    # 预置信息（见 PRESET）在这里灌。文件已存在时必须写回磁盘：不然下次保存
    # 会把预置值又覆盖掉；文件不存在时只改内存，第一次点「保存」自然落盘。
    if apply_preset(cfg) and exists:
        save_config(cfg)
        app_log('已应用预置连接信息（preset v%s）' % PRESET_VERSION)
    return cfg


def save_config(cfg):
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        app_log('配置保存失败: %s' % e)
        return False


def parse_hhmm(text):
    m = re.match(r'^\s*(\d{1,2})\s*[:：]\s*(\d{1,2})\s*$', str(text or ''))
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if 0 <= h <= 23 and 0 <= mi <= 59:
        return h * 60 + mi
    return None


def fmt_hhmm(total):
    total = max(0, min(1439, int(total)))
    return '%02d:%02d' % (total // 60, total % 60)


def hhmm_to_cron(text):
    t = parse_hhmm(text)
    if t is None:
        return None
    return ('%d' % (t % 60), '%d' % (t // 60))


def remote_version_hint(text):
    return 'campus_switch.sh' not in (text or '')


# ---------------------------------------------------------------- DPAPI

class _BLOB(ctypes.Structure):
    _fields_ = [('cbData', wintypes.DWORD),
                ('pbData', ctypes.POINTER(ctypes.c_char))]


def _blob(data):
    if not data:
        data = b'\x00'
    buf = ctypes.create_string_buffer(data, len(data))
    return _BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def dpapi_encrypt(text):
    """用当前 Windows 用户凭据加密（换用户/换机器都解不开），失败返回 ''"""
    if not text:
        return ''
    try:
        crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
        blob_in, _b1 = _blob(text.encode('utf-8'))
        blob_ent, _b2 = _blob(DPAPI_ENTROPY)
        blob_out = _BLOB()
        if not crypt32.CryptProtectData(ctypes.byref(blob_in), None,
                                        ctypes.byref(blob_ent), None, None, 0,
                                        ctypes.byref(blob_out)):
            return ''
        try:
            raw = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            kernel32.LocalFree(blob_out.pbData)
        return base64.b64encode(raw).decode('ascii')
    except Exception as e:
        app_log('DPAPI 加密失败: %s' % e)
        return ''


def dpapi_decrypt(token):
    if not token:
        return ''
    try:
        crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
        blob_in, _b1 = _blob(base64.b64decode(token))
        blob_ent, _b2 = _blob(DPAPI_ENTROPY)
        blob_out = _BLOB()
        if not crypt32.CryptUnprotectData(ctypes.byref(blob_in), None,
                                         ctypes.byref(blob_ent), None, None, 0,
                                         ctypes.byref(blob_out)):
            return ''
        try:
            data = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            kernel32.LocalFree(blob_out.pbData)
        return data.decode('utf-8', 'replace')
    except Exception:
        return ''


def apply_preset(cfg):
    """把预置连接信息灌进配置，返回是否真的改动了。

    放在这里而不是 load_config 里，是因为本机可能有一份老配置：load_config 会用
    文件里的值盖住默认值，只改常量等于没改。用 preset_version 做一次性迁移。

    两个刻意的边界：
      * 只在版本号落后时执行 → 用户之后在界面上改的值不会被每次启动冲掉。
      * 密码只在库里还没有的时候灌 → 否则「清空已保存的密码」会被下次启动撤销。
    """
    try:
        if int(cfg.get('preset_version', 0) or 0) >= PRESET_VERSION:
            return False
    except (TypeError, ValueError):
        pass

    for key in ('host', 'port', 'user'):
        val = PRESET.get(key)
        if val not in (None, '') and cfg.get(key) != val:
            cfg[key] = val
    if PRESET.get('campus_user') and not cfg.get('campus_user'):
        cfg['campus_user'] = PRESET['campus_user']

    for src, field in (('router_password', 'router_password_enc'),
                       ('campus_password', 'campus_password_enc')):
        if PRESET.get(src) and not cfg.get(field):
            enc = dpapi_encrypt(PRESET[src])
            if enc:
                cfg[field] = enc
            else:
                app_log('预置 %s 加密失败，密码框会留空' % src)

    cfg['preset_version'] = PRESET_VERSION
    return True


# ---------------------------------------------------------------- 单实例 / DPI

_mutex = None


def acquire_single_instance():
    global _mutex
    if sys.platform != 'win32':
        return True
    try:
        _mutex = ctypes.windll.kernel32.CreateMutexW(None, False,
                                                     'Local\\CampusNetConsole')
        return ctypes.windll.kernel32.GetLastError() != 183
    except Exception:
        return True


def enable_dpi_awareness():
    if sys.platform != 'win32':
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def init_app_identity():
    """声明本进程的任务栏身份（AppUserModelID）。

    不做这件事，Windows 会把任务栏按钮认成 pythonw.exe，图标就是 Python 的 ——
    窗口图标设得再对也没用。必须在**创建任何窗口之前**调用。
    返回生效的 AUMID，失败返回 None。
    """
    if sys.platform != 'win32' or _S32 is None:
        return None
    try:
        hr = _S32.SetCurrentProcessExplicitAppUserModelID(APP_AUMID)
        if hr:
            app_log('设置 AppUserModelID 失败: 0x%08x' % (hr & 0xffffffff))
            return None
        return APP_AUMID
    except Exception as e:
        app_log('设置 AppUserModelID 异常: %s' % e)
        return None


def current_app_identity():
    """读回当前身份的 AppUserModelID；没设置过返回 None。

    GitHub 上大量「Tkinter 任务栏图标改不了」的问题都是漏了这一步，
    所以自检里要能直接断言它，别指望肉眼从任务栏看出来。
    """
    if sys.platform != 'win32' or _S32 is None:
        return None
    try:
        p = ctypes.c_wchar_p()
        if _S32.GetCurrentProcessExplicitAppUserModelID(ctypes.byref(p)):
            return None
        return p.value
    except Exception:
        return None


def set_taskbar_icon(win, ico_path):
    """把任务栏 / Alt+Tab 用的大图标显式设成 .ico 里尺寸合适的那一帧。

    `wm iconbitmap` 设的是窗口图标，但任务栏取的大图标未必拿到该有的尺寸；
    高 DPI 下（本机 200%）任务栏要的是 64px，给 16px 会被拉糊。
    这里按系统要求的尺寸从 .ico 里挑一帧，Windows 会自己选最接近的。
    """
    if sys.platform != 'win32' or _W32 is None or not os.path.exists(ico_path):
        return False
    try:
        hwnd = _toplevel_hwnd(win)
        if not hwnd:
            return False
        IMAGE_ICON, LR_LOADFROMFILE = 1, 0x0010
        WM_SETICON = 0x0080
        got = False
        # ICON_BIG(1) 给任务栏与 Alt+Tab；ICON_SMALL(0) 给标题栏与任务栏小图
        for flag, idx in ((1, (11, 12)), (0, (49, 50))):    # SM_CXICON / SM_CXSMICON
            cx = max(_W32.GetSystemMetrics(idx[0]), 16)
            cy = max(_W32.GetSystemMetrics(idx[1]), 16)
            h = _W32.LoadImageW(None, ico_path, IMAGE_ICON, cx, cy,
                                LR_LOADFROMFILE)
            if not h:
                continue
            _W32.SendMessageW(ctypes.c_void_p(hwnd), WM_SETICON,
                              ctypes.c_void_p(flag), ctypes.c_void_p(h))
            _ICON_HANDLES.append(h)   # 引用留住：释放掉窗口就画不出来了
            got = True
        return got
    except Exception as e:
        app_log('设置任务栏大图标失败: %s' % e)
        return False


def system_dpi():
    try:
        dpi = int(_W32.GetDpiForSystem())
        if dpi > 0:
            return dpi
    except Exception:
        pass
    # 退回读设备上下文。HDC 是 64 位句柄，必须用声明过签名的 _W32，
    # 否则它会被当成 32 位 int 截断，GetDeviceCaps 直接失败。
    try:
        hdc = _W32.GetDC(None)
        if not hdc:
            return 96
        try:
            dpi = int(_G32.GetDeviceCaps(hdc, 88))      # LOGPIXELSY
            return dpi if dpi > 0 else 96
        finally:
            _W32.ReleaseDC(None, hdc)
    except Exception:
        return 96


class W32RECT(ctypes.Structure):
    _fields_ = [('left', ctypes.c_long), ('top', ctypes.c_long),
                ('right', ctypes.c_long), ('bottom', ctypes.c_long)]


_W32 = None
_G32 = None
_S32 = None
_ICON_HANDLES = []          # 交给窗口的 HICON 引用必须留着，否则图标画不出来
if sys.platform == 'win32':
    # ctypes 默认把参数和返回值都当 32 位 int —— HWND / HDC 都是 64 位句柄，
    # 被截断后 GetWindowRect / GetDeviceCaps 直接失败，而且**静默**失败：
    # 表现为「窗口位置设了但没生效，底边照旧沉在任务栏下面」。
    # 这一组签名必须显式声明，否则 Windows 相关的坐标操作全都不可信。
    _W32 = ctypes.windll.user32
    _W32.GetAncestor.restype = ctypes.c_void_p
    _W32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    _W32.GetDesktopWindow.restype = ctypes.c_void_p
    _W32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(W32RECT)]
    _W32.GetWindowRect.restype = ctypes.c_bool
    _W32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                  ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    _W32.SetWindowPos.restype = ctypes.c_bool
    _W32.SystemParametersInfoW.argtypes = [ctypes.c_uint, ctypes.c_uint,
                                           ctypes.c_void_p, ctypes.c_uint]
    _W32.SystemParametersInfoW.restype = ctypes.c_bool
    _W32.GetDpiForSystem.restype = ctypes.c_uint
    _W32.GetDC.restype = ctypes.c_void_p
    _W32.GetDC.argtypes = [ctypes.c_void_p]
    _W32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    # GetDeviceCaps 在 gdi32 里，不在 user32 —— 写错会直接 AttributeError
    _G32 = ctypes.windll.gdi32
    _G32.GetDeviceCaps.argtypes = [ctypes.c_void_p, ctypes.c_int]
    _G32.GetDeviceCaps.restype = ctypes.c_int
    # 截图用（PrintWindow 路线）
    _W32.GetWindowDC.restype = ctypes.c_void_p
    _W32.GetWindowDC.argtypes = [ctypes.c_void_p]
    _W32.PrintWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
    _W32.PrintWindow.restype = ctypes.c_bool
    _G32.CreateCompatibleDC.restype = ctypes.c_void_p
    _G32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
    _G32.CreateCompatibleBitmap.restype = ctypes.c_void_p
    _G32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                            ctypes.c_int]
    _G32.SelectObject.restype = ctypes.c_void_p
    _G32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    _G32.DeleteObject.argtypes = [ctypes.c_void_p]
    _G32.DeleteDC.argtypes = [ctypes.c_void_p]
    _G32.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
                               ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p,
                               ctypes.c_uint]
    _G32.GetDIBits.restype = ctypes.c_int
    # 任务栏身份（shell32）+ 图标
    _W32.GetSystemMetrics.argtypes = [ctypes.c_int]
    _W32.GetSystemMetrics.restype = ctypes.c_int
    _W32.LoadImageW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p,
                               ctypes.c_uint, ctypes.c_int, ctypes.c_int,
                               ctypes.c_uint]
    _W32.LoadImageW.restype = ctypes.c_void_p
    _W32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                  ctypes.c_void_p, ctypes.c_void_p]
    _W32.SendMessageW.restype = ctypes.c_void_p
    _S32 = ctypes.windll.shell32
    _S32.SetCurrentProcessExplicitAppUserModelID.argtypes = [ctypes.c_wchar_p]
    _S32.SetCurrentProcessExplicitAppUserModelID.restype = ctypes.c_long
    _S32.GetCurrentProcessExplicitAppUserModelID.argtypes = [
        ctypes.POINTER(ctypes.c_wchar_p)]
    _S32.GetCurrentProcessExplicitAppUserModelID.restype = ctypes.c_long


def work_area(root=None):
    """桌面可用区（物理像素，已扣掉任务栏），返回 (left, top, right, bottom)。

    只拿 winfo_screenheight 来限高是不够的 —— 那是**整块**屏幕，任务栏还占着
    底部一截（本机 200% 缩放下 1800 里有 96px）。窗口尺寸按整屏裁完再去定位，
    底边就沉到任务栏下面，最底下那条状态栏（设置 / 刷新按钮）正好被盖住。
    """
    try:
        r = W32RECT()
        # 48 = SPI_GETWORKAREA 的 DPI 感知版本（进程已声明 DPI 感知时才准）
        if _W32.SystemParametersInfoW(48, 0, ctypes.byref(r), 0):
            if r.right > r.left and r.bottom > r.top:
                return (r.left, r.top, r.right, r.bottom)
    except Exception:
        pass
    try:                                    # 退回整屏，至少不会算出负数
        return (0, 0, root.winfo_screenwidth(), root.winfo_screenheight())
    except Exception:
        return (0, 0, 1920, 1080)


def _toplevel_hwnd(win):
    """拿到 Tk 窗口对应的**真正顶层**窗口句柄（含标题栏那条）。

    winfo_id() 对 Toplevel 给的是 Tk 自己的窗口；用 GetAncestor(GA_ROOT) 往上找一层
    才稳，而且必须确认结果不是桌面窗口 —— 往桌面上 SetWindowPos 会动到整个 shell。
    """
    try:
        hwnd = int(win.winfo_id())
        top = _W32.GetAncestor(ctypes.c_void_p(hwnd), 2)   # GA_ROOT
        top = top or hwnd
        if top == _W32.GetDesktopWindow():
            return hwnd
        return top
    except Exception:
        return None


def window_rect(win):
    """窗口外框的真实矩形 (x, y, w, h)。

    `winfo_geometry()` 报的是**请求值**，不是屏幕上的实际位置 —— 排查
    「窗口沉到任务栏下面」时就是被它骗了一轮。要判断窗口有没有出屏，
    只能读这个。
    """
    try:
        hwnd = _toplevel_hwnd(win)
        if not hwnd:
            return None
        r = W32RECT()
        if not _W32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(r)):
            return None
        return (r.left, r.top, r.right - r.left, r.bottom - r.top)
    except Exception:
        return None


def window_icon_set(win):
    """窗口是否真的挂上了图标（WM_GETICON 能拿到句柄才算）。

    任务栏身份、多尺寸 .ico、高 DPI 这几件事的效果都只体现在真机界面上，
    所以自检要能直接问 Windows「这个窗口现在用的图标是哪个」。
    """
    if sys.platform != 'win32' or _W32 is None:
        return False
    try:
        hwnd = _toplevel_hwnd(win)
        if not hwnd:
            return False
        WM_GETICON = 0x007F
        for flag in (1, 0, 2):      # ICON_BIG / ICON_SMALL / ICON_SMALL2
            if _W32.SendMessageW(ctypes.c_void_p(hwnd), WM_GETICON,
                                 ctypes.c_void_p(flag), None):
                return True
        return False
    except Exception:
        return False


def grab_window_image(win):
    """把窗口自己画到内存 DC 里，返回 PIL Image（含标题栏），失败返回 None。

    为什么不用 ImageGrab：它是「整屏截图再按矩形裁剪」，别的窗口压在
    本窗口上面就会一起被截进来 —— 本机上 Windows 快捷设置面板常驻开着，
    右侧一条总被吃掉，走查截图根本没法看。PrintWindow 让窗口自己渲染，
    与遮挡无关。
    """
    try:
        from PIL import Image
    except Exception:
        return None
    try:
        hwnd = _toplevel_hwnd(win)
        r = W32RECT()
        if not hwnd or not _W32.GetWindowRect(ctypes.c_void_p(hwnd),
                                              ctypes.byref(r)):
            return None
        w, h = r.right - r.left, r.bottom - r.top
        if w <= 0 or h <= 0:
            return None

        hdc = _W32.GetWindowDC(ctypes.c_void_p(hwnd))
        if not hdc:
            return None
        mem = _G32.CreateCompatibleDC(ctypes.c_void_p(hdc))
        bmp = _G32.CreateCompatibleBitmap(ctypes.c_void_p(hdc), w, h)
        old = _G32.SelectObject(ctypes.c_void_p(mem), ctypes.c_void_p(bmp))
        try:
            # 2 = PW_RENDERFULLCONTENT，DWM 合成的窗口（含 Tk）靠它才画得全
            if not _W32.PrintWindow(ctypes.c_void_p(hwnd),
                                    ctypes.c_void_p(mem), 2):
                return None

            class _BMIH(ctypes.Structure):
                _fields_ = [('biSize', ctypes.c_uint32),
                            ('biWidth', ctypes.c_int32),
                            ('biHeight', ctypes.c_int32),
                            ('biPlanes', ctypes.c_uint16),
                            ('biBitCount', ctypes.c_uint16),
                            ('biCompression', ctypes.c_uint32),
                            ('biSizeImage', ctypes.c_uint32),
                            ('biXPelsPerMeter', ctypes.c_int32),
                            ('biYPelsPerMeter', ctypes.c_int32),
                            ('biClrUsed', ctypes.c_uint32),
                            ('biClrImportant', ctypes.c_uint32)]

            class _BMI(ctypes.Structure):
                _fields_ = [('bmiHeader', _BMIH), ('bmiColors', ctypes.c_uint32 * 3)]

            bi = _BMI()
            bi.bmiHeader.biSize = ctypes.sizeof(_BMIH)
            bi.bmiHeader.biWidth = w
            bi.bmiHeader.biHeight = -h        # 负高度 = 自上而下，省一次翻转
            bi.bmiHeader.biPlanes = 1
            bi.bmiHeader.biBitCount = 32
            bi.bmiHeader.biCompression = 0    # BI_RGB
            buf = ctypes.create_string_buffer(w * h * 4)
            if not _G32.GetDIBits(ctypes.c_void_p(mem), ctypes.c_void_p(bmp),
                                  0, h, buf, ctypes.byref(bi), 0):
                return None
            im = Image.frombuffer('RGBA', (w, h), buf, 'raw', 'BGRA', 0, 1)
            im = im.convert('RGB')
            # PrintWindow 对个别窗口会给出全黑画面：这种当失败处理，
            # 让调用方退回 ImageGrab，别把黑图当结果。
            if im.getextrema() == ((0, 0), (0, 0), (0, 0)):
                return None
            return im
        finally:
            _G32.SelectObject(ctypes.c_void_p(mem), ctypes.c_void_p(old))
            _G32.DeleteObject(ctypes.c_void_p(bmp))
            _G32.DeleteDC(ctypes.c_void_p(mem))
            _W32.ReleaseDC(ctypes.c_void_p(hwnd), ctypes.c_void_p(hdc))
    except Exception:
        return None


def place_window(win, w, h, over=None):
    """按可用区摆稳窗口：尺寸合适、整框可见、居中。

    `winfo_geometry()` 报的是**请求值**不是实际落点，而 `wm geometry` 的
    `+x+y` 在本机（200% 缩放）实测是准的 —— 前提是 Win32 那一步没被
    ctypes 的 32 位默认签名坑掉（见 _W32 的注释）。所以这里：

    1. 先把客户区收到「外框 + 四周留白一定装得进可用区」；
    2. 用四段式 geometry 一次给全尺寸和位置；
    3. **读回 GetWindowRect 核对**，对不上再用 SetWindowPos 兜底。

    不核对的话，一旦定位失效，窗口底边就沉到任务栏下面，
    最下面那条状态栏（设置 / 刷新）点不到 —— 这个 bug 正是这么发现的。

    over 给定时居中于该窗口（设置对话框盖在主界面上）；否则居中于可用区。
    """
    try:
        win.geometry('%dx%d' % (int(w), int(h)))
        win.update_idletasks()
    except Exception:
        pass
    if sys.platform != 'win32' or _W32 is None:
        return None
    try:
        hwnd = _toplevel_hwnd(win)
        if not hwnd:
            return None

        def _rect():
            """外框真实矩形 (x, y, w, h)，含标题栏与边框。"""
            r = W32RECT()
            if not _W32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(r)):
                return None
            return (r.left, r.top, r.right - r.left, r.bottom - r.top)

        o = _rect()
        if not o:
            return None
        # 外框比客户区多出标题栏 + 边框：先量出来，才能把客户区收到
        # 「外框 + 四周留白一定装得进可用区」。
        fw = o[2] - win.winfo_width()
        fh = o[3] - win.winfo_height()
        wa = work_area()
        m = u(12)
        max_w = (wa[2] - wa[0]) - fw - 2 * m
        max_h = (wa[3] - wa[1]) - fh - 2 * m
        cw = min(win.winfo_width(), max(320, max_w))
        ch = min(win.winfo_height(), max(240, max_h))

        if over is not None:
            try:
                ax, ay = over.winfo_rootx(), over.winfo_rooty()
                aw, ah = over.winfo_width(), over.winfo_height()
            except Exception:
                ax = ay = 0
                aw = ah = 0
        else:
            ax = ay = aw = ah = 0

        ow, oh = cw + fw, ch + fh
        if aw > 0 and ah > 0:                   # 居中于参照窗口
            x, y = ax + (aw - ow) // 2, ay + (ah - oh) // 2
        else:                                   # 没有参照物就居中于可用区
            x = wa[0] + ((wa[2] - wa[0]) - ow) // 2
            y = wa[1] + ((wa[3] - wa[1]) - oh) // 2
        # 夹进可用区：参照窗口自己贴边时，不能把这个窗口一起带出去
        x = min(max(x, wa[0] + m), max(wa[0] + m, wa[2] - ow - m))
        y = min(max(y, wa[1] + m), max(wa[1] + m, wa[3] - oh - m))

        dbg = bool(os.environ.get('CAMPUS_DEBUG_WIN'))

        # 尺寸 + 位置一次给全，然后**读回来核**：对不上就用 SetWindowPos 兜底。
        # 位置单独给或单独不给都可能落到别处 —— 底边一旦被任务栏盖住，
        # 最下面那条状态栏（设置 / 刷新）就点不到了。
        win.geometry('%dx%d+%d+%d' % (cw, ch, x, y))
        win.update()
        got = _rect()
        if dbg:
            print('[place] 目标 %dx%d+%d+%d | geometry 后 %r | Tk 记 %s'
                  % (cw, ch, x, y, got, win.geometry()))
        if got and (got[0], got[1]) != (int(x), int(y)):
            SWP_NOZORDER, SWP_NOACTIVATE, SWP_NOSIZE = 0x0004, 0x0010, 0x0001
            _W32.SetWindowPos(ctypes.c_void_p(hwnd), None, int(x), int(y), 0, 0,
                              SWP_NOZORDER | SWP_NOACTIVATE | SWP_NOSIZE)
            win.update()
            got = _rect() or got
            if dbg:
                print('[place] SetWindowPos 兜底后 %r' % (got,))
        return got
    except Exception:
        return None


# ---------------------------------------------------------------- SSH 后端

class Backend:
    name = '?'
    # 上传通道发生降级时放一句人话说明，由上层（deploy）转述给用户。
    # 有些精简固件（如小米路由器上 XMiR-SSH 带的 dropbear）**没有编译 SFTP 子系统**，
    # 远端也没有 scp，这时只能退到 base64 通道。
    xfer_note = ''

    def run(self, cmd, timeout=25):
        raise NotImplementedError

    def run_stdin(self, cmd, data, timeout=40):
        """把 data 从 stdin 交给远端命令（密码这样走，不进命令行）"""
        raise NotImplementedError

    def run_script(self, script, timeout=90):
        """把一段 sh 脚本从 stdin 交给远端 /bin/sh -s 执行"""
        return self.run_stdin('/bin/sh -s', script, timeout)

    def upload(self, local, remote, progress=None):
        raise NotImplementedError

    def close(self):
        pass


class ParamikoBackend(Backend):
    name = 'paramiko'

    def __init__(self, host, port, user, password, fingerprint=''):
        try:
            import paramiko
        except ImportError:
            raise OpsError('未安装 paramiko，无法建立 SSH 连接。\n'
                           '点主界面上的「一键安装」即可自动装好。')
        self.paramiko = paramiko
        self.host, self.port, self.user = host, port, user
        self.fingerprint = fingerprint
        self.new_fingerprint = ''
        self.sftp_ok = None          # None=还没试过；False=没有 SFTP，走 base64
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            self.client.connect(host, port=port, username=user, password=password,
                                timeout=8, banner_timeout=12, auth_timeout=12,
                                allow_agent=False, look_for_keys=False)
        except paramiko.AuthenticationException:
            raise OpsError('路由器密码不对（或用户名不是 root）。\n'
                           '这里要填的是路由器后台登录密码，不是校园网密码。')
        except paramiko.SSHException as e:
            raise OpsError('SSH 协商失败：%s' % e)
        except Exception as e:
            raise OpsError('连不上路由器 %s:%s\n%s\n\n'
                           '最常见的原因：这台电脑现在没连该路由器的 WiFi。'
                           % (host, port, e))
        try:
            key = self.client.get_transport().get_remote_server_key()
            fp = '%s %s' % (key.get_name(),
                            base64.b64encode(key.get_fingerprint()).decode('ascii'))
            if self.fingerprint and fp != self.fingerprint:
                self.client.close()
                raise OpsError('路由器身份指纹变了（路由器重装过系统时属正常）。\n'
                               '为安全起见已中断连接：到「设置」点一次「测试连接」'
                               '确认后即可继续。')
            self.new_fingerprint = fp
        except OpsError:
            raise
        except Exception:
            pass

    def _chan(self, cmd, data=None, timeout=25):
        chan = self.client.get_transport().open_session()
        chan.settimeout(timeout)
        try:
            chan.exec_command(cmd)
            if data is not None:
                chan.sendall(data.replace('\r\n', '\n').encode('utf-8')
                             if isinstance(data, str) else data)
                chan.shutdown_write()
            out = b''
            while True:
                moved = False
                while chan.recv_ready():
                    out += chan.recv(65536)
                    moved = True
                while chan.recv_stderr_ready():
                    out += chan.recv_stderr(65536)
                    moved = True
                if chan.exit_status_ready() and not moved:
                    break
                if not moved:
                    time.sleep(0.02)
            rc = chan.recv_exit_status()
            while chan.recv_ready():
                out += chan.recv(65536)
            return rc, out.decode('utf-8', 'replace')
        finally:
            chan.close()

    def run(self, cmd, timeout=25):
        return self._chan(cmd, None, timeout)

    def run_stdin(self, cmd, data, timeout=40):
        return self._chan(cmd, data, timeout)

    def upload(self, local, remote, progress=None):
        """优先 SFTP；远端没有 SFTP 子系统时自动退到 base64 通道。

        为什么要退：小米路由器开 SSH 后跑的是 XMiR-SSH（dropbearmulti），
        它 **没编译 sftp 子系统**，远端也没有 scp。此时 open_sftp() 会直接
        `EOF during negotiation`，部署会在第一个文件上就断掉。
        """
        if self.sftp_ok is False:
            return self._upload_b64(local, remote, progress)
        try:
            sftp = self.client.open_sftp()
        except Exception as e:
            self.sftp_ok = False
            self.xfer_note = ('这台路由器没有 SFTP（%s），已自动改用 base64 通道上传。'
                              % (str(e) or type(e).__name__))
            return self._upload_b64(local, remote, progress)
        try:
            if progress:
                sftp.put(local, remote,
                         callback=lambda done, total: progress(done, total))
            else:
                sftp.put(local, remote)
            self.sftp_ok = True
        except Exception as e:
            self.sftp_ok = False
            self.xfer_note = ('SFTP 传输中断（%s），已自动改用 base64 通道上传。'
                              % (str(e) or type(e).__name__))
            return self._upload_b64(local, remote, progress)
        finally:
            try:
                sftp.close()
            except Exception:
                pass

    def _upload_b64(self, local, remote, progress=None):
        """没有 SFTP 时的上传通道：本地 base64 → 远端 `base64 -d`。

        内容走 exec 通道的 stdin，因此不受 shell 命令行长度限制；
        写完先落 `.new` 再 mv，避免中途出错留下半截文件；最后按字节数校验。
        """
        with open(local, 'rb') as fh:
            raw = fh.read()
        payload = base64.b64encode(raw)
        tmp = remote + '.new'
        rc, out = self.run_stdin(
            'base64 -d > %s && mv %s %s && echo B64_OK' % (tmp, tmp, remote),
            payload, timeout=60)
        if 'B64_OK' not in out:
            raise OpsError('上传 %s 失败：远端没有 base64 命令或写入被拒。\n%s'
                           % (os.path.basename(local),
                              out.strip() or '(远端无输出)'))
        rc, out = self.run('wc -c < %s' % remote)
        try:
            got = int((out or '').strip().split()[0])
        except Exception:
            got = -1
        if got != len(raw):
            raise OpsError('上传 %s 后校验不一致：本地 %d 字节 / 远端 %d 字节'
                           % (os.path.basename(local), len(raw), got))
        if progress:
            progress(len(raw), len(raw))

    def close(self):
        try:
            self.client.close()
        except Exception:
            pass


def find_putty_tools():
    """找 plink/pscp（本机没装 PuTTY 时返回 (None, None)）"""
    here = os.path.dirname(os.path.abspath(__file__))
    cands = []
    for base in (os.path.join(here, 'tools'), here):
        for exe in ('plink.exe', 'pscp.exe'):
            cands.append(os.path.join(base, exe))
    for env in ('ProgramFiles', 'ProgramFiles(x86)', 'LOCALAPPDATA'):
        root = os.environ.get(env, '')
        if root:
            for sub in ('PuTTY', r'Programs\PuTTY'):
                for exe in ('plink.exe', 'pscp.exe'):
                    cands.append(os.path.join(root, sub, exe))
    plink = pscp = None
    for p in cands:
        if not os.path.isfile(p):
            continue
        if os.path.basename(p).lower() == 'plink.exe' and not plink:
            plink = p
        elif os.path.basename(p).lower() == 'pscp.exe' and not pscp:
            pscp = p
    for name, cur in (('plink.exe', plink), ('pscp.exe', pscp)):
        if cur:
            continue
        for d in os.environ.get('PATH', '').split(os.pathsep):
            c = os.path.join(d, name)
            if os.path.isfile(c):
                if name == 'plink.exe':
                    plink = c
                else:
                    pscp = c
                break
    return plink, pscp


class PlinkBackend(Backend):
    name = 'plink'

    def __init__(self, host, port, user, password, fingerprint, plink, pscp):
        self.host, self.port, self.user, self.password = host, port, user, password
        self.fingerprint, self.plink, self.pscp = fingerprint, plink, pscp
        self.new_fingerprint = ''
        rc, out = self._exec(['echo READY'])
        if 'READY' not in out:
            m = re.search(r'(ssh-\S+)\s+(\d+)\s+(SHA256:[A-Za-z0-9+/=]+)', out)
            if m:
                self.fingerprint = '%s %s' % (m.group(1), m.group(3))
                self.new_fingerprint = self.fingerprint
                rc, out = self._exec(['echo READY'])
        if 'READY' not in out:
            low = out.lower()
            if 'access denied' in low or 'authentication failed' in low:
                raise OpsError('路由器密码不对（或用户名不是 root）。')
            raise OpsError('连不上路由器 %s:%s\n'
                           '最常见的原因：这台电脑现在没连该路由器的 WiFi。'
                           % (host, port))

    def _base(self, scp=False):
        a = ['-scp' if scp else '-ssh', '-batch', '-P', str(self.port)]
        if self.fingerprint:
            a += ['-hostkey', self.fingerprint]
        return a + ['-pw', self.password, '%s@%s' % (self.user, self.host)]

    def _exec(self, tail, data=None, timeout=30):
        args = [self.plink] + self._base() + list(tail)
        try:
            p = subprocess.run(args, input=data, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise OpsError('操作超时（%ss），路由器没有响应。' % timeout)
        except OSError as e:
            raise OpsError('无法运行 plink：%s' % e)
        return p.returncode, ((p.stdout or b'') + (p.stderr or b'')).decode(
            'utf-8', 'replace')

    def run(self, cmd, timeout=25):
        return self._exec([cmd], timeout=timeout)

    def run_stdin(self, cmd, data, timeout=40):
        payload = data.replace('\r\n', '\n') if isinstance(data, str) else data
        return self._exec(cmd.split(' '), data=payload, timeout=timeout)

    def upload(self, local, remote, progress=None):
        """pscp 优先；失败则退到 base64 通道（有些固件没 sftp/scp，见 ParamikoBackend）。"""
        if self.pscp:
            args = [self.pscp] + self._base(scp=True) + \
                ['-q', local, '%s@%s:%s' % (self.user, self.host, remote)]
            why = ''
            try:
                p = subprocess.run(args, capture_output=True, timeout=180)
                if p.returncode == 0:
                    return
                why = (((p.stdout or b'') + (p.stderr or b''))
                       .decode('utf-8', 'replace').strip().splitlines() or [''])
                why = why[-1][:120]
            except subprocess.TimeoutExpired:
                why = '超时'
            except OSError as e:
                why = str(e)
            self.xfer_note = ('pscp 上传不可用（%s），已自动改用 base64 通道上传。'
                              % (why or '未知原因'))
        else:
            self.xfer_note = '没找到 pscp.exe，已自动改用 base64 通道上传。'
        self._upload_b64(local, remote, progress)

    def _upload_b64(self, local, remote, progress=None):
        """没有 sftp/scp 时的上传通道：base64 文本走 heredoc 落 /tmp，再解码过半。
        用 /tmp 中转是为了不拿目标分区反复写（延长 flash 寿命）。"""
        with open(local, 'rb') as fh:
            raw = fh.read()
        b64 = base64.b64encode(raw).decode('ascii')
        script = ("cat > /tmp/_up.b64 <<'B64EOF'\n%s\nB64EOF\n"
                  "base64 -d /tmp/_up.b64 > %s.new && mv %s.new %s "
                  "&& rm -f /tmp/_up.b64 && echo B64_OK\n"
                  % (b64, remote, remote, remote))
        rc, out = self.run_script(script, timeout=120)
        if 'B64_OK' not in out:
            raise OpsError('上传 %s 失败：%s'
                           % (os.path.basename(local), out.strip() or '(远端无输出)'))
        if progress:
            progress(len(raw), len(raw))


def make_backend(cfg, password, note=None):
    host, port = cfg['host'], int(cfg['port'])
    user, fp = cfg['user'], cfg.get('host_fingerprint', '')
    try:
        import paramiko  # noqa: F401
        has_paramiko = True
    except ImportError:
        has_paramiko = False
    plink, pscp = find_putty_tools()
    prefer = cfg.get('backend', 'auto')

    if prefer == 'plink' and plink:
        return PlinkBackend(host, port, user, password, fp, plink, pscp)
    if has_paramiko:
        return ParamikoBackend(host, port, user, password, fp)
    if plink:
        if note:
            note('未安装 paramiko，已改用 PuTTY(plink) 连接')
        return PlinkBackend(host, port, user, password, fp, plink, pscp)
    raise OpsError('没有可用的 SSH 组件。\n\n'
                   '推荐：点主界面「一键安装」自动装好 paramiko（约 10-30 秒）。\n'
                   '或者：装一个 PuTTY（https://www.putty.org）后重新打开本程序。')


# ---------------------------------------------------------------- 远端脚本

PERM_SCRIPT = """set -e
chmod 755 {R}/bin/*.sh
chmod 644 {R}/conf/config.ini
chmod 700 {R}/key
chmod 755 {R}
rm -f {R}/conf/cred.bin {R}/key/campus.key
echo PERM_OK
"""

CRON_SCRIPT = """LINE='{LINE}'
for f in {TABS}; do
  [ -f "$f" ] || : > "$f"
  grep -v 'campus_login\\.sh' "$f" > "$f.new" 2>/dev/null
  [ -f "$f.new" ] || : > "$f.new"
  cat "$f.new" > "$f"
  rm -f "$f.new"
  [ -n "$(tail -c 1 "$f" 2>/dev/null)" ] && printf '\\n' >> "$f"
  printf '%s\\n' "$LINE" >> "$f"
done
pgrep crond >/dev/null 2>&1 || /etc/init.d/cron start >/dev/null 2>&1
/etc/init.d/cron restart >/dev/null 2>&1
sleep 1
n=0; o=0
for f in {TABS}; do
  n=$((n + $(grep -cF "$LINE" "$f" 2>/dev/null || true)))
  o=$((o + $(grep -c 'campus_login\\.sh' "$f" 2>/dev/null || true)))
done
[ "$n" -ge 1 ] || {{ echo CRON_FAIL reason=missing; exit 1; }}
[ "$n" -eq "$o" ] || {{ echo "CRON_FAIL reason=stale stale=$((o - n))"; exit 1; }}
echo "CRON_OK n=$n"
"""

FLAG_SCRIPT = """FLAG={R}/enabled
if [ "{ON}" = "1" ]; then
  umask 077
  date '+%F %T' > "$FLAG" && echo FLAG_OK
else
  rm -f "$FLAG" && echo FLAG_OK
fi
"""


def schedule_script(enabled, off_hhmm, on_hhmm):
    """写/清 crontab 里的自动时段行（带标记，便于整体替换）"""
    lines = ''
    if enabled:
        off = hhmm_to_cron(off_hhmm)
        on = hhmm_to_cron(on_hhmm)
        if not off or not on:
            raise OpsError('自动时段的时间格式不对，应为 HH:MM。')
        for (mi, h), act in ((off, 'off'), (on, 'on')):
            lines += ("  printf '%%s\\n' '%s %s * * * %s %s >/dev/null 2>&1' >> \"$f\"\n"
                      % (mi, h, REMOTE_SWITCH, act))
    return ("for f in {TABS}; do\n"
            "  [ -f \"$f\" ] || : > \"$f\"\n"
            "  grep -v -e '{MARK}' -e 'campus_switch.sh on' -e 'campus_switch.sh off' "
            "\"$f\" > \"$f.new\" 2>/dev/null\n"
            "  [ -f \"$f.new\" ] || : > \"$f.new\"\n"
            "  cat \"$f.new\" > \"$f\"\n"
            "  rm -f \"$f.new\"\n"
            "  [ -n \"$(tail -c 1 \"$f\" 2>/dev/null)\" ] && printf '\\n' >> \"$f\"\n"
            "  printf '%s\\n' '{MARK}' >> \"$f\"\n"
            "{LINES}done\n"
            "/etc/init.d/cron restart >/dev/null 2>&1\n"
            "sleep 1\n"
            "echo SCHED_OK\n").format(TABS=' '.join(CRONTABS), MARK=SCHED_MARK,
                                      LINES=lines)


def parse_status(text):
    """解析 campus_switch.sh status 的输出（对旧版本也容错）"""
    st = {'raw': text or '', 'deployed': True, 'script_ok': False,
          'switch_on': None, 'since': '', 'cron_line': '', 'cron_gated': False,
          'cron_stale': False, 'crond': '', 'schedule': None, 'log_lines': []}
    t = text or ''
    if not t or 'not found' in t or 'No such file' in t:
        st['deployed'] = False
        return st
    m = re.search(r'开关状态\s*[:：]\s*【(已开启|已关闭)】', t)
    if m:
        st['switch_on'] = (m.group(1) == '已开启')
    m = re.search(r'（(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) 开启）', t)
    if m:
        st['since'] = m.group(1)
    m = re.search(r'登录脚本\s*[:：]\s*(就绪|缺失)', t)
    st['script_ok'] = bool(m and m.group(1) == '就绪')
    m = re.search(r'crond\s*[:：]\s*(运行中|未运行)', t)
    st['crond'] = m.group(1) if m else ''
    st['cron_stale'] = '不含开关判定' in t
    m = re.search(r'^\s*(\S[^\n]*campus_login\.sh[^\n]*)$', t, re.M)
    if m:
        st['cron_line'] = m.group(1).strip()
        st['cron_gated'] = 'enabled' in st['cron_line']
    off = re.search(r'^\s*(\d+)\s+(\d+)\s+\*\s+\*\s+\*\s+\S*campus_switch\.sh off',
                    t, re.M)
    on = re.search(r'^\s*(\d+)\s+(\d+)\s+\*\s+\*\s+\*\s+\S*campus_switch\.sh on',
                   t, re.M)
    if off and on:
        st['schedule'] = (fmt_hhmm(int(off.group(2)) * 60 + int(off.group(1))),
                          fmt_hhmm(int(on.group(2)) * 60 + int(on.group(1))))
    if '最近日志' in t:
        tail = t.split('最近日志', 1)[1]
        for ln in tail.splitlines()[1:]:
            ln = ln.strip()
            if ln and not ln.startswith('（'):
                st['log_lines'].append(ln)
    return st


# ---------------------------------------------------------------- 远程操作

class RouterOps:
    def __init__(self, cfg, log=None):
        self.cfg = dict(cfg)
        self._log = log or (lambda m: None)
        self.be = None

    def connect(self, password, note=None):
        if self.be is None:
            self._log('正在连接 %s ...' % self.cfg['host'])
            self.be = make_backend(self.cfg, password, note)
            self._log('已连接（%s）' % self.be.name)
        return self.be

    def close(self):
        if self.be:
            self.be.close()
            self.be = None

    def status(self, password):
        be = self.connect(password)
        rc, out = be.run('%s status 2>&1' % REMOTE_SWITCH)
        st = parse_status(out)
        if not st['deployed'] and 'campus_switch.sh' in out:
            st['deployed'] = False
        st['legacy'] = False
        if not st['deployed']:
            rc2, out2 = be.run('test -f %s && echo HAS_LOGIN || echo NO_LOGIN'
                               % REMOTE_LOGIN)
            st['legacy'] = 'HAS_LOGIN' in out2
        return st

    def set_switch(self, password, on):
        be = self.connect(password)
        rc, out = be.run('%s %s 2>&1' % (REMOTE_SWITCH, 'on' if on else 'off'),
                         timeout=45)
        if rc == 0 or '已开启' in out or '已关闭' in out:
            return out.strip()
        raise OpsError(out.strip() or '切换开关失败')

    def login_now(self, password):
        be = self.connect(password)
        rc, out = be.run('CAMPUS_ROOT=%s %s; echo "EXIT=$?"; tail -3 %s 2>/dev/null'
                         % (REMOTE_ROOT, REMOTE_LOGIN, REMOTE_LOG), timeout=70)
        return rc, out

    def tail_log(self, password, lines=200):
        be = self.connect(password)
        rc, out = be.run('tail -n %d %s 2>/dev/null' % (int(lines), REMOTE_LOG))
        return out

    def set_schedule(self, password, enabled, off_hhmm, on_hhmm):
        be = self.connect(password)
        rc, out = be.run_script(schedule_script(enabled, off_hhmm, on_hhmm))
        if 'SCHED_OK' not in out:
            raise OpsError('写入自动时段失败：%s' % out.strip())
        return out.strip()

    def install_paramiko(self, log):
        log('正在安装 paramiko（10-30 秒）...')
        cmds = [[sys.executable, '-m', 'pip', 'install', 'paramiko',
                 '-i', 'https://pypi.tuna.tsinghua.edu.cn/simple'],
                [sys.executable, '-m', 'pip', 'install', 'paramiko']]
        last = None
        for args in cmds:
            try:
                p = subprocess.run(args, capture_output=True, timeout=600)
            except Exception as e:
                last = '安装命令无法执行：%s' % e
                continue
            out = ((p.stdout or b'') + (p.stderr or b'')).decode('utf-8', 'replace')
            for ln in out.strip().splitlines()[-5:]:
                log(ln.strip())
            if p.returncode == 0:
                log('安装完成。')
                return True
            last = 'pip 返回码 %s' % p.returncode
        raise OpsError('安装失败：%s\n可手动执行：\n  %s -m pip install paramiko'
                       % (last, sys.executable))

    def deploy(self, password, campus_user, campus_pass, payload_dir, enable, step):
        be = self.connect(password)
        if not payload_dir or not os.path.isdir(payload_dir):
            raise OpsError('找不到 payload 目录（程序文件）。请在设置里指定。')
        files = sorted(glob.glob(os.path.join(payload_dir, 'bin', '*.sh'))) + \
            sorted(glob.glob(os.path.join(payload_dir, 'conf', '*.ini')))
        names = [os.path.basename(f) for f in files]
        for need in ('campus_login.sh', 'campus_switch.sh', 'cred_admin.sh',
                     'lib_webauth.sh'):
            if need not in names:
                raise OpsError('payload 不完整：缺少 %s。\n请确认解压了整个压缩包。'
                               % need)

        step('准备目录')
        rc, out = be.run('mkdir -p %s/bin %s/conf %s/key'
                         % (REMOTE_ROOT, REMOTE_ROOT, REMOTE_ROOT))
        if rc != 0:
            raise OpsError('创建目录失败：%s' % out.strip())

        step('上传程序文件（共 %d 个）' % len(files))
        for i, f in enumerate(files, 1):
            base = os.path.basename(f)
            sub = 'bin' if base.lower().endswith('.sh') else 'conf'
            be.upload(f, '%s/%s/%s' % (REMOTE_ROOT, sub, base))
            step('上传 %d/%d  %s' % (i, len(files), base))
        note = getattr(be, 'xfer_note', '')
        if note:
            step(note)

        step('设置文件权限')
        rc, out = be.run_script(PERM_SCRIPT.format(R=REMOTE_ROOT))
        if 'PERM_OK' not in out:
            raise OpsError('设置权限失败：%s' % out.strip())

        step('加密保存校园网账号密码')
        rc, out = be.run_stdin('%s init --force --stdin' % REMOTE_CRED,
                               '%s\n%s\n' % (campus_user, campus_pass), timeout=45)
        if '已加密保存' not in out and '凭据已加密写入' not in out:
            raise OpsError('保存账号密码失败：%s' % out.strip())
        rc, out = be.run('%s verify' % REMOTE_CRED)
        if '凭据校验通过' not in out:
            raise OpsError('凭据校验失败：%s' % out.strip())

        step('写入定时任务（含旧版本迁移）')
        rc, out = be.run_script(CRON_SCRIPT.format(LINE=CRON_LINE,
                                                  TABS=' '.join(CRONTABS)))
        if 'CRON_OK' not in out:
            raise OpsError('设置定时任务失败：%s' % out.strip())
        if 'stale=' in out:
            raise OpsError('检测到旧版本残留的定时任务，请再点一次部署。')

        step('设置总开关：%s' % ('开启' if enable else '关闭'))
        rc, out = be.run_script(FLAG_SCRIPT.format(R=REMOTE_ROOT,
                                                  ON='1' if enable else '0'))
        if 'FLAG_OK' not in out:
            raise OpsError('设置总开关失败：%s' % out.strip())
        return True


# ---------------------------------------------------------------- 后台任务

class Runner:
    """慢操作丢后台线程；结果与日志都经队列回主线程（Tk 只能在主线程碰）"""

    def __init__(self, root, logq):
        self.root = root
        self.logq = logq
        self.q = queue.Queue()
        self.busy = False
        self._cb = None

    def submit(self, name, fn, on_done, interactive=True):
        if self.busy:
            if interactive:
                on_done(name, False, '有任务正在执行，请稍候再试。')
            return
        self.busy = True
        self._cb = (name, on_done)

        def worker():
            try:
                ok, payload = True, fn()
            except OpsError as e:
                ok, payload = False, str(e)
            except Exception as e:
                app_log('后台任务异常: %r' % e)
                ok, payload = False, '发生意外错误：%s' % e
            self.q.put((name, ok, payload))

        threading.Thread(target=worker, daemon=True).start()
        self.root.after(100, self._poll)

    def _poll(self):
        try:
            name, ok, payload = self.q.get_nowait()
        except queue.Empty:
            self.root.after(100, self._poll)
            return
        self.busy = False
        cb = self._cb[1] if self._cb else None
        self._cb = None
        if cb:
            cb(name, ok, payload)


# ---------------------------------------------------------------- 组件

# ---------------------------------------------------------------- 组件库
# 全部自绘（tk.Frame + tk.Label）。ttk 在 clam 主题下既放不进图标，也做不出稳定的
# 悬停/聚焦反馈，所以按钮、开关、复选、输入框都自己搭，换来一致的观感与完整的键盘可达性。

class Card(tk.Frame):
    """白底 + 1px 发丝边。可选 rail：左侧状态色竖条，把状态"染"到整块卡片上。"""

    def __init__(self, parent, padx=None, pady=None, rail=None):
        super().__init__(parent, bg=COL_CARD, highlightbackground=COL_BORDER,
                         highlightthickness=1, bd=0, highlightcolor=COL_BORDER)
        self.rail = None
        if rail:
            self.rail = tk.Frame(self, bg=rail, width=u(5), bd=0,
                                 highlightthickness=0)
            self.rail.pack(side='left', fill='y')
        self.body = tk.Frame(self, bg=COL_CARD)
        self.body.pack(side='left', fill='both', expand=True,
                       padx=u(SP4) if padx is None else padx,
                       pady=u(SP3) if pady is None else pady)

    def set_rail(self, color):
        if self.rail is not None:
            self.rail.config(bg=color)


class SectionHead(tk.Frame):
    """区域标题：图标 + 标题 + 说明。主界面和设置页共用，保证层级语言一致。"""

    def __init__(self, parent, title, icon=None, hint='', bg=COL_CARD,
                 size=T_SUB, hint_side='left'):
        super().__init__(parent, bg=bg)
        if icon:
            icon_label(self, icon, u(16), COL_MUTED, bg).pack(
                side='left', padx=(0, u(SP2)))
        self.title = tk.Label(self, text=title, bg=bg, fg=COL_TEXT,
                              font=(F_UI, size, 'bold'))
        self.title.pack(side='left')
        self.hint = None
        if hint:
            self.hint = tk.Label(self, text=hint, bg=bg, fg=COL_FAINT,
                                 font=(F_UI, T_CAPTION))
            self.hint.pack(side=hint_side,
                           padx=(u(SP2), 0) if hint_side == 'left' else (0, 0))
        self.bg = bg


class Chip(tk.Frame):
    """状态药丸：图标 + 文字，底色与描边都随语义变化。"""

    def __init__(self, parent, text='', kind='idle'):
        super().__init__(parent, bg=SEM['idle'][1], bd=0, highlightthickness=1,
                         highlightbackground=SEM['idle'][2],
                         highlightcolor=SEM['idle'][2])
        self._icon = icon_label(self, STATUS_ICON['idle'], u(13),
                                SEM['idle'][0], SEM['idle'][1])
        self._icon.pack(side='left', padx=(u(SP2), u(1)), pady=u(SP1))
        self._label = tk.Label(self, text=text, bg=SEM['idle'][1],
                               fg=SEM['idle'][0],
                               font=(F_UI, T_CAPTION, 'bold'))
        self._label.pack(side='left', padx=(0, u(SP2)), pady=u(SP1))
        self.set(text, kind)

    def set(self, text, kind='idle'):
        fg, bg, bd = SEM.get(kind, SEM['idle'])
        self.config(bg=bg, highlightbackground=bd)
        self._label.config(text=text, fg=fg, bg=bg)
        ph = icon_photo(STATUS_ICON.get(kind, 'dot_circle'), u(13), fg)
        if ph is not None and isinstance(self._icon, tk.Label):
            self._icon.config(image=ph, bg=bg)
            self._icon._icon_ref = ph


class StatCard(tk.Frame):
    """概览格。状态由「图标徽章底色 + 右上角圆点」表达，数值保持深色：
    整块文字都染成红绿会让界面变花，也降低可读性；只有真出问题才让数字变红。"""

    def __init__(self, parent, title, icon):
        super().__init__(parent, bg=COL_CARD, highlightbackground=COL_BORDER,
                         highlightthickness=1, bd=0)
        self.icon_name = icon
        inner = tk.Frame(self, bg=COL_CARD)
        inner.pack(fill='both', expand=True, padx=u(SP3), pady=u(SP3))
        top = tk.Frame(inner, bg=COL_CARD)
        top.pack(fill='x')
        self.badge = tk.Frame(top, bg=SEM['idle'][1], width=u(26), height=u(26),
                              bd=0, highlightthickness=0)
        self.badge.pack(side='left')
        self.badge.pack_propagate(False)
        self.icon = icon_label(self.badge, icon, u(15), SEM['idle'][0],
                               SEM['idle'][1])
        self.icon.pack(expand=True)
        self.dot = icon_label(top, 'dot', u(9), SEM['idle'][0], COL_CARD)
        self.dot.pack(side='right', pady=(u(2), 0))
        self.name = tk.Label(inner, text=title, bg=COL_CARD, fg=COL_FAINT,
                             font=(F_UI, T_CAPTION))
        self.name.pack(anchor='w', pady=(u(SP2) + u(1), 0))
        self.value = tk.Label(inner, text='—', bg=COL_CARD, fg=COL_MUTED,
                              font=(F_UI, T_BODY, 'bold'), anchor='w')
        self.value.pack(anchor='w')

    def set(self, value, kind='idle'):
        fg, bg, _bd = SEM.get(kind, SEM['idle'])
        self.value.config(text=value,
                          fg=fg if kind in ('err', 'off') else COL_TEXT)
        self.badge.config(bg=bg)
        if isinstance(self.icon, tk.Label):
            ph = icon_photo(self.icon_name, u(15), fg)
            if ph is not None:
                self.icon.config(image=ph, bg=bg)
                self.icon._icon_ref = ph
        if isinstance(self.dot, tk.Label):
            dph = icon_photo('dot', u(9), fg)
            if dph is not None:
                self.dot.config(image=dph)
                self.dot._icon_ref = dph


class FlatButton(tk.Frame):
    """自绘扁平按钮：内置图标、悬停/按下/禁用三态、键盘聚焦环。

    聚焦环借用 highlightcolor（只换颜色不改尺寸），这样获得焦点时布局不会跳动。
    """

    VARIANTS = {
        #         常态底      常态字     悬停底      按下底      常态描边        聚焦环
        'primary': (COL_ACCENT, '#ffffff', COL_ACCENT_D, COL_ACCENT_PRESS,
                    COL_ACCENT, COL_ACCENT_PRESS),
        'default': (COL_CARD, COL_TEXT, COL_HOVER, COL_PRESS,
                    COL_BORDER_STRONG, COL_ACCENT),
        'ghost':   (COL_CARD, COL_ACCENT_D, COL_ACCENT_L, '#dbe7ff',
                    COL_BORDER, COL_ACCENT),
        'soft':    (COL_SOFT, COL_MUTED, COL_HOVER, COL_PRESS,
                    COL_BORDER, COL_ACCENT),
        'warn':    (SEM['off'][1], SEM['off'][0], '#fbe9d4', '#f6e0c2',
                    SEM['off'][2], SEM['off'][0]),
        'danger':  (SEM['err'][1], SEM['err'][0], '#fbe0de', '#f7d2cf',
                    SEM['err'][2], SEM['err'][0]),
    }

    def __init__(self, parent, text='', icon=None, variant='default',
                 command=None, padx=None, pady=None, width=None, bold=False):
        self.variant = variant
        bg, fg, hov, prs, bd, focus = self.VARIANTS.get(
            variant, self.VARIANTS['default'])
        super().__init__(parent, bg=bg, bd=0, cursor='hand2', takefocus=True,
                         highlightthickness=1, highlightbackground=bd,
                         highlightcolor=focus)
        self.command = command
        self.enabled = True
        self._icon_name = icon
        self._pad_x = u(SP4) if padx is None else padx
        self._pad_y = (u(SP2) + u(1)) if pady is None else pady
        kids = []
        if icon:
            self.icon = icon_label(self, icon, u(15), fg, bg)
            self.icon.pack(side='left', padx=(self._pad_x, u(SP2)),
                           pady=self._pad_y)
            kids.append(self.icon)
        else:
            self.icon = None
        self.label = tk.Label(self, text=text, bg=bg, fg=fg,
                              font=(F_UI, T_BODY, 'bold' if bold else 'normal'),
                              width=width)
        self.label.pack(side='left',
                        padx=((0 if icon else self._pad_x), self._pad_x),
                        pady=self._pad_y)
        kids.append(self.label)
        for w in [self] + kids:
            w.bind('<Button-1>', self._press)
            w.bind('<ButtonRelease-1>', self._release)
            w.bind('<Enter>', self._enter)
            w.bind('<Leave>', self._leave)
            w.bind('<Return>', lambda e: self.invoke())
            w.bind('<space>', lambda e: self.invoke())
        self.bind('<FocusIn>', lambda e: self._paint('hover'))
        self.bind('<FocusOut>', lambda e: self._paint('normal'))
        self._paint('normal')

    # --- 内部 ---
    def _pressed_inside(self):
        return self._hover

    def _inside(self, w):
        while w is not None:
            if w is self:
                return True
            w = getattr(w, 'master', None)
        return False

    def _paint(self, mode):
        if not self.enabled:
            bg, fg = COL_SOFT, COL_DISABLED
        else:
            bg, fg, hov, prs, _bd, _f = self.VARIANTS.get(
                self.variant, self.VARIANTS['default'])
            bg = {'normal': bg, 'hover': hov, 'press': prs}.get(mode, bg)
        self.config(bg=bg)
        self.label.config(bg=bg, fg=fg)
        if isinstance(self.icon, tk.Label):
            ph = icon_photo(self._icon_name, u(15), fg)
            if ph is not None:
                self.icon.config(image=ph, bg=bg)
                self.icon._icon_ref = ph

    def _enter(self, _e=None):
        self._hover = True
        if self.enabled:
            self._paint('hover')

    def _leave(self, e=None):
        # 指针移到内部子控件上时 Tk 也会发 <Leave>，这里用坐标判定是否真的离开了
        if e is not None:
            try:
                w = self.winfo_containing(e.x_root, e.y_root)
            except Exception:
                w = None
            if w is not None and self._inside(w):
                return
        self._hover = False
        if self.enabled:
            self._paint('normal')

    def _press(self, _e=None):
        if self.enabled:
            self._paint('press')

    def _release(self, e=None):
        if not self.enabled:
            return
        self._paint('hover' if self._hover else 'normal')
        if e is not None:
            try:
                w = self.winfo_containing(e.x_root, e.y_root)
            except Exception:
                w = None
            if w is None or not self._inside(w):
                return
        self.invoke()

    # --- 对外 ---
    _hover = False

    def invoke(self):
        if self.enabled and self.command:
            self.command()

    def set_state(self, state):
        self.enabled = (state != 'disabled')
        self.config(cursor='hand2' if self.enabled else 'arrow')
        self._paint('normal')

    def set_text(self, text):
        self.label.config(text=text)

    def set_icon(self, name):
        self._icon_name = name
        self._paint('normal')


class SegmentedSwitch(tk.Frame):
    """二选一开关。比"两个并列按钮"好的地方：当前状态一眼可见，也不会出现
    「两个都灰着、不知道能点哪个」的困惑。"""

    def __init__(self, parent, segments, command=None):
        super().__init__(parent, bg=COL_CARD,
                         highlightbackground=COL_BORDER_STRONG,
                         highlightthickness=1, bd=0)
        self.command = command
        self.enabled = True
        self.value = None
        self._segs = []
        for i, (text, icon) in enumerate(segments):
            if i:
                tk.Frame(self, bg=COL_BORDER, width=1).grid(
                    row=0, column=1, sticky='ns')
            seg = tk.Frame(self, bg=COL_CARD, cursor='hand2', takefocus=True)
            seg.grid(row=0, column=i * 2, sticky='nsew')
            self.columnconfigure(i * 2, weight=1)
            ic = icon_label(seg, icon, u(15), COL_MUTED, COL_CARD)
            ic.pack(side='left', padx=(u(SP3), u(SP2)), pady=u(SP2) + u(1))
            lb = tk.Label(seg, text=text, bg=COL_CARD, fg=COL_MUTED,
                          font=(F_UI, T_BODY))
            lb.pack(side='left', padx=(0, u(SP3)), pady=u(SP2) + u(1))
            self._segs.append({'seg': seg, 'icon': ic, 'label': lb,
                               'name': icon, 'val': (i == 0)})
            for w in (seg, ic, lb):
                w.bind('<Button-1>', lambda e, v=(i == 0): self._click(v))
                w.bind('<Return>', lambda e, v=(i == 0): self._click(v))
                w.bind('<space>', lambda e, v=(i == 0): self._click(v))
        self.set(None)

    def _click(self, val):
        # 点当前已选中的那一段不做任何事：原来是靠把按钮置灰来避免重复触发，
        # 那样反而让人以为"不能点"，这里改成静默忽略，按钮外观保持不变。
        if self.enabled and self.value is not None and val == self.value:
            return
        if self.enabled and self.command:
            self.command(val)

    def set(self, value):
        self.value = value
        for s in self._segs:
            on = (value is not None and s['val'] == value)
            if on:
                fg, bg, _bd = SEM['ok' if value else 'off']
            else:
                fg, bg = COL_MUTED, COL_CARD
            s['seg'].config(bg=bg)
            s['label'].config(bg=bg, fg=fg,
                              font=(F_UI, T_BODY, 'bold' if on else 'normal'))
            if isinstance(s['icon'], tk.Label):
                ph = icon_photo(s['name'], u(15),
                                fg if self.enabled else COL_DISABLED)
                if ph is not None:
                    s['icon'].config(image=ph, bg=bg)
                    s['icon']._icon_ref = ph

    def set_enabled(self, flag):
        self.enabled = bool(flag)
        self.config(cursor='hand2' if self.enabled else 'arrow')
        for s in self._segs:
            s['seg'].config(cursor='hand2' if self.enabled else 'arrow')


class CheckBox(tk.Frame):
    """自绘复选框（clam 主题的勾选标记会渲染成一个 '*'，很丑）。"""

    def __init__(self, parent, text='', variable=None, command=None, bg=COL_CARD):
        super().__init__(parent, bg=bg, cursor='hand2', takefocus=True)
        self.var = variable if variable is not None else tk.BooleanVar(value=False)
        self.command = command
        self._bg = bg
        self._box = icon_label(self, 'checkbox_off', u(16), COL_BORDER_STRONG, bg)
        self._box.pack(side='left')
        self._text = tk.Label(self, text=text, bg=bg, fg=COL_TEXT,
                              font=(F_UI, T_BODY))
        self._text.pack(side='left', padx=(u(SP2), 0))
        for w in (self, self._box, self._text):
            w.bind('<Button-1>', self._toggle)
            w.bind('<Return>', self._toggle)
            w.bind('<space>', self._toggle)
        self.bind('<FocusIn>', lambda e: self._focus(True))
        self.bind('<FocusOut>', lambda e: self._focus(False))
        # 监听变量的外部写入：render_status() 会直接 var.set(True)，
        # 不跟踪的话勾选状态就只更新了数据、没更新画面（之前真踩到过）
        try:
            self.var.trace_add('write', self._on_var)
        except Exception:
            pass
        self._paint()

    def _on_var(self, *_a):
        self._paint()

    def _focus(self, on):
        # 用浅色底表达聚焦，不去改 highlightthickness，避免布局跳动
        c = COL_ACCENT_L if on else self._bg
        self.config(bg=c)
        self._box.config(bg=c)
        self._text.config(bg=c)

    def _toggle(self, _e=None):
        self.var.set(not self.var.get())     # 变量变了会经 trace 触发重绘
        if self.command:
            self.command()

    def set(self, value):
        self.var.set(bool(value))

    def get(self):
        return bool(self.var.get())

    def _paint(self):
        on = bool(self.var.get())
        if isinstance(self._box, tk.Label):
            ph = icon_photo('checkbox_on' if on else 'checkbox_off', u(16),
                            COL_ACCENT if on else COL_BORDER_STRONG)
            if ph is not None:
                self._box.config(image=ph)
                self._box._icon_ref = ph
        self._text.config(fg=COL_TEXT if on else COL_MUTED)


class Field(tk.Frame):
    """输入框。ttk.Entry 在 clam 下的聚焦态几乎看不出来，这里自己画聚焦描边。"""

    def __init__(self, parent, textvariable=None, width=16, show=None,
                 justify='left', bg=COL_CARD):
        super().__init__(parent, bg=bg, highlightbackground=COL_BORDER_STRONG,
                         highlightthickness=1, bd=0, highlightcolor=COL_ACCENT)
        self.entry = tk.Entry(self, textvariable=textvariable, width=width,
                              bd=0, relief='flat', bg=bg, fg=COL_TEXT,
                              insertbackground=COL_TEXT, font=(F_UI, T_BODY),
                              justify=justify, highlightthickness=0,
                              disabledbackground=COL_SOFT)
        if show:
            self.entry.config(show=show)
        self.entry.pack(fill='both', expand=True, padx=u(SP2),
                        pady=max(2, u(SP2) - 1))
        self.entry.bind('<FocusIn>', lambda e: self.config(
            highlightbackground=COL_ACCENT))
        self.entry.bind('<FocusOut>', lambda e: self.config(
            highlightbackground=COL_BORDER_STRONG))

    def set_show(self, show):
        self.entry.config(show=show)

    def get(self):
        return self.entry.get()

    def set_state(self, state):
        self.entry.config(state=state)

    def see_end(self):
        """把内容滚到最右端，露出**最后一段**。

        路径这类长文本，左边永远是 C:\\Users\\… 那串没信息量的前缀，
        被截断掉的偏偏是最关键的文件夹名（截图里只显示到 \\campus）。
        等布局完成再滚：宽度还没算出来时 xview_moveto 不起作用。
        """
        def _go():
            try:
                self.entry.icursor('end')
                self.entry.xview_moveto(1.0)
            except Exception:
                pass
        try:
            self.entry.after_idle(_go)
        except Exception:
            _go()

    def bind_return(self, fn):
        self.entry.bind('<Return>', fn)


# ---------------------------------------------------------------- 主窗口

class MainWindow:
    def __init__(self):
        self.cfg = load_config()
        self.logq = queue.Queue()
        self.runner = None
        self.ops = None
        self.status = None
        self.last_log_tail = ''
        self.paused = bool(self.cfg.get('log_pause'))
        self.icon = None
        self._quitting = False
        self.scale = 1.0
        self.has_paramiko = self._check_paramiko()
        self.putty = find_putty_tools()

        self._build()
        self.runner = Runner(self.root, self.logq)
        self.root.after(100, self._drain_logq)
        self.root.after(400, self._do_status)
        self.root.after(1200, self._tick_log)
        self.root.after(20000, self._tick_status)

    @staticmethod
    def _check_paramiko():
        try:
            import paramiko  # noqa: F401
            return True
        except ImportError:
            return False

    # ---------- 密码 ----------
    def router_password(self):
        return dpapi_decrypt(self.cfg.get('router_password_enc', ''))

    def campus_password(self):
        return dpapi_decrypt(self.cfg.get('campus_password_enc', ''))

    def backend_name(self):
        """当前可用的 SSH 后端名。

        优先报实例真正用的那个；实例还没建起来（刚启动、尚未保存密码）时，
        退回「探测到的可用后端」。这里**不能**返回 '—' ——
        调用方会把它拼进「已连接 · %s」，于是界面出现「已连接 · —」这种废话。
        实在一个都没有才返回空串，由调用方决定怎么措辞。
        """
        try:
            n = self.ops.be.name
            if n:
                return n
        except Exception:
            pass
        if self.has_paramiko:
            return 'paramiko'
        if self.putty and self.putty[0]:
            return 'plink'
        return ''

    def link_text(self):
        """「连接方式」格的文案。后端未知时只说「已连接」，不留悬空的 · 分隔符。"""
        n = self.backend_name()
        return '已连接 · %s' % n if n else '已连接'

    # ---------- 样式 ----------
    def _setup_style(self):
        global UI_SCALE, F_UI, F_MONO
        dpi = system_dpi()
        self.scale = dpi / 96.0
        UI_SCALE = self.scale        # 让 u() 立刻生效：之后所有间距都跟着 DPI 缩放
        try:
            self.root.tk.call('tk', 'scaling', dpi / 72.0)
        except Exception:
            pass
        # 字体兜底：万一系统没有雅黑/Consolas，退到还有的字体，别渲染成方块
        try:
            import tkinter.font as tkfont
            have = set(tkfont.families(self.root))
            for cand in ('Microsoft YaHei UI', 'Microsoft YaHei', 'Segoe UI',
                         'Tahoma'):
                if cand in have:
                    F_UI = cand
                    break
            for cand in ('Consolas', 'Cascadia Mono', 'Lucida Console',
                         'Courier New'):
                if cand in have:
                    F_MONO = cand
                    break
        except Exception:
            pass
        st = ttk.Style(self.root)
        try:
            st.theme_use('clam')
        except Exception:
            pass
        st.configure('.', font=(F_UI, T_BODY), background=COL_CARD,
                     foreground=COL_TEXT)
        st.configure('TFrame', background=COL_CARD)
        st.configure('TLabel', background=COL_CARD)
        st.configure('Vertical.TScrollbar', background=COL_RAIL,
                     troughcolor=COL_SOFT, bordercolor=COL_SOFT,
                     arrowcolor=COL_MUTED, lightcolor=COL_RAIL,
                     darkcolor=COL_RAIL, relief='flat', arrowsize=u(11),
                     width=u(11))
        st.map('Vertical.TScrollbar',
               background=[('active', COL_BORDER_STRONG),
                           ('pressed', COL_BORDER_STRONG)])

    def _apply_window_icon(self):
        """窗口/任务栏图标。

        三条路都要走，缺一条就会在某个位置露出 Python 的默认图标：
        AUMID（任务栏身份，见 init_app_identity）+ .ico（多尺寸）
        + iconphoto（不依赖文件的兜底），最后再把大图标按系统尺寸显式设一遍。
        """
        try:
            ico = ensure_app_icon()
            if ico:
                self.root.iconbitmap(ico)
                self.root.iconbitmap(default=ico)
        except Exception as e:
            app_log('设置窗口图标(.ico)失败: %s' % e)
        try:
            m = _pil()
            if m is not None:
                img = brand_image(64)
                if img is not None:
                    self._icon_ref = m[2].PhotoImage(img)
                    self.root.iconphoto(True, self._icon_ref)
        except Exception as e:
            app_log('设置窗口图标(photo)失败: %s' % e)
        # Tk 设完之后再用 Win32 把任务栏那一档尺寸钉死（高 DPI 下差得最明显）
        try:
            self.root.update_idletasks()
            set_taskbar_icon(self.root, ico or APP_ICON_FILE)
        except Exception as e:
            app_log('设置任务栏图标失败: %s' % e)

    # ---------- 构建 ----------
    def _build(self):
        # 兜底再声明一次（幂等）：直接 new 出 MainWindow 的调用路径
        # 没走 main()，漏了这步任务栏就还是 Python 的图标
        init_app_identity()
        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.configure(bg=COL_CANVAS)
        self.root.protocol('WM_DELETE_WINDOW', self.on_close)
        self._setup_style()
        self._apply_window_icon()
        pad = u(SP5)

        # ---- 顶栏：改为白底发丝线。原来那条实心蓝条太重，和"主操作按钮"
        #      抢视觉焦点，而且白占 90px 高度。状态药丸挪到右侧同一行。----
        tk.Frame(self.root, bg=COL_ACCENT, height=2).pack(fill='x')
        header = tk.Frame(self.root, bg=COL_CARD)
        header.pack(fill='x')
        hin = tk.Frame(header, bg=COL_CARD)
        hin.pack(fill='x', padx=pad, pady=u(SP3))
        badge_label(hin, 'wifi', u(32), COL_ACCENT, COL_CARD, '#ffffff').pack(
            side='left')
        ht = tk.Frame(hin, bg=COL_CARD)
        ht.pack(side='left', padx=(u(SP3), 0))
        tk.Label(ht, text=APP_NAME, bg=COL_CARD, fg=COL_TEXT,
                 font=(F_UI, T_H3, 'bold')).pack(anchor='w')
        tk.Label(ht, text=APP_DESC, bg=COL_CARD, fg=COL_FAINT,
                 font=(F_UI, T_MICRO)).pack(anchor='w')
        self.chip_header = Chip(hin, '初始化中', 'info')
        self.chip_header.pack(side='right')
        tk.Frame(self.root, bg=COL_BORDER, height=1).pack(fill='x')

        outer = tk.Frame(self.root, bg=COL_CANVAS)
        outer.pack(fill='both', expand=True, padx=pad, pady=pad)

        # ---- 缺组件提示条 ----
        self.banner = tk.Frame(outer, bg=SEM['off'][1],
                               highlightbackground=SEM['off'][2],
                               highlightthickness=1, bd=0)
        bin_ = tk.Frame(self.banner, bg=SEM['off'][1])
        bin_.pack(fill='x', padx=u(SP3), pady=u(SP2))
        FlatButton(bin_, '一键安装', icon='download', variant='warn',
                   command=self.install_paramiko).pack(side='right')
        icon_label(bin_, 'alert', u(16), SEM['off'][0], SEM['off'][1]).pack(
            side='left', padx=(0, u(SP2)))
        tk.Label(bin_, text='缺少 SSH 组件（paramiko）', bg=SEM['off'][1],
                 fg=SEM['off'][0], font=(F_UI, T_BODY, 'bold')).pack(side='left')
        tk.Label(bin_, text='装好即可连接路由器，约 10–30 秒', bg=SEM['off'][1],
                 fg=SEM['off'][0], font=(F_UI, T_CAPTION)).pack(
            side='left', padx=(u(SP2), 0))
        if not self.has_paramiko:
            self.banner.pack(fill='x', pady=(0, u(SP3)))

        # ---- 状态主卡：左侧状态色竖条 + 徽章，全屏最重的一块 ----
        self.hero = Card(outer, rail=SEM['idle'][2],
                         padx=u(SP5), pady=u(SP4) + u(1))
        self.hero.pack(fill='x')
        hl = tk.Frame(self.hero.body, bg=COL_CARD)
        hl.pack(side='left', fill='both', expand=True)
        self.hero_badge = badge_label(hl, 'dot_circle', u(46), SEM['idle'][0],
                                      COL_CARD, '#ffffff')
        self.hero_badge.pack(side='left', anchor='n', padx=(0, u(SP4)))
        hcol = tk.Frame(hl, bg=COL_CARD)
        hcol.pack(side='left', fill='both', expand=True)
        self.hero_title = tk.Label(hcol, text='正在读取状态…', bg=COL_CARD,
                                   fg=COL_TEXT, font=(F_UI, T_H1, 'bold'),
                                   anchor='w', justify='left')
        self.hero_title.pack(fill='x')
        self.hero_desc = tk.Label(hcol, text='', bg=COL_CARD, fg=COL_MUTED,
                                  font=(F_UI, T_BODY), justify='left',
                                  anchor='w', wraplength=int(400 * self.scale))
        self.hero_desc.pack(fill='x', pady=(u(SP1) + 1, 0))
        self.hero_hint = tk.Label(hcol, text='', bg=COL_CARD, fg=COL_FAINT,
                                  font=(F_UI, T_CAPTION), justify='left',
                                  anchor='w', wraplength=int(400 * self.scale))
        self.hero_hint.pack(fill='x', pady=(u(SP2), 0))

        hr = tk.Frame(self.hero.body, bg=COL_CARD)
        hr.pack(side='right', padx=(u(SP5), 0))
        self.switch = SegmentedSwitch(
            hr, [('开启自动登录', 'power'), ('关闭自动登录', 'pause_circle')],
            command=self.set_switch)
        self.switch.pack(fill='x')
        self.btn_login = FlatButton(hr, '立即登录一次', icon='bolt',
                                    variant='ghost', command=self.login_now)
        self.btn_login.pack(fill='x', pady=(u(SP2), 0))

        # ---- 概览四格 ----
        tiles = tk.Frame(outer, bg=COL_CANVAS)
        tiles.pack(fill='x', pady=(u(SP3), 0))
        self.tile_cron = StatCard(tiles, '自动重连', 'clock')
        self.tile_script = StatCard(tiles, '登录程序', 'file')
        self.tile_crond = StatCard(tiles, '定时服务', 'pulse')
        self.tile_link = StatCard(tiles, '连接方式', 'plug')
        for i, t in enumerate((self.tile_cron, self.tile_script,
                               self.tile_crond, self.tile_link)):
            t.grid(row=0, column=i, sticky='nsew',
                   padx=(0 if i == 0 else u(SP3), 0))
            tiles.columnconfigure(i, weight=1)

        # ---- 自动时段 ----
        sch = Card(outer)
        sch.pack(fill='x', pady=(u(SP3), 0))
        self.var_sched_on = tk.BooleanVar(value=bool(self.cfg.get('sched_enabled')))
        self.var_off = tk.StringVar(value=self.cfg.get('sched_off', '08:00'))
        self.var_on = tk.StringVar(value=self.cfg.get('sched_on', '22:00'))
        sh = SectionHead(sch.body, '自动时段', 'clock', '到点自动开 / 关，出门不用惦记')
        sh.pack(fill='x')
        self.sched_hint = tk.Label(sh, text='', bg=COL_CARD, fg=COL_FAINT,
                                   font=(F_UI, T_CAPTION))
        self.sched_hint.pack(side='right')

        srow = tk.Frame(sch.body, bg=COL_CARD)
        srow.pack(fill='x', pady=(u(SP3), 0))
        self.cb_sched = CheckBox(srow, '启用', variable=self.var_sched_on,
                                 command=self._sched_toggle)
        self.cb_sched.pack(side='left')
        tk.Label(srow, text='每天', bg=COL_CARD, fg=COL_MUTED,
                 font=(F_UI, T_BODY)).pack(side='left', padx=(u(SP4), u(SP2)))
        self.e_off = Field(srow, self.var_off, width=6, justify='center')
        self.e_off.pack(side='left')
        tk.Label(srow, text='自动关闭', bg=COL_CARD, fg=COL_MUTED,
                 font=(F_UI, T_CAPTION)).pack(side='left', padx=(u(SP2), u(SP4)))
        self.e_on = Field(srow, self.var_on, width=6, justify='center')
        self.e_on.pack(side='left')
        tk.Label(srow, text='自动开启', bg=COL_CARD, fg=COL_MUTED,
                 font=(F_UI, T_CAPTION)).pack(side='left', padx=(u(SP2), u(SP4)))
        FlatButton(srow, '保存到路由器', icon='upload', variant='default',
                   command=self.save_schedule).pack(side='right')
        self._sched_toggle()

        # ---- 运行日志 ----
        logcard = Card(outer, padx=0, pady=0)
        logcard.pack(fill='both', expand=True, pady=(u(SP3), 0))
        # 注意：必须挂在 logcard.body 里。之前这里挂到了 logcard 上，
        # 于是空的 body 先被 pack 吃掉一半高度，日志标题被顶到卡片中间。
        lh = tk.Frame(logcard.body, bg=COL_CARD)
        lh.pack(fill='x', padx=u(SP4), pady=(u(SP3), u(SP2)))
        SectionHead(lh, '运行日志', 'pulse', '来自路由器 · 15 秒自动刷新'
                    ).pack(side='left')
        small = {'padx': u(SP3), 'pady': u(1) + 2}
        FlatButton(lh, '导出', icon='download', variant='ghost',
                   command=self.export_log, **small).pack(side='right',
                                                          padx=(u(SP2), 0))
        self.btn_pause = FlatButton(
            lh, '暂停' if not self.paused else '继续',
            icon='pause' if not self.paused else 'play', variant='ghost',
            command=self.toggle_pause, **small)
        self.btn_pause.pack(side='right', padx=(u(SP2), 0))
        FlatButton(lh, '清屏', icon='trash', variant='ghost',
                   command=self.clear_log, **small).pack(side='right')

        wrap = tk.Frame(logcard.body, bg=COL_LOG_BG,
                        highlightbackground=COL_BORDER, highlightthickness=1,
                        bd=0)
        wrap.pack(fill='both', expand=True, padx=u(SP4) - 1, pady=(0, u(SP4) - 1))
        # height 是**最小**高度，不是显示高度：卡片是 expand=True，
        # 窗口有多高日志就铺多高。这里给 4 是为了别把窗口的最小尺寸顶起来 ——
        # 200% 缩放的 1440x900 屏幕上，可用区只有 852 逻辑像素高，
        # 日志要按 9 行占最小尺寸，整窗最小高度就超过可用区，底栏永远露不出来。
        self.log = tk.Text(wrap, height=4, bg=COL_LOG_BG, fg=COL_MUTED, bd=0,
                           relief='flat', font=(F_MONO, T_MONO_SIZE),
                           wrap='none', padx=u(SP3), pady=u(SP2),
                           state='disabled', highlightthickness=0,
                           insertbackground=COL_TEXT,
                           selectbackground=COL_ACCENT_L,
                           selectforeground=COL_TEXT)
        sb = ttk.Scrollbar(wrap, orient='vertical', command=self.log.yview,
                           style='Vertical.TScrollbar')
        sb.pack(side='right', fill='y')
        self.log.pack(side='left', fill='both', expand=True)
        self.log.configure(yscrollcommand=sb.set)
        for tag, color in (('sys', COL_MUTED), ('ok', COL_GREEN),
                           ('warn', COL_AMBER), ('error', COL_RED),
                           ('muted', COL_FAINT), ('head', COL_ACCENT_D)):
            self.log.tag_configure(tag, foreground=color)
        # 告警/错误整行加底色，扫一眼就能定位问题行，不必逐行读文字
        self.log.tag_configure('warn', background=SEM['off'][1])
        self.log.tag_configure('error', background=SEM['err'][1])
        self.log.tag_configure('head', font=(F_MONO, T_MONO_SIZE, 'bold'))

        # ---- 底部状态条 ----
        # 先把右侧按钮 pack 掉再放文字：pack 按顺序分配空间，
        # 否则一行过长的路径会把按钮挤出窗口。
        bar = tk.Frame(outer, bg=COL_CANVAS)
        bar.pack(fill='x', pady=(u(SP3), 0))
        FlatButton(bar, '设置', icon='gear', variant='default',
                   command=lambda: SettingsDialog(self), **small).pack(
            side='right')
        self.btn_refresh = FlatButton(bar, '刷新', icon='refresh',
                                      variant='default',
                                      command=lambda: self._do_status(force=True),
                                      **small)
        self.btn_refresh.pack(side='right', padx=(0, u(SP2)))
        self.status_line = tk.Label(bar, text='', bg=COL_CANVAS, fg=COL_FAINT,
                                    font=(F_UI, T_MICRO), anchor='w')
        self.status_line.pack(side='left', fill='x', expand=True)

        self.root.update_idletasks()
        # 窗口尺寸 / 位置统一交给 place_window：它会按可用区（扣掉任务栏）
        # 把外框收进屏幕，再用 Win32 把位置钉死。
        # 基准取 820x800 而不是更高：本机 200% 缩放下可用区只有 852 逻辑像素高，
        # 定得再高就只能靠裁剪，用户看到的永远是「被砍过的界面」。
        place_window(self.root, int(820 * self.scale), int(800 * self.scale))
        wa = work_area()
        # minsize 不能超过可用区，否则小屏上「最小尺寸」会把窗口重新顶出去
        self.root.minsize(
            min(int(760 * self.scale), wa[2] - wa[0]),
            min(int(660 * self.scale), wa[3] - wa[1]))
        self.root.bind('<F5>', lambda e: self._do_status(force=True))
        self.say('界面已就绪。本机日志：%s' % APP_LOG_FILE, 'muted')

    # ---------- 日志 ----------
    # 级别记号集中在这里加：只用颜色区分级别对色觉障碍用户不友好（WCAG 1.4.1）
    MARK = {'ok': '√ ', 'error': '× ', 'warn': '! '}

    def say(self, text, tag='sys'):
        self.log.configure(state='normal')
        self.log.insert('end', '[%s] ' % time.strftime('%H:%M:%S'), 'muted')
        self.log.insert('end', self.MARK.get(tag, '') + str(text).rstrip() + '\n',
                        tag)
        self.log.see('end')
        self.log.configure(state='disabled')

    def say_remote(self, lines):
        for ln in lines:
            low = ' ' + ln.lower() + ' '
            tag = 'sys'
            if ' error ' in low or '失败' in ln or 'fail' in low:
                tag = 'error'
            elif ' warn ' in low or '不可达' in ln or '冷静期' in ln:
                tag = 'warn'
            elif '成功' in ln or 'online' in low or '已在网络' in ln:
                tag = 'ok'
            self.log.configure(state='normal')
            self.log.insert('end', '  %s\n' % ln, tag)
            self.log.see('end')
            self.log.configure(state='disabled')

    def _drain_logq(self):
        try:
            while True:
                text, tag = self.logq.get_nowait()
                self.say(text, tag)
        except queue.Empty:
            pass
        self.root.after(120, self._drain_logq)

    def log_line(self, text, tag='sys'):
        self.logq.put((text, tag))

    def toggle_pause(self):
        self.paused = not self.paused
        self.cfg['log_pause'] = self.paused
        save_config(self.cfg)
        self.btn_pause.set_text('继续' if self.paused else '暂停')
        self.btn_pause.set_icon('play' if self.paused else 'pause')
        self.say('日志刷新已%s' % ('暂停' if self.paused else '继续'))

    def clear_log(self):
        self.log.configure(state='normal')
        self.log.delete('1.0', 'end')
        self.log.configure(state='disabled')

    def export_log(self):
        path = filedialog.asksaveasfilename(
            title='导出日志', defaultextension='.txt',
            initialfile='campus_console_%s.txt' % time.strftime('%Y%m%d_%H%M'),
            filetypes=[('文本文件', '*.txt')])
        if not path:
            return
        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(self.log.get('1.0', 'end'))
            self.say('日志已导出：%s' % path, 'ok')
        except Exception as e:
            messagebox.showerror(APP_NAME, '导出失败：%s' % e)

    # ---------- 定时器 ----------
    def _tick_status(self):
        self.root.after(20000, self._tick_status)
        if not self.runner.busy:
            self._do_status()

    def _tick_log(self):
        self.root.after(15000, self._tick_log)
        if self.paused or self.runner.busy or self.ops is None:
            return
        pw = self.router_password()
        if not pw:
            return
        ops = self.ops
        self.runner.submit('log', lambda: ops.tail_log(pw, 200), self._log_done,
                           interactive=False)

    def _log_done(self, name, ok, payload):
        if ok and payload:
            lines = [l for l in payload.splitlines() if l.strip()]
            self._append_remote_log(lines)

    def _do_status(self, force=False):
        if self.runner.busy:
            return
        pw = self.router_password()
        if not pw:
            self.render_no_password()
            return
        if self.ops is None:
            self.ops = RouterOps(self.cfg, log=self.log_line)
        ops = self.ops
        self.set_busy(True)
        self.runner.submit('status', lambda: ops.status(pw), self._status_done)

    def _status_done(self, name, ok, payload):
        self.set_busy(False)
        if ok:
            self.status = payload
            self.render_status(payload)
        else:
            self.ops.close()
            self.ops = None
            self.render_error(str(payload))

    # ---------- 渲染 ----------
    def set_busy(self, busy):
        self.switch.set_enabled(not busy)
        self.btn_login.set_state('disabled' if busy else 'normal')
        if busy:
            self.status_line.config(text='正在与路由器通信…', fg=COL_MUTED)
        else:
            self.render_status_line()

    def _hero(self, kind, title, desc='', hint='', chip=None, hint_kind=None):
        """状态主卡的统一入口。四种状态本来把这 5 行重复了四遍，
        抽出来既少犯错，也保证「状态色 → 竖条 / 标题 / 徽章 / 药丸」始终同步。"""
        fg, _bg, bd = SEM.get(kind, SEM['idle'])
        self.hero.set_rail(bd)
        self.hero_title.config(text=title, fg=fg)
        self.hero_desc.config(text=desc)
        self.hero_hint.config(text=hint,
                              fg=SEM[hint_kind][0] if hint_kind else COL_FAINT)
        ph = badge_photo(STATUS_ICON.get(kind, 'dot_circle'), u(46), fg,
                         '#ffffff')
        if ph is not None and isinstance(self.hero_badge, tk.Label):
            self.hero_badge.config(image=ph)
            self.hero_badge._icon_ref = ph
        if chip is not None:
            self.chip_header.set(chip[0], chip[1])

    def render_no_password(self):
        self._hero('idle', '还没配置路由器',
                   '点右下角「设置」，填入路由器地址和登录密码即可开始使用。',
                   '不知道路由器密码？看路由器背面贴纸，'
                   '或用浏览器打开 miwifi.com（小米默认 %s）。'
                   % DEFAULT_ROUTER_HOST,
                   chip=('未配置', 'idle'))
        self.switch.set(None)
        self._tiles_reset()
        self.render_status_line()

    def _tiles_reset(self):
        """把四个概览格清回「未知」。

        必须做：这些格子是「上一次读到的状态」的缓存，而 render_error / 未配置
        这两条路径读不到任何状态。不清的话，界面会出现
        「连不上路由器」+「登录程序 就绪」+「连接方式 已连接」这种自相矛盾的组合 ——
        截图上真出现过。宁可显示「—」，也不要显示过期的断言。
        """
        for t in (self.tile_cron, self.tile_script, self.tile_crond,
                  self.tile_link):
            t.set('—', 'idle')

    def render_error(self, msg):
        # 提示里报「现在填的地址」而不是常量默认值：预置之后这两者不是一个值
        # （实测地址 172.17.113.25 vs 小米出厂默认 192.168.31.1），报错时前者才有用。
        cur_host = self.cfg.get('host') or DEFAULT_ROUTER_HOST
        self._hero('err', '连不上路由器',
                   (msg or '').splitlines()[0] if msg else '',
                   '检查：① 电脑是否连着这台路由器的 WiFi　'
                   '② 地址是否正确（现在填的是 %s，小米出厂默认 %s）'
                   % (cur_host, DEFAULT_ROUTER_HOST),
                   chip=('连不上', 'err'))
        self.switch.set(None)
        self._tiles_reset()
        self.tile_link.set('未连接', 'err')   # 唯一确定的一条：连不上
        self.say('%s' % msg, 'error')
        self.render_status_line()

    def render_status(self, st):
        if not st.get('deployed'):
            self._hero('off', '路由器上还没装程序',
                       '点「设置」→「部署 / 更新到路由器」即可装好。',
                       '需要填校园网账号密码（会加密保存到路由器里）。',
                       chip=('未部署', 'off'))
            self.switch.set(None)
            self.tile_cron.set('缺失', 'err')
            self.tile_script.set('缺失', 'err')
            self.tile_crond.set(st.get('crond') or '—', 'idle')
            self.tile_link.set(self.link_text(), 'ok')
            self.render_status_line()
            return

        self.tile_link.set(self.link_text(), 'ok')
        self.tile_script.set('就绪' if st['script_ok'] else '缺失',
                             'ok' if st['script_ok'] else 'err')
        if st['cron_line']:
            self.tile_cron.set('已就绪' if st['cron_gated'] else '旧版本',
                               'ok' if st['cron_gated'] else 'off')
        else:
            self.tile_cron.set('缺失', 'err')
        self.tile_crond.set(st['crond'] or '未知',
                            'ok' if st['crond'] == '运行中' else 'err')

        if st['switch_on']:
            # 开启时间只放在底部状态条里，不塞进正文：塞进去会让这段说明多折一行，
            # 末尾孤零零剩个「）」很难看
            self._hero('ok', '自动登录已开启',
                       '路由器会自动保持在线，宿舍 WiFi 随时可用。',
                       '提醒：学校限制一个账号只能一台设备在线。'
                       '离开宿舍前请切到「关闭自动登录」，否则在教室会被顶掉。',
                       chip=('自动登录中', 'ok'))
            self.switch.set(True)
        else:
            self._hero('off', '自动登录已关闭',
                       '路由器不会自动登录，你在教室 / 图书馆可以放心用校园网。',
                       '回宿舍想上网时切到「开启自动登录」，路由器会立刻登录一次。',
                       chip=('已关闭', 'off'))
            self.switch.set(False)

        # 提示行按严重程度二选一，不堆叠：
        # 1) crond 死了 = 自动登录根本不会触发，与头部绿色「自动登录中」直接矛盾，
        #    必须盖过其它提示（截图上出现过「绿药丸 + 定时服务未运行」的组合）；
        # 2) 旧版 cron 行 = 开关判定不生效，需要重新部署。
        if st['switch_on'] and st['crond'] and st['crond'] != '运行中':
            self.hero_hint.config(
                text='定时服务（crond）没有运行，自动登录不会生效 —— '
                     '请到「设置」重新部署一次，或重启路由器。', fg=SEM['err'][0])
        elif st['cron_stale'] or (st['cron_line'] and not st['cron_gated']):
            self.hero_hint.config(
                text='检测到旧版本留下的定时任务（不含开关判定），开关不会生效 —— '
                     '请到「设置」重新部署一次。', fg=SEM['off'][0])
        if st.get('schedule'):
            self.var_off.set(st['schedule'][0])
            self.var_on.set(st['schedule'][1])
            self.var_sched_on.set(True)
            self._sched_toggle()
            self.sched_hint.config(text='路由器上已生效')
        elif self.var_sched_on.get():
            self.sched_hint.config(text='路由器上未生效（点右侧保存）')
        else:
            self.sched_hint.config(text='未启用')

        self._append_remote_log(st.get('log_lines') or [])
        self.render_status_line()

    def render_status_line(self):
        st = self.status or {}
        bits = ['路由器 %s' % self.cfg['host']]
        if st.get('since'):
            bits.append('最近开启 %s' % st['since'])
        bits.append('设置文件 %s' % os.path.basename(CONFIG_FILE))
        self.status_line.config(text='　·　'.join(bits), fg=COL_FAINT)

    def _append_remote_log(self, lines):
        if not lines:
            return
        if not self.last_log_tail:
            self.say('— 路由器日志（最近 %d 行）—' % len(lines), 'muted')
            self.say_remote(lines)
            self.last_log_tail = lines[-1]
            return
        if self.last_log_tail in lines:
            idx = len(lines) - 1 - lines[::-1].index(self.last_log_tail)
            new = lines[idx + 1:]
        else:
            new = lines
        if new:
            self.say_remote(new)
            self.last_log_tail = new[-1]

    # ---------- 操作 ----------
    def need_password(self):
        if self.router_password():
            return True
        messagebox.showinfo(APP_NAME, '还没有填写路由器登录密码，先到「设置」里填一下。')
        SettingsDialog(self)
        return False

    def with_ops(self, fn, on_ok=None, interactive=True):
        if not self.need_password():
            return
        if self.ops is None:
            self.ops = RouterOps(self.cfg, log=self.log_line)
        ops = self.ops

        def done(name, ok, payload):
            self.set_busy(False)
            if ok:
                self._save_fingerprint()
                if on_ok:
                    on_ok(payload)
                self._do_status()
            else:
                ops.close()
                self.ops = None
                self.say(payload, 'error')
                if interactive:
                    messagebox.showerror(APP_NAME, payload)

        self.runner.submit('op', lambda: fn(ops), done, interactive=interactive)

    def _save_fingerprint(self):
        fp = getattr(self.ops.be, 'new_fingerprint', '') if self.ops else ''
        if fp and fp != self.cfg.get('host_fingerprint'):
            self.cfg['host_fingerprint'] = fp
            save_config(self.cfg)

    def set_switch(self, on):
        if not self.need_password():
            return
        body = ('开启后路由器会自动保持在线，宿舍 WiFi 随时可用。\n\n'
                '如果你现在人在教室/外面，请不要开启 —— 会和你抢账号。' if on else
                '关闭后路由器不再自动登录，你在外面可以放心用校园网。\n\n'
                '回宿舍时点「开启自动登录」即可恢复。')
        if not messagebox.askyesno(APP_NAME, '确认%s自动登录？\n\n%s'
                                   % ('开启' if on else '关闭', body)):
            return
        self.say('正在%s自动登录…' % ('开启' if on else '关闭'))

        def job(ops):
            return ops.set_switch(self.router_password(), on)

        def ok(out):
            self.say('已%s自动登录' % ('开启' if on else '关闭'), 'ok')
            if out:
                self.say_remote([l for l in out.splitlines() if l.strip()])

        self.with_ops(job, ok)

    def login_now(self):
        if not self.need_password():
            return
        st = self.status or {}
        if st.get('switch_on') is False:
            if not messagebox.askyesno(
                    APP_NAME, '总开关现在是关闭状态。\n'
                              '直接登录一次会让路由器短暂上线（之后不会再抢），继续吗？'):
                return
        self.say('正在登录一次…')

        def job(ops):
            return ops.login_now(self.router_password())

        def ok(res):
            rc, out = res
            good = 'EXIT=0' in out
            self.say('立即登录：%s' % ('成功 / 本来就在线' if good else '本次没成功'),
                     'ok' if good else 'warn')
            lines = [l for l in out.splitlines() if l.strip()]
            if lines:
                self.say_remote(lines[-4:])

        self.with_ops(job, ok)

    def _sched_toggle(self):
        state = 'normal' if self.var_sched_on.get() else 'disabled'
        self.e_off.set_state(state)
        self.e_on.set_state(state)

    def save_schedule(self):
        if not self.need_password():
            return
        enabled = bool(self.var_sched_on.get())
        off, on = self.var_off.get().strip(), self.var_on.get().strip()
        if enabled:
            if parse_hhmm(off) is None or parse_hhmm(on) is None:
                messagebox.showwarning(APP_NAME, '时间格式应为 HH:MM，例如 08:00。')
                return
            if parse_hhmm(off) == parse_hhmm(on):
                messagebox.showwarning(APP_NAME, '关闭时间和开启时间不能相同。')
                return

        def job(ops):
            return ops.set_schedule(self.router_password(), enabled, off, on)

        def ok(out):
            self.cfg['sched_enabled'] = enabled
            self.cfg['sched_off'] = off
            self.cfg['sched_on'] = on
            save_config(self.cfg)
            self.sched_hint.config(text='路由器上已生效' if enabled else '未启用')
            self.say('自动时段已更新：' + ('每天 %s 关闭、%s 开启' % (off, on)
                                           if enabled else '已停用'), 'ok')

        self.with_ops(job, ok)

    def install_paramiko(self):
        self.say('开始安装 SSH 组件（paramiko）…')

        def job():
            return RouterOps(self.cfg).install_paramiko(self.log_line)

        def done(name, ok, payload):
            self.set_busy(False)
            if ok:
                self.has_paramiko = self._check_paramiko()
                if self.has_paramiko:
                    try:
                        self.banner.pack_forget()
                    except Exception:
                        pass
                    self.say('paramiko 安装成功，可以连接路由器了。', 'ok')
                    self._do_status()
                else:
                    self.say('安装命令已执行，但当前进程还导入不到 paramiko。'
                             '请关掉本程序重新打开。', 'warn')
            else:
                self.say(payload, 'error')
                messagebox.showerror(APP_NAME, str(payload))

        self.set_busy(True)
        self.runner.submit('pip', job, done)

    # ---------- 部署 ----------
    def run_deploy(self, cuser, cpw, payload, enable):
        if not self.need_password():
            return
        self.say('===== 开始部署 =====')
        self.set_busy(True)

        def job():
            pw = self.router_password()
            ops = RouterOps(self.cfg, log=self.log_line)
            ops.connect(pw)
            self.log_line('程序文件：%s' % payload)
            ops.deploy(pw, cuser, cpw, payload, enable,
                       lambda m: self.log_line('· ' + m))
            return ops.status(pw)

        def done(name, ok, res):
            self.set_busy(False)
            if ok:
                self._save_fingerprint()
                self.say('===== 部署完成 =====', 'ok')
                self.status = res
                self.render_status(res)
                messagebox.showinfo(
                    APP_NAME, '部署完成。\n\n' + (
                        '总开关：已开启 —— 宿舍 WiFi 现在可用。' if enable else
                        '总开关：已关闭 —— 你在教室用校园网不会被打扰，'
                        '回宿舍点「开启自动登录」即可。'))
            else:
                self.say('部署失败：%s' % res, 'error')
                messagebox.showerror(APP_NAME, str(res))

        self.runner.submit('deploy', job, done)

    # ---------- 托盘 ----------
    def setup_tray(self):
        try:
            import pystray
        except Exception:
            self.say('未安装 pystray/Pillow，跳过托盘常驻（不影响其他功能）。', 'muted')
            return
        # 托盘图标与窗口图标、界面里的品牌标记同源，避免三处各画一个
        img = brand_image(64)
        if img is None:
            self.say('缺少 Pillow，跳过托盘常驻（不影响其他功能）。', 'muted')
            return
        menu = pystray.Menu(
            pystray.MenuItem('显示主界面', self._tray_show, default=True),
            pystray.MenuItem('开启自动登录', lambda *a: self._tray_act(True)),
            pystray.MenuItem('关闭自动登录', lambda *a: self._tray_act(False)),
            pystray.MenuItem('立即登录一次', lambda *a: self.root.after(0, self.login_now)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem('退出', self._tray_quit),
        )
        try:
            self.icon = pystray.Icon('CampusNetConsole', img, APP_NAME, menu)
            self.icon.run_detached()
        except Exception as e:
            app_log('托盘启动失败: %s' % e)
            self.icon = None

    def _tray_show(self, *a):
        self.root.after(0, self.show_window)

    def _tray_act(self, on):
        self.root.after(0, lambda: self.set_switch(on))

    def _tray_quit(self, *a):
        self.root.after(0, self.quit)

    def show_window(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def on_close(self):
        if self.icon is not None and self.cfg.get('minimize_to_tray', True):
            self.root.withdraw()
            try:
                self.icon.notify('已最小化到托盘，后台仍在运行', APP_NAME)
            except Exception:
                pass
        else:
            self.quit()

    def quit(self):
        if self._quitting:
            return
        self._quitting = True
        try:
            if self.ops:
                self.ops.close()
        except Exception:
            pass
        if self.icon is not None:
            try:
                self.icon.stop()
            except Exception:
                pass
        try:
            self.root.destroy()
        except Exception:
            pass


# ---------------------------------------------------------------- 设置窗口

class SettingsDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title('设置 · ' + APP_NAME)
        self.configure(bg=COL_CANVAS)
        self.transient(app.root)
        self.resizable(False, False)
        self.protocol('WM_DELETE_WINDOW', self.destroy)

        cfg = app.cfg
        small = {'padx': u(SP3), 'pady': u(1) + 2}

        # 标题栏：和主窗口同一套语言（图标 + 标题 + 白色发丝线）
        head = tk.Frame(self, bg=COL_CARD)
        head.pack(fill='x')
        hin = tk.Frame(head, bg=COL_CARD)
        hin.pack(fill='x', padx=u(SP5), pady=u(SP3))
        icon_label(hin, 'gear', u(18), COL_ACCENT, COL_CARD).pack(
            side='left', padx=(0, u(SP2)))
        tk.Label(hin, text='设置', bg=COL_CARD, fg=COL_TEXT,
                 font=(F_UI, T_H3, 'bold')).pack(side='left')
        tk.Label(hin, text='所有内容只保存在本机', bg=COL_CARD, fg=COL_FAINT,
                 font=(F_UI, T_CAPTION)).pack(side='left', padx=(u(SP2), 0))
        tk.Frame(self, bg=COL_BORDER, height=1).pack(fill='x')

        pad = tk.Frame(self, bg=COL_CANVAS)
        pad.pack(fill='both', expand=True, padx=u(SP5), pady=u(SP4))

        # ---- 路由器连接 ----
        c1 = Card(pad)
        c1.pack(fill='x')
        SectionHead(c1.body, '路由器连接', 'plug',
                    'SSH 登录信息 · 已预填，可改').pack(fill='x')
        g = self._form(c1.body)
        self.v_host = tk.StringVar(value=cfg.get('host', ''))
        self.v_port = tk.StringVar(value=str(cfg.get('port', 22)))
        self.v_user = tk.StringVar(value=cfg.get('user', 'root'))
        self.v_pw = tk.StringVar(value=app.router_password())
        self._flabel(g, '地址', 0, 0)
        Field(g, self.v_host, width=18).grid(row=0, column=1, sticky='we')
        self._flabel(g, '端口', 0, 2)
        Field(g, self.v_port, width=8).grid(row=0, column=3, sticky='we')
        self._flabel(g, '用户', 1, 0)
        Field(g, self.v_user, width=18).grid(
            row=1, column=1, sticky='we', pady=(u(SP2), 0))
        self._flabel(g, '密码', 1, 2)
        self.f_pw = Field(g, self.v_pw, width=18, show='●')
        self.f_pw.grid(row=1, column=3, sticky='we', pady=(u(SP2), 0))
        self.v_show = tk.BooleanVar(value=False)
        CheckBox(g, '显示', variable=self.v_show,
                 command=lambda: self.f_pw.set_show(
                     '' if self.v_show.get() else '●')).grid(
            row=1, column=4, sticky='w', padx=(u(SP3), 0), pady=(u(SP2), 0))
        self._note(c1.body, '密码用 Windows DPAPI 加密存在本机，只有你的账户能解开；'
                            '小米路由器 SSH 账号是 root（不是后台的 Xiaomi_xxxx）。')
        brow = tk.Frame(c1.body, bg=COL_CARD)
        brow.pack(fill='x', pady=(u(SP3), 0))
        FlatButton(brow, '测试连接', icon='bolt', variant='default',
                   command=self.test_conn).pack(side='left')
        self.conn_hint = tk.Label(brow, text='', bg=COL_CARD, fg=COL_MUTED,
                                  font=(F_UI, T_CAPTION))
        self.conn_hint.pack(side='left', padx=(u(SP3), 0))

        # ---- 部署 ----
        c2 = Card(pad)
        c2.pack(fill='x', pady=(u(SP3), 0))
        SectionHead(c2.body, '部署 / 更新到路由器', 'upload',
                    '上传程序 · 迁移定时任务 · 加密保存账号密码').pack(fill='x')
        g2 = self._form(c2.body)
        self.v_cuser = tk.StringVar(value=cfg.get('campus_user', ''))
        self.v_cpw = tk.StringVar(value=app.campus_password())
        self._flabel(g2, '校园网账号', 0, 0)
        Field(g2, self.v_cuser, width=18).grid(row=0, column=1, sticky='we')
        self._flabel(g2, '密码', 0, 2)
        self.f_cpw = Field(g2, self.v_cpw, width=18, show='●')
        self.f_cpw.grid(row=0, column=3, sticky='we')
        self.v_show2 = tk.BooleanVar(value=False)
        CheckBox(g2, '显示', variable=self.v_show2,
                 command=lambda: self.f_cpw.set_show(
                     '' if self.v_show2.get() else '●')).grid(
            row=0, column=4, sticky='w', padx=(u(SP3), 0))
        self._flabel(g2, '程序文件', 1, 0)
        self.v_payload = tk.StringVar(value=cfg.get('payload_dir', '')
                                      or auto_payload_dir())
        # 单独占满一行：原来挤在 4 列里，长路径直接被裁掉一半
        self.f_payload = Field(g2, self.v_payload, width=40)
        self.f_payload.grid(
            row=1, column=1, columnspan=3, sticky='we', pady=(u(SP2), 0))
        # 路径左边永远是没信息量的前缀，滚到最右端让文件夹名露出来
        self.f_payload.see_end()
        FlatButton(g2, '浏览…', icon='folder', variant='default',
                   command=self.pick_payload, **small).grid(
            row=1, column=4, sticky='w', padx=(u(SP3), 0), pady=(u(SP2), 0))
        self.v_enable = tk.BooleanVar(value=True)
        CheckBox(c2.body, '部署完成后立即启用自动登录（人在宿舍选这个）',
                 variable=self.v_enable).pack(anchor='w', pady=(u(SP3), 0))
        brow2 = tk.Frame(c2.body, bg=COL_CARD)
        brow2.pack(fill='x', pady=(u(SP3), 0))
        FlatButton(brow2, '部署 / 更新', icon='upload', variant='primary',
                   bold=True, command=self.deploy).pack(side='left')
        FlatButton(brow2, '清空已保存的密码', icon='key', variant='default',
                   command=self.clear_secrets).pack(side='left', padx=(u(SP2), 0))
        self._note(c2.body, '重跑部署会重新加密保存账号密码（旧密钥作废），'
                            '不影响其他设置。')

        # ---- 启动与常驻 ----
        c3 = Card(pad)
        c3.pack(fill='x', pady=(u(SP3), 0))
        SectionHead(c3.body, '启动与常驻', 'power').pack(fill='x')
        self.v_auto = tk.BooleanVar(value=bool(cfg.get('autostart')))
        CheckBox(c3.body, '开机自动启动（登录 Windows 后缩到托盘运行）',
                 variable=self.v_auto, command=self.toggle_autostart).pack(
            anchor='w', pady=(u(SP3), u(SP1)))
        self.v_tray = tk.BooleanVar(value=bool(cfg.get('minimize_to_tray', True)))
        CheckBox(c3.body, '关闭窗口时最小化到托盘（不退出）',
                 variable=self.v_tray).pack(anchor='w', pady=u(SP1))
        self.auto_hint = tk.Label(
            c3.body, text='当前：' + ('已开启' if autostart_enabled() else '未开启'),
            bg=COL_CARD, fg=COL_FAINT, font=(F_UI, T_MICRO))
        self.auto_hint.pack(anchor='w', padx=u(SP6), pady=(u(SP1), 0))

        # ---- 底栏 ----
        tk.Frame(self, bg=COL_BORDER, height=1).pack(fill='x')
        bottom = tk.Frame(self, bg=COL_CANVAS)
        bottom.pack(fill='x', padx=u(SP5), pady=u(SP3))
        icon_label(bottom, 'plug', u(14), COL_FAINT, COL_CANVAS).pack(
            side='left', padx=(0, u(SP1) + 1))
        tk.Label(bottom, text='SSH 后端：%s' % (
            'paramiko（内置）' if app.has_paramiko else
            ('plink（PuTTY）' if app.putty[0] else '无 —— 请先安装')),
            bg=COL_CANVAS, fg=COL_FAINT, font=(F_UI, T_CAPTION)).pack(side='left')
        FlatButton(bottom, '保存', icon='check', variant='primary', bold=True,
                   command=self.save).pack(side='right')
        FlatButton(bottom, '关闭', variant='default',
                   command=self.destroy).pack(side='right', padx=(0, u(SP2)))

        self.bind('<Escape>', lambda e: self.destroy())
        self.bind('<Return>', lambda e: self.save())
        self.update_idletasks()
        # 盖在主窗口正中，并保证整框留在可用区里 ——
        # 位置交给窗口管理器的话，主窗口自己偏下时这个框会被一起带出屏幕，
        # 底部「保存 / 关闭」就点不到了。
        place_window(self, max(self.winfo_reqwidth(), int(680 * app.scale)),
                     self.winfo_reqheight(), over=app.root)
        try:
            self.grab_set()
        except Exception:
            pass

    # ---- 表单小工具：标签用固定像素宽，保证每张卡片的输入框左边缘对齐 ----
    def _form(self, parent):
        g = tk.Frame(parent, bg=COL_CARD)
        g.pack(fill='x', pady=(u(SP3), 0))
        g.columnconfigure(1, weight=1)
        g.columnconfigure(3, weight=1)
        return g

    def _flabel(self, parent, text, r, c):
        box = tk.Frame(parent, bg=COL_CARD, width=u(92), height=u(20))
        box.grid(row=r, column=c, sticky='e',
                 padx=(0, u(SP2)) if c == 0 else (u(SP4), u(SP2)))
        box.grid_propagate(False)
        tk.Label(box, text=text, bg=COL_CARD, fg=COL_MUTED, font=(F_UI, T_BODY),
                 anchor='e').pack(fill='both', expand=True)
        return box

    def _note(self, parent, text):
        lbl = tk.Label(parent, text=text, bg=COL_CARD, fg=COL_FAINT,
                       font=(F_UI, T_MICRO), anchor='w', justify='left')
        lbl.pack(fill='x', pady=(u(SP2), 0))
        return lbl

    def pick_payload(self):
        p = filedialog.askdirectory(title='选择 payload 目录（程序文件所在文件夹）',
                                   initialdir=self.v_payload.get()
                                   or os.path.expanduser('~'))
        if p:
            self.v_payload.set(p)
            self.f_payload.see_end()

    def save(self, silent=False):
        app = self.app
        old_host, old_user = app.cfg.get('host'), app.cfg.get('user')
        cfg = dict(app.cfg)
        try:
            port = int(self.v_port.get().strip() or 22)
        except ValueError:
            port = 22
        cfg.update({
            'host': self.v_host.get().strip() or DEFAULT_ROUTER_HOST,
            'port': port,
            'user': self.v_user.get().strip() or 'root',
            'campus_user': self.v_cuser.get().strip(),
            'payload_dir': self.v_payload.get().strip(),
            'autostart': bool(self.v_auto.get()),
            'minimize_to_tray': bool(self.v_tray.get()),
        })
        old_pw = app.router_password()
        if self.v_pw.get() and self.v_pw.get() != old_pw:
            enc = dpapi_encrypt(self.v_pw.get())
            if enc:
                cfg['router_password_enc'] = enc
            else:
                messagebox.showwarning(APP_NAME, '密码加密失败，未保存（其他设置已保存）。')
        if self.v_cpw.get() and self.v_cpw.get() != app.campus_password():
            cfg['campus_password_enc'] = dpapi_encrypt(self.v_cpw.get())
        if (cfg['host'], cfg['user']) != (old_host, old_user):
            cfg['host_fingerprint'] = ''
        app.cfg = cfg
        app.ops = None
        save_config(cfg)
        if not silent:
            app.say('设置已保存', 'ok')
        return True

    def test_conn(self):
        self.save(silent=True)
        app = self.app
        pw = self.v_pw.get() or app.router_password()
        if not pw:
            messagebox.showwarning(APP_NAME, '请先填写路由器密码。', parent=self)
            return
        self.conn_hint.config(text='连接中…', fg=COL_MUTED)
        self.update_idletasks()
        host = self.v_host.get().strip()
        try:
            port = int(self.v_port.get().strip() or 22)
        except ValueError:
            port = 22
        user = self.v_user.get().strip() or 'root'

        def job():
            cfg = dict(app.cfg)
            cfg.update({'host': host, 'port': port, 'user': user})
            ops = RouterOps(cfg)
            ops.connect(pw)
            rc, out = ops.be.run('echo OK; ls %s 2>/dev/null | head -6' % REMOTE_ROOT)
            res = {'out': out, 'name': ops.be.name,
                   'fp': getattr(ops.be, 'new_fingerprint', '')}
            ops.close()
            return res

        def done(name, ok, res):
            if ok:
                self.conn_hint.config(text='连接成功（%s）' % res['name'], fg=COL_GREEN)
                if res['fp']:
                    app.cfg['host_fingerprint'] = res['fp']
                    save_config(app.cfg)
                app.say('连接测试成功（%s）' % res['name'], 'ok')
                for ln in (res['out'] or '').splitlines()[1:]:
                    if ln.strip():
                        app.say_remote(['  ' + ln.strip()])
                app.ops = None
                app._do_status()
                self.destroy()
            else:
                self.conn_hint.config(text='连接失败', fg=COL_RED)
                messagebox.showerror(APP_NAME, str(res), parent=self)

        app.runner.submit('test', job, done)

    def clear_secrets(self):
        if not messagebox.askyesno(APP_NAME, '清除本机保存的路由器密码和校园网密码？\n'
                                            '（路由器里已加密保存的凭据不受影响）',
                                   parent=self):
            return
        app = self.app
        app.cfg['router_password_enc'] = ''
        app.cfg['campus_password_enc'] = ''
        save_config(app.cfg)
        self.v_pw.set('')
        self.v_cpw.set('')
        app.say('已清除本机保存的密码', 'warn')

    def deploy(self):
        self.save(silent=True)
        app = self.app
        cuser = self.v_cuser.get().strip()
        cpw = self.v_cpw.get() or app.campus_password()
        payload = self.v_payload.get().strip()
        if not cuser or not cpw:
            messagebox.showwarning(APP_NAME, '请填写校园网账号和密码。', parent=self)
            return
        if not payload or not os.path.isdir(payload):
            messagebox.showwarning(APP_NAME, '找不到 payload 目录，请点「浏览…」选择。',
                                   parent=self)
            return
        if not os.path.isfile(os.path.join(payload, 'bin', 'campus_switch.sh')):
            messagebox.showwarning(
                APP_NAME, '这个 payload 目录里没有 campus_switch.sh。\n'
                          '请选择新版压缩包里的 campus-auth-easy-deploy\\payload。',
                parent=self)
            return
        if not messagebox.askyesno(
                APP_NAME, '开始部署到路由器？\n\n会做的事：\n'
                          '· 上传程序文件\n· 用你的账号密码重新加密保存\n'
                          '· 重写定时任务（清理旧版本残留）\n'
                          '· 总开关设为「%s」' % ('开启' if self.v_enable.get() else '关闭'),
                parent=self):
            return
        enable = bool(self.v_enable.get())
        self.destroy()
        app.run_deploy(cuser, cpw, payload, enable)

    def toggle_autostart(self):
        want = bool(self.v_auto.get())
        if set_autostart(want):
            self.auto_hint.config(text='当前：' + ('已开启' if want else '未开启'))
            self.app.cfg['autostart'] = want
            save_config(self.app.cfg)
            self.app.say('开机自启已%s' % ('开启' if want else '关闭'), 'ok')
        else:
            self.v_auto.set(not want)
            messagebox.showerror(APP_NAME, '设置开机自启失败（注册表写入被拒绝？）',
                                 parent=self)


# ---------------------------------------------------------------- 自启 / 工具

def _autostart_command():
    if getattr(sys, 'frozen', False):
        return '"%s" %s' % (sys.executable, AUTOSTART_FLAG)
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), 'pythonw.exe')
    if not os.path.exists(pyw):
        pyw = exe
    return '"%s" "%s" %s' % (pyw, os.path.abspath(__file__), AUTOSTART_FLAG)


def autostart_enabled():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r'Software\Microsoft\Windows\CurrentVersion\Run') as k:
            winreg.QueryValueEx(k, AUTOSTART_KEY)
        return True
    except Exception:
        return False


def set_autostart(enable):
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r'Software\Microsoft\Windows\CurrentVersion\Run', 0,
                            winreg.KEY_SET_VALUE) as k:
            if enable:
                winreg.SetValueEx(k, AUTOSTART_KEY, 0, winreg.REG_SZ,
                                  _autostart_command())
            else:
                try:
                    winreg.DeleteValue(k, AUTOSTART_KEY)
                except FileNotFoundError:
                    pass
        return True
    except Exception as e:
        app_log('设置开机自启失败: %s' % e)
        return False


def auto_payload_dir():
    """自动定位部署用的 payload 目录。

    打包版优先用**随 exe 一起打进去**的那份 —— 这样分发给别人时只给一个 exe
    就能完成部署，不用再额外拖一个目录过去。
    """
    roots = []
    if _frozen():
        roots.append(res_dir())                      # _MEIPASS 里就是 payload 本身
        roots.append(os.path.dirname(sys.executable))  # 允许 exe 旁边另放一份覆盖
    here = os.path.dirname(os.path.abspath(__file__))
    roots += [os.path.dirname(here), here,
              os.path.join(os.path.expanduser('~'), 'Downloads')]
    pats = ['payload', '*/payload', '*/*/payload', '*/*/*/payload',
            'campus-auth-easy-deploy/payload']
    for r in roots:
        for pat in pats:
            for cand in glob.glob(os.path.join(r, pat)):
                if os.path.isfile(os.path.join(cand, 'bin', 'campus_switch.sh')):
                    return cand
    return ''


# ---------------------------------------------------------------- 自检

def selftest():
    global UI_SCALE
    ok = True
    out = []

    def check(name, cond, extra=''):
        nonlocal ok
        ok = ok and bool(cond)
        out.append('  [%s] %s %s' % ('OK' if cond else 'FAIL', name, extra))

    print('== 校园网助手 自检 ==')
    print('python     :', sys.version.split()[0])
    print('解释器     :', sys.executable)
    try:
        import paramiko
        print('paramiko   :', paramiko.__version__)
    except ImportError:
        print('paramiko   : 未安装（可在程序里一键安装）')
    plink, pscp = find_putty_tools()
    print('plink/pscp :', plink or '无', '/', pscp or '无')
    print('payload    :', auto_payload_dir() or '未找到')
    print('配置文件   :', CONFIG_FILE)
    print()

    check('时间解析 08:30', parse_hhmm('08:30') == 510)
    check('时间解析容错 8：5', parse_hhmm('8：5') == 485)
    check('非法时间', parse_hhmm('25:00') is None and parse_hhmm('abc') is None
          and parse_hhmm('') is None)
    check('时间格式化', fmt_hhmm(510) == '08:30' and fmt_hhmm(0) == '00:00')
    check('cron 时间转换', hhmm_to_cron('08:30') == ('30', '8'))

    sample = """安装目录  : /data/campus/v2
登录脚本  : 就绪
开关状态  : 【已开启】路由器会自动登录（2026-09-17 20:34:25 开启）
开关文件  : /data/campus/v2/enabled  存在

定时任务  :
  */2 7-23 * * * [ -f /data/campus/v2/enabled ] && CAMPUS_ROOT=/data/campus/v2 /data/campus/v2/bin/campus_login.sh >/dev/null 2>&1
  30 8 * * * /data/campus/v2/bin/campus_switch.sh off >/dev/null 2>&1
  0 22 * * * /data/campus/v2/bin/campus_switch.sh on >/dev/null 2>&1
crond     : 运行中

最近日志  :
  2026-09-17 20:35:24 warn 当前离线（probe=000），开始认证
  2026-09-17 20:35:26 info 认证成功，已恢复在线
"""
    st = parse_status(sample)
    check('解析 已部署', st['deployed'])
    check('解析 开关开', st['switch_on'] is True)
    check('解析 开启时间', st['since'] == '2026-09-17 20:34:25')
    check('解析 脚本就绪', st['script_ok'])
    check('解析 crond', st['crond'] == '运行中')
    check('解析 cron 含开关判定', st['cron_gated'] and 'enabled' in st['cron_line'])
    check('解析 自动时段', st['schedule'] == ('08:30', '22:00'), str(st['schedule']))
    check('解析 日志两行', len(st['log_lines']) == 2, str(st['log_lines']))

    check('未部署识别', parse_status('sh: campus_switch.sh: not found')['deployed'] is False)
    st3 = parse_status(sample.replace('【已开启】', '【已关闭】').replace(
        '  */2 7-23 * * * [ -f /data/campus/v2/enabled ] && CAMPUS_ROOT=/data/campus/v2 '
        '/data/campus/v2/bin/campus_login.sh >/dev/null 2>&1',
        '  */2 7-23 * * * CAMPUS_ROOT=/data/campus/v2 '
        '/data/campus/v2/bin/campus_login.sh'))
    check('关闭状态识别', st3['switch_on'] is False)
    check('旧版 cron 报警', st3['cron_gated'] is False and st3['cron_line'] != '')
    check('无时段时不误报', parse_status('crond     : 运行中')['schedule'] is None)

    s_on = schedule_script(True, '08:30', '22:00')
    on_lines = [l for l in s_on.splitlines() if l.strip().startswith('printf') and '*' in l]
    check('时段脚本 两条 cron', len(on_lines) == 2 and "30 8 * * *" in s_on
          and "0 22 * * *" in s_on)
    check('时段脚本 带标记', SCHED_MARK in s_on and 'SCHED_OK' in s_on)
    s_off = schedule_script(False, '', '')
    off_lines = [l for l in s_off.splitlines() if l.strip().startswith('printf')
                 and '*' in l]
    check('停用时无时段行', not off_lines and 'SCHED_OK' in s_off)
    cron = CRON_SCRIPT.format(LINE=CRON_LINE, TABS=' '.join(CRONTABS))
    check('主任务脚本 可格式化', 'CRON_OK' in cron and '{LINE}' not in cron
          and '{TABS}' not in cron)
    check('主任务脚本 含迁移', "grep -v 'campus_login\\.sh'" in cron)
    check('主任务脚本 带开关判定', 'enabled' in CRON_LINE and '{LINE}' not in cron)
    check('权限脚本 可格式化', 'PERM_OK' in PERM_SCRIPT.format(R=REMOTE_ROOT))
    check('开关脚本 两种形态', 'FLAG_OK' in FLAG_SCRIPT.format(R=REMOTE_ROOT, ON='1')
          and 'FLAG_OK' in FLAG_SCRIPT.format(R=REMOTE_ROOT, ON='0'))

    if sys.platform == 'win32':
        token = dpapi_encrypt('测试密码-abc123')
        check('DPAPI 加密非空', bool(token))
        check('DPAPI 往返一致', dpapi_decrypt(token) == '测试密码-abc123')
        check('DPAPI 密文不含明文', 'abc123' not in (token or ''))
        check('DPAPI 空值安全', dpapi_encrypt('') == '' and dpapi_decrypt('') == '')
        check('DPAPI 坏数据不崩', dpapi_decrypt('!!!bad!!!') == '')
        check('单实例接口可用', callable(acquire_single_instance))
        check('DPI 可读', system_dpi() >= 96, 'dpi=%d' % system_dpi())

    # ---- 设计令牌：对比度必须达到 WCAG 2.1 AA（正文 4.5:1）----
    for label, fg, bg in (
            ('正文 / 卡片', COL_TEXT, COL_CARD),
            ('次级文字 / 卡片', COL_MUTED, COL_CARD),
            ('辅助文字 / 卡片', COL_FAINT, COL_CARD),
            ('次级文字 / 页面底', COL_MUTED, COL_CANVAS),
            ('辅助文字 / 页面底', COL_FAINT, COL_CANVAS),
            ('主按钮文字 / 主色', '#ffffff', COL_ACCENT),
            ('强调文字 / 卡片', COL_ACCENT_D, COL_CARD),
            ('日志正文 / 日志底', COL_MUTED, COL_LOG_BG),
            ('辅助文字 / 日志底', COL_FAINT, COL_LOG_BG)):
        r = contrast_ratio(fg, bg)
        check('对比度 %s' % label, r >= 4.5, '%.2f:1' % r)
    for kind in ('ok', 'off', 'err', 'info', 'idle'):
        sf, sb, _bd = SEM[kind]
        r = contrast_ratio(sf, sb)
        check('对比度 语义色 %s' % kind, r >= 4.5, '%.2f:1' % r)

    keep = UI_SCALE
    UI_SCALE = 2.0
    check('间距随 DPI 缩放', u(8) == 16 and u(3) == 6, 'u(8)=%d' % u(8))
    UI_SCALE = keep

    if _pil() is not None:
        miss = [n for n in ICON_NAMES if _icon_image(n, 20, COL_MUTED) is None]
        check('图标全部可渲染', not miss, str(miss))
        b = badge_image('check_circle', 46, COL_GREEN)
        check('徽章渲染', b is not None and b.size == (46, 46),
              str(b.size if b else None))
        br = brand_image(64)
        check('品牌图标渲染', br is not None and br.size == (64, 64))
        check('图标名拼写正确', not _ICON_UNKNOWN, str(sorted(_ICON_UNKNOWN)))
        ico = os.path.join(tempfile.gettempdir(), '_campus_test.ico')
        check('生成多尺寸 ico', make_ico(ico) and os.path.getsize(ico) > 2000,
              '%d bytes' % (os.path.getsize(ico) if os.path.exists(ico) else 0))
        try:
            os.remove(ico)
        except OSError:
            pass

    # 任务栏身份：漏了这一步，窗口图标再对，任务栏上也是 Python 的图标
    ident = current_app_identity()
    check('任务栏身份已声明', ident == APP_AUMID,
          ident or '未设置（任务栏会显示 pythonw.exe 的图标）')

    # 预置信息：灌入 / 幂等 / 不引入未知键。全程用内存 dict，不碰磁盘上的真实配置。
    p = dict(DEFAULT_CONFIG)
    check('预置 灌入连接信息', apply_preset(p)
          and p['host'] == PRESET['host'] and p['user'] == PRESET['user']
          and p['campus_user'] == PRESET['campus_user'])
    check('预置 密码已加密可解回',
          dpapi_decrypt(p['router_password_enc']) == PRESET['router_password']
          and dpapi_decrypt(p['campus_password_enc']) == PRESET['campus_password'],
          '解不出明文' if not p['router_password_enc'] else '')
    check('预置 幂等（迁移过就不再改）', apply_preset(dict(p)) is False)
    q = {}
    apply_preset(q)
    check('预置 不引入未知键', set(q) <= set(DEFAULT_CONFIG),
          str(sorted(set(q) - set(DEFAULT_CONFIG))))

    # 上传通道回退：小米路由器开 SSH 后跑的是 XMiR-SSH（dropbearmulti），**没编译
    # sftp 子系统**，远端也没有 scp —— open_sftp() 直接 EOF during negotiation。
    # 不退回 base64 通道的话，部署会在第一个文件上就断掉（真踩过）。
    class _NoSftp:
        def open_sftp(self):
            raise IOError('EOF during negotiation')

    raw = b'#!/bin/sh\necho \xe4\xb8\xad\xe6\x96\x87\n' * 4
    p_local = os.path.join(tempfile.gettempdir(), '_cc_up_test.bin')
    with open(p_local, 'wb') as fh:
        fh.write(raw)
    got = {'cmd': '', 'b64': b'', 'wc': ''}

    def _rs(cmd, data, timeout=40):
        got['cmd'], got['b64'] = cmd, data
        return 0, 'B64_OK'

    def _r(cmd, timeout=25):
        got['wc'] = cmd
        return 0, str(len(raw))

    be = ParamikoBackend.__new__(ParamikoBackend)
    be.sftp_ok, be.xfer_note, be.client = None, '', _NoSftp()
    be.run_stdin, be.run = _rs, _r                       # 不真连路由器
    be.upload(p_local, '/tmp/_cc_x')
    check('上传 无SFTP时退到 base64 通道',
          be.sftp_ok is False
          and base64.b64decode(got['b64']) == raw
          and got['cmd'].startswith('base64 -d >')
          and 'mv ' in got['cmd']
          and got['wc'].startswith('wc -c <')
          and 'base64' in be.xfer_note,
          be.xfer_note or '没触发回退')

    be2 = ParamikoBackend.__new__(ParamikoBackend)
    be2.sftp_ok, be2.xfer_note, be2.client = None, '', _NoSftp()
    be2.run_stdin = _rs
    be2.run = lambda cmd, timeout=25: (0, '1')           # 故意报错字节数
    try:
        be2.upload(p_local, '/tmp/_cc_y')
        mismatch_raised = False
    except OpsError:
        mismatch_raised = True
    check('上传 字节数不符会拦截', mismatch_raised)
    try:
        os.remove(p_local)
    except OSError:
        pass

    backup = None
    if os.path.exists(CONFIG_FILE):
        backup = open(CONFIG_FILE, 'rb').read()
    tmp = dict(DEFAULT_CONFIG)
    tmp['host'] = '10.0.0.1'
    # 必须带上 preset_version：否则 load_config 里的预置迁移会把测试值改回去，
    # 这条断言会假失败，而且会顺手把用户真实配置写坏。
    tmp['preset_version'] = PRESET_VERSION
    check('配置写入读回', save_config(tmp) and load_config()['host'] == '10.0.0.1')
    if backup is not None:
        open(CONFIG_FILE, 'wb').write(backup)
    elif os.path.exists(CONFIG_FILE):
        os.remove(CONFIG_FILE)

    print('\n'.join(out))
    print()
    nfail = sum(1 for line in out if '[FAIL]' in line)
    print('结果：%s（共 %d 项，失败 %d 项）'
          % ('全部通过' if ok else '有失败项', len(out), nfail))
    return 0 if ok else 1


def _demo_cases(app):
    """冒烟测试与截图共用的状态样本。"""
    on = (
        '登录脚本  : 就绪\n'
        '开关状态  : 【已开启】路由器会自动登录（2026-09-17 20:34:25 开启）\n'
        '定时任务  :\n'
        '  */2 7-23 * * * [ -f /data/campus/v2/enabled ] && CAMPUS_ROOT=/data/campus/v2 '
        '/data/campus/v2/bin/campus_login.sh >/dev/null 2>&1\n'
        '# CAMPUS-CONSOLE-SCHEDULE\n'
        '  30 8 * * * /data/campus/v2/bin/campus_switch.sh off >/dev/null 2>&1\n'
        '  0 22 * * * /data/campus/v2/bin/campus_switch.sh on >/dev/null 2>&1\n'
        'crond     : 运行中\n'
        '最近日志  :\n'
        '  2026-09-17 20:35:24 warn 当前离线（probe=000），开始认证\n'
        '  2026-09-17 20:35:26 info 认证成功，已恢复在线\n')
    off = (
        '登录脚本  : 就绪\n'
        '开关状态  : 【已关闭】路由器不会自动登录\n'
        '定时任务  :\n'
        '  */2 7-23 * * * [ -f /data/campus/v2/enabled ] && CAMPUS_ROOT=/data/campus/v2 '
        '/data/campus/v2/bin/campus_login.sh >/dev/null 2>&1\n'
        'crond     : 运行中\n'
        '最近日志  :\n'
        '  2026-09-17 20:40:02 warn 网络不可达（1.1.1.1），本轮跳过\n')
    stale = (
        '登录脚本  : 就绪\n'
        '开关状态  : 【已开启】路由器会自动登录\n'
        '定时任务  :\n'
        '  */2 7-23 * * * CAMPUS_ROOT=/data/campus/v2 '
        '/data/campus/v2/bin/campus_login.sh\n'
        'crond     : 运行中\n'
        '最近日志  :\n')
    # 开关开着、cron 行是新的，但 crond 没跑：头部绿色药丸与实际不符，
    # 靠 hero 提示兜底。这条样本专门盯住那个提示别被别的分支吃掉。
    crond_dead = (
        '登录脚本  : 就绪\n'
        '开关状态  : 【已开启】路由器会自动登录\n'
        '定时任务  :\n'
        '  */2 7-23 * * * [ -f /data/campus/v2/enabled ] && CAMPUS_ROOT=/data/campus/v2 '
        '/data/campus/v2/bin/campus_login.sh >/dev/null 2>&1\n'
        'crond     : 未运行\n'
        '最近日志  :\n')
    # 装了 Python 但没装 paramiko 的机器一启动就是这个样子：顶部多一条琥珀色横幅。
    # before= 不能省 —— 运行中 pack 是**追加**的，不加就跑到窗口最底下去了；
    # 真实首次启动时这条是构建期 pack 的，本来就在最上面。
    def missing_dep():
        app.banner.pack(fill='x', pady=(0, u(SP3)), before=app.hero)
        app.render_no_password()

    def baseline():
        """每个样本都从同一状态出发。

        否则后一个样本会继承前一个的残留（自动时段的勾选、缺组件横幅），
        截图上一堆「明明没配路由器却勾着启用」，看着像 bug 其实只是采样顺序。
        """
        app.banner.pack_forget()
        app.var_sched_on.set(False)
        app._sched_toggle()
        app.sched_hint.config(text='未启用')

    def case(name, fn):
        def run():
            baseline()
            fn()
        return (name, run)

    return [
        case('01_未配置', app.render_no_password),
        case('02_已开启', lambda: app.render_status(parse_status(on))),
        case('03_已关闭', lambda: app.render_status(parse_status(off))),
        case('04_未部署', lambda: app.render_status(parse_status('sh: not found'))),
        case('05_旧版定时任务', lambda: app.render_status(parse_status(stale))),
        case('06_定时服务已停', lambda: app.render_status(parse_status(crond_dead))),
        case('07_连不上', lambda: app.render_error(
            '连不上路由器 %s:22\n连接被拒绝（端口 22 未开放）'
            % (app.cfg.get('host') or DEFAULT_ROUTER_HOST))),
        case('08_缺SSH组件', missing_dep),
    ]


def smoke():
    print('== UI 冒烟测试 ==')
    enable_dpi_awareness()
    app = MainWindow()
    app.root.update_idletasks()
    print('窗口尺寸  :', app.root.winfo_geometry(),
          '| 内容最小 %dx%d' % (app.root.winfo_reqwidth(),
                              app.root.winfo_reqheight()))
    wa = work_area()
    r = window_rect(app.root)
    print('任务栏身份  :', current_app_identity() or '未设置')
    print('窗口图标    :', '已挂上' if window_icon_set(app.root) else '未设置')
    print('可用区    : %dx%d' % (wa[2] - wa[0], wa[3] - wa[1]),
          '| 实际外框', r)
    if r:
        over_b = (r[1] + r[3]) - wa[3]
        over_r = (r[0] + r[2]) - wa[2]
        print('越界      :',
              '无' if (over_b <= 0 and over_r <= 0 and r[0] >= 0 and r[1] >= 0)
              else '底 %d / 右 %d' % (over_b, over_r))
    print('缩放      :', round(app.scale, 3), '(DPI %d)' % system_dpi())
    print('首屏标题  :', app.hero_title.cget('text'))
    print('SSH 后端  :', 'paramiko' if app.has_paramiko else
          ('plink' if app.putty[0] else '无'))
    for name, fn in _demo_cases(app):
        fn()
        app.root.update_idletasks()
        print('  渲染 %-16s OK' % name)

    # 回归 A：连不上时四个概览格必须清空。曾经不清，于是界面同时写着
    # 「连不上路由器」和「登录程序 就绪 / 连接方式 已连接」。
    app.render_error('连不上路由器 %s:22' % DEFAULT_ROUTER_HOST)
    app.root.update_idletasks()
    leftovers = [t.value.cget('text')
                 for t in (app.tile_cron, app.tile_script, app.tile_crond)]
    leftover = [v for v in leftovers if v != '—']
    print('连不上复位 :', 'OK' if not leftover else '残留 %r' % leftover)

    # 回归 B：后端名取不到时不能拼出「已连接 · —」这种没意义的文案。
    # --shot/--smoke 下 ops 尚未建立，正好是触发那条路径的场景。
    link = app.link_text()
    print('连接方式   :', link, '' if '—' not in link else '← 含占位符')

    dlg = SettingsDialog(app)
    dlg.update_idletasks()
    print('设置窗口  :', dlg.winfo_geometry())
    dlg.destroy()
    app.root.update_idletasks()
    print('用到图标  :', len(_ICON_USED), '个')
    print('未知图标  :', sorted(_ICON_UNKNOWN) or '无')
    # 图标状态要在 destroy() **之前**读：窗口没了就什么也问不出来
    icon_ok = window_icon_set(app.root)
    ident = current_app_identity()
    app.root.destroy()
    bad = list(_ICON_UNKNOWN)
    if leftover:
        bad.append('概览格残留 %r' % leftover)
    if '—' in link:
        bad.append('连接方式含占位符')
    if r and ((r[1] + r[3]) > wa[3] or (r[0] + r[2]) > wa[2]
              or r[0] < 0 or r[1] < 0):
        bad.append('窗口出屏 %r（可用区 %dx%d）' % (r, wa[2] - wa[0], wa[3] - wa[1]))
    if not icon_ok:
        bad.append('窗口图标未设置')
    if sys.platform == 'win32' and ident != APP_AUMID:
        bad.append('任务栏身份未声明（任务栏会显示 Python 的图标）')
    print('结果：', 'UI 构建通过' if not bad else '失败：%s' % bad)
    return 0 if not bad else 1


def _grab(win, path):
    """截窗口本身。优先 PrintWindow（不受遮挡影响），失败才退回整屏裁剪。"""
    win.update_idletasks()
    win.update()
    time.sleep(0.4)          # 等窗口把这一帧画完
    win.update()

    im = grab_window_image(win)
    how = 'PrintWindow'
    if im is None:
        from PIL import ImageGrab
        x, y = win.winfo_rootx(), win.winfo_rooty()
        w, h = win.winfo_width(), win.winfo_height()
        im = ImageGrab.grab(bbox=(x, y, x + w, y + h))
        how = 'ImageGrab(可能被遮挡)'
    im.save(path)
    # 打出实际抓取尺寸与来源：窗口一旦被窗口管理器挪动/截断，
    # 截图里会混进任务栏或黑边，而文件名看着一切正常，不查这里看不出来
    print('    grab %s %dx%d (屏幕 %dx%d)'
          % (how, im.width, im.height,
             win.winfo_screenwidth(), win.winfo_screenheight()))
    return os.path.getsize(path)


def shot():
    """把每种状态各截一张 PNG，用于设计走查（需要可用的桌面会话）。"""
    out = os.environ.get('CAMPUS_SHOT_DIR') or os.path.join(
        os.path.expanduser('~'), 'campus_shots')
    os.makedirs(out, exist_ok=True)
    try:
        from PIL import ImageGrab  # noqa: F401
    except Exception as e:
        print('需要 Pillow 才能截图：%s' % e)
        return 1
    enable_dpi_awareness()
    app = MainWindow()
    app.root.attributes('-topmost', True)
    app.root.deiconify()
    app.root.lift()
    items = []
    for name, fn in _demo_cases(app):
        fn()
        p = os.path.join(out, name + '.png')
        _grab(app.root, p)
        items.append((name, p))
    dlg = SettingsDialog(app)
    # 序号跟着状态样本走，避免加一条样本就要回头改这里的文件名
    p = os.path.join(out, '%02d_设置.png' % (len(items) + 1))
    _grab(dlg, p)
    items.append(('设置', p))
    dlg.destroy()
    app.root.attributes('-topmost', False)
    app.root.destroy()
    print('输出目录：%s' % out)
    for n, p in items:
        print('  %-18s %7d bytes' % (n, os.path.getsize(p)))
    return 0


def dump_config():
    """不启动界面，直接打印 load_config() 的结果。

    用途：打包/分发核验，以及排查「界面里显示的连接信息为什么不对」。
    它走的就是正常启动那条路径（含 apply_preset），所以「首启会写进什么」
    能用它原样复现出来 —— 不用真的把窗口拉起来。

    敏感字段只报长度不报内容：它的输出经常被重定向进日志，
    把学号/密文原样打出来没有意义，还容易外泄。
    """
    cfg = load_config()
    view = {}
    for k, v in sorted(cfg.items()):
        if k == 'campus_user':
            view[k] = '' if not v else '<%d 位>' % len(str(v))
        elif k.endswith('_enc'):
            view[k] = '' if not v else '<%d 字节密文>' % len(str(v))
        else:
            view[k] = v
    print(json.dumps(view, ensure_ascii=False, indent=2))
    print('配置文件: %s' % CONFIG_FILE)
    return 0


def main():
    # 身份与 DPI 感知都必须在窗口出现之前定下来，所以放在最前面。
    # 声明任务栏身份之后，任务栏才会用我们自己的图标而不是 Python 的。
    enable_dpi_awareness()
    init_app_identity()

    if '--selftest' in sys.argv:
        sys.exit(selftest())
    if '--smoke' in sys.argv:
        sys.exit(smoke())
    if '--shot' in sys.argv:
        sys.exit(shot())
    if '--dump-config' in sys.argv:
        sys.exit(dump_config())

    if not acquire_single_instance():
        if AUTOSTART_FLAG in sys.argv:
            return
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo(APP_NAME, '程序已经在运行了。\n看不到窗口的话，点右下角托盘图标。')
        root.destroy()
        return

    app = MainWindow()
    app.setup_tray()
    if AUTOSTART_FLAG in sys.argv:
        app.say('开机自启：已在托盘后台运行', 'ok')
        if app.cfg.get('minimize_to_tray', True) and app.icon is not None:
            app.root.withdraw()
    try:
        app.root.mainloop()
    finally:
        app.quit()


if __name__ == '__main__':
    main()
