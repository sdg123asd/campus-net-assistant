# -*- coding: utf-8 -*-
"""打包exe.py — 把「校园网助手」打成单文件 exe，方便分发给别人。

用法（**必须用系统 Python**，隔离 venv 里没有 tkinter）：

    "C:\\Users\\李\\AppData\\Local\\Programs\\Python\\Python313\\python.exe" 打包exe.py

产出（默认落在同目录的 发布\\ 下）：

    校园网助手.exe          —— 带你自己的预置连接信息，自己用（双击即用，不用装 Python）
    校园网助手-通用版.exe    —— 账号密码已清空，发给别人用

为什么是两个
------------
源码里的 PRESET 固定了你这台路由器的 SSH 密码和校园网学号+密码。直接打包发出去，
等于把学号密码一起发出去（`strings 校园网助手.exe` 就能看到）。所以通用版在打包前
会用正则把 PRESET 里三个敏感字段清空 —— 替换不完整会直接报错，绝不静默产脏包。

通用版对使用者是安全的：apply_preset() 只在「字段为空」时才灌预置值，
PRESET 为空自然什么都不灌，不会覆盖使用者自己填的内容。

为什么能打包
------------
campus_console.py 里的图标/徽章/品牌图全部是**运行时用 Pillow 现画**的，
不依赖任何外部图片资源；唯一要随包的静态数据是部署用的 payload 目录，
用 --add-data 塞进去即可。程序内已针对 frozen 状态做过三处适配
（res_dir / data_dir / 无控制台时的输出落点），见文件里的「打包适配」段。
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
WORKSPACE = os.path.dirname(HERE)
SRC = os.path.join(HERE, 'campus_console.py')
PAYLOAD = os.path.join(WORKSPACE, '校园网自动登录-一键部署',
                       'campus-auth-easy-deploy', 'payload')
DIST = os.path.join(HERE, '发布')

# PRESET 里一旦泄露就等于泄露学号密码的字段。host/port/user 不清 ——
# 它们是「小米路由器出厂默认 + dropbear 只有 root」这类通用事实，
# 对使用者反而是有用的兜底，留着不构成隐私问题。
SECRET_KEYS = ('router_password', 'campus_user', 'campus_password')

# 系统 Python 里装了一大堆与本程序无关的重型库（torch / PyQt6 / 各类视觉库）。
# PyInstaller 只跟随 import，本来就不会带上，这里显式排除是为了：
#   1. 避免某些 hook 误收（体积会翻几倍）；
#   2. 缩短打包时间。
# 排错对象都是本程序绝对不会 import 的模块，真漏了会在自检里炸出来。
EXCLUDES = [
    'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'matplotlib', 'torch',
    'torchvision', 'cv2', 'ultralytics', 'onnxruntime', 'pandas', 'scipy',
    'sympy', 'numpy', 'IPython', 'notebook', 'pytest', 'sqlite3',
    'test', 'unittest',
]

VARIANTS = [
    ('校园网助手', False, '带你自己的预置连接信息（自己用）'),
    ('校园网助手-通用版', True, '账号密码已清空（发给别人）'),
]


_LOG = {'path': None}


def say(msg=''):
    # 同时写一份到文件：本机 PowerShell 管道会把 Python 的中文输出二次乱码，
    # 让脚本自己落盘是最稳的取证方式。
    print(msg, flush=True)
    if _LOG['path']:
        try:
            with open(_LOG['path'], 'a', encoding='utf-8') as fh:
                fh.write(str(msg) + '\n')
        except OSError:
            pass


def die(msg):
    say('!! ' + msg)
    sys.exit(1)


# ---------------------------------------------------------------- 前置检查

def preflight():
    say('== 前置检查 ==')
    if not os.path.isfile(SRC):
        die('找不到 %s' % SRC)

    # tkinter 必须在**当前解释器**里可用 —— 它没法 pip 装，
    # 一旦用了隔离 venv 的解释器，打出来的 exe 起不来。
    try:
        import tkinter  # noqa: F401
    except Exception as e:
        die('当前解释器没有 tkinter（%s）\n   %s\n'
            '   必须用系统 Python：'
            r'C:\Users\<你>\AppData\Local\Programs\Python\Python313\python.exe'
            % (sys.executable, e))
    say('解释器     : %s' % sys.executable)
    say('Python     : %s' % sys.version.split()[0])

    try:
        import PyInstaller  # noqa: F401
    except Exception:
        die('没有 PyInstaller。先装：\n   "%s" -m pip install pyinstaller'
            % sys.executable)
    from PyInstaller import __version__ as pv
    say('PyInstaller: %s' % pv)

    for mod in ('paramiko', 'PIL', 'pystray'):
        try:
            __import__(mod)
            say('依赖 %-9s: OK' % mod)
        except Exception as e:
            say('依赖 %-9s: 缺失（%s）—— 打进 exe 后该功能会降级' % (mod, e))

    if not os.path.isdir(PAYLOAD) or not os.path.isfile(
            os.path.join(PAYLOAD, 'bin', 'campus_switch.sh')):
        die('payload 目录不完整: %s' % PAYLOAD)
    say('payload    : %s' % PAYLOAD)

    # 图标：让主程序自己画一份出来，保证与界面里的品牌图一致
    sys.path.insert(0, HERE)
    import campus_console as cc
    icon = cc.ensure_app_icon()
    if not icon or not os.path.isfile(icon):
        die('生成 campus.ico 失败')
    say('图标       : %s (%d bytes)' % (icon, os.path.getsize(icon)))
    say()


# ---------------------------------------------------------------- 通用版清洗

PRESET_RE = re.compile(r'^PRESET = \{.*?^\}', re.S | re.M)


def sanitize_preset(text):
    """清空 PRESET 里的账号/密码。返回 (新文本, 被清掉的键, 清洗后的块)。

    宁可报错也不产出脏包 —— 这个函数一旦静默失败，学号密码就跟着 exe 发出去了。
    第三个返回值用来把「实际打进包里的是什么」打到日志里，留成可核对的证据。
    """
    m = PRESET_RE.search(text)
    if not m:
        die('在源码里找不到 PRESET 块，打包脚本的正则需要更新')
    block = m.group(0)
    new = block
    done = []
    for key in SECRET_KEYS:
        pat = re.compile(r"('%s':\s*)'[^']*'" % key)
        new, n = pat.subn(lambda mm: mm.group(1) + "''", new)
        if n:
            done.append(key)
    missing = [k for k in SECRET_KEYS if k not in done]
    if missing:
        die('PRESET 清洗不完整，没清掉 %r —— 拒绝产出通用版' % missing)
    if new == block:
        die('PRESET 清洗没有产生任何变化，正则失效了')
    return text[:m.start()] + new + text[m.end():], done, new


# ---------------------------------------------------------------- 打包

def build(name, src_path, work_root):
    dst = os.path.join(DIST, name + '.exe')
    cmd = [
        sys.executable, '-m', 'PyInstaller',
        '--noconfirm', '--clean',
        '--onefile', '--windowed',
        '--name', name,
        '--icon', os.path.join(HERE, 'campus.ico'),
        # payload 打进去 → 别人只拿一个 exe 就能完成部署
        '--add-data', '%s;payload' % PAYLOAD,
        # Tk 与托盘的后端都是运行时动态挑的，PyInstaller 静态分析抓不到
        '--hidden-import', 'pystray._win32',
        '--hidden-import', 'PIL.ImageTk',
        '--distpath', DIST,
        '--workpath', os.path.join(work_root, 'build_' + name),
        '--specpath', work_root,
        '--log-level', 'WARN',
    ]
    for mod in EXCLUDES:
        cmd += ['--exclude-module', mod]
    cmd.append(src_path)

    say('  $ (工作目录 %s)' % work_root)
    p = subprocess.run(cmd, cwd=work_root, capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    log = os.path.join(work_root, 'pyinstaller_%s.log' % name)
    with open(log, 'w', encoding='utf-8') as fh:
        fh.write(' '.join(cmd) + '\n\n')
        fh.write(p.stdout or '')
        fh.write(p.stderr or '')
    if p.returncode != 0 or not os.path.isfile(dst):
        say((p.stderr or p.stdout or '')[-3000:])
        die('打包 %s 失败，完整日志：%s' % (name, log))
    return dst, log


# ---------------------------------------------------------------- 验证

def _decode(raw):
    """按 utf-8 → gbk → latin-1 依次尝试解码，返回 (文本, 实际生效的编码)。

    为什么要这么麻烦：--windowed 的 exe 往管道写中文时，实际用的是 UTF-8 还是
    本机 ANSI 代码页(GBK)，受 PyInstaller bootloader 与 Python 的交互影响 ——
    实测给子进程设 PYTHONIOENCODING=utf-8 / PYTHONUTF8=1 **并不生效**，
    按 UTF-8 硬解就全是 U+FFFD，连「失败 0 项」都匹配不上，于是自检被误判为失败。
    改成先落字节、再逐种解码；latin-1 永不抛异常，保证兜得住。
    """
    for enc in ('utf-8', 'gbk', 'latin-1'):
        try:
            return raw.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode('utf-8', 'replace'), 'utf-8/replace'


def _probe_env(probe_dir):
    """给被测 exe 一份隔离的环境。

    只改这几个：Python 在 Windows 上用 USERPROFILE 解析 ~（决定配置文件位置），
    LOCALAPPDATA/APPDATA 决定 data_dir()（campus.ico / console_output.txt）落哪。
    别动 HOMEDRIVE/HOMEPATH —— 它们要求是「盘符 + 相对路径」，
    塞绝对路径进去会让别处拼出畸形路径。
    """
    env = dict(os.environ)
    for key in ('USERPROFILE', 'HOME', 'LOCALAPPDATA', 'APPDATA'):
        env[key] = probe_dir
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUTF8'] = '1'
    return env


def verify(exe, probe_dir):
    """用隔离的假用户目录跑 --selftest。返回 (rc, 自检输出文本, 采用的解码)。"""
    env = _probe_env(probe_dir)

    raw_path = os.path.join(probe_dir, '_selftest_raw.txt')
    try:
        # 用文件而不是管道接输出：管道在这套环境里还有别的坑，
        # 文件既能拿到原始字节，也不会因为缓冲把进程挂住。
        with open(raw_path, 'wb') as fh:
            p = subprocess.run([exe, '--selftest'], env=env, timeout=300,
                               stdout=fh, stderr=subprocess.STDOUT)
        rc = p.returncode
    except subprocess.TimeoutExpired:
        return 99, '(自检超时)', '-'

    # 若子进程完全没有标准输出句柄，程序会把 print 重定向到这个文件
    # （那份一定是 UTF-8 写的，优先用它）。
    out_file = os.path.join(probe_dir, 'CampusNetAssistant',
                            'console_output.txt')
    text, enc = '', '-'
    if os.path.isfile(out_file):
        with open(out_file, 'rb') as fh:
            text, enc = _decode(fh.read())
    if not text.strip():
        with open(raw_path, 'rb') as fh:
            text, enc = _decode(fh.read())
    return rc, text, enc


def probe_config(exe, probe_dir):
    """问程序「首启会写出什么配置」（--dump-config，不启动界面）。

    这是「通用版 exe 里到底有没有学号密码」唯一端到端可证的办法 ——
    exe 内的字节是压缩的，strings 扫不到**不等于**没有；
    必须让程序自己按正常启动流程解出来才算数。
    返回解析出的 dict（失败则 {}）。dump_config 只报长度不报明文。
    """
    try:
        p = subprocess.run([exe, '--dump-config'], env=_probe_env(probe_dir),
                           timeout=120, capture_output=True)
    except subprocess.TimeoutExpired:
        return {}
    text, _ = _decode(p.stdout or b'')
    a, b = text.find('{'), text.rfind('}')
    if a < 0 or b < 0:
        return {}
    try:
        return json.loads(text[a:b + 1])
    except Exception:
        return {}


def payload_line(text):
    """从自检输出里捞出 payload 那一行 —— 用来证明打包进去的那份被找到了。"""
    for line in text.splitlines():
        if line.startswith('payload'):
            return line.split(':', 1)[-1].strip()
    return ''


def run_verification(names, work_root):
    """对 发布\\ 下已有的 exe 逐个跑自检。返回 results 列表。"""
    results = []
    for name, generic, note in VARIANTS:
        if name not in names:
            continue
        exe = os.path.join(DIST, name + '.exe')
        if not os.path.isfile(exe):
            say('!! 找不到 %s' % exe)
            results.append((name, exe, 0, False, ''))
            continue
        size = os.path.getsize(exe)
        probe = os.path.join(work_root, 'probe_' + name)
        os.makedirs(probe, exist_ok=True)
        rc, out, enc = verify(exe, probe)
        lines = [l for l in out.splitlines() if l.strip()]
        summary = lines[-1].strip() if lines else '(无输出)'
        ok = (rc == 0) and ('失败 0' in out)
        say('  %-18s rc=%s  解码=%s  %s' % (name, rc, enc, summary))
        pl = payload_line(out)
        if pl:
            say('  %-18s payload → %s' % ('', pl))

        # 端到端隐私核对：让 exe 自己按正常启动流程把配置解出来。
        view = probe_config(exe, probe)
        cuser = view.get('campus_user', '')
        has_pw = bool(view.get('router_password_enc'))
        if not view:
            say('  %-18s 首启配置：取不到，隐私项无法判定' % '')
        else:
            say('  %-18s 首启配置：campus_user=%s  路由器密码=%s'
                % ('', '空' if not cuser else '已填充',
                   '有' if has_pw else '无'))
        if generic and (cuser or has_pw):
            say('  %-18s !! 通用版里仍带预置凭据 —— 拒绝通过' % '')
            ok = False

        with open(os.path.join(DIST, name + '.自检.txt'), 'w',
                  encoding='utf-8') as fh:
            fh.write('exe  : %s\n' % exe)
            fh.write('rc   : %s\n' % rc)
            fh.write('大小 : %d bytes\n' % size)
            fh.write('payload : %s\n' % (pl or '(未取到)'))
            fh.write('首启配置: %s\n' % json.dumps(view, ensure_ascii=False))
            fh.write('\n' + out)
        results.append((name, exe, size, ok, pl))
    return results


def main():
    argv = sys.argv[1:]
    verify_only = '--verify-only' in argv

    os.makedirs(DIST, exist_ok=True)
    _LOG['path'] = os.path.join(DIST, '打包日志.txt')
    if not verify_only:
        with open(_LOG['path'], 'w', encoding='utf-8') as fh:
            fh.write('打包日志 %s\n\n' % sys.version.split()[0])

    work_root = tempfile.mkdtemp(prefix='campus_exe_build_')
    try:
        if verify_only:
            # 只跑自检，不重新打包（重打一次要 3 分半，调验证逻辑时没必要）
            say('===== 只验证（不重新打包） =====')
            names = [n for n, _g, _d in VARIANTS]
            results = run_verification(names, work_root)
        else:
            preflight()
            with open(SRC, encoding='utf-8') as fh:
                original = fh.read()
            say('临时工作目录: %s' % work_root)
            say()

            results = []
            for name, generic, note in VARIANTS:
                say('===== 打包 %s（%s） =====' % (name, note))
                variant_dir = os.path.join(work_root, 'src_' + str(generic))
                os.makedirs(variant_dir, exist_ok=True)
                text = original
                if generic:
                    text, cleared, block = sanitize_preset(original)
                    say('  已清空 PRESET 字段: %s' % ', '.join(cleared))
                    # 把实际进包的那段贴进日志：这是「通用版不含学号密码」
                    # 唯一可核对的证据（exe 里的字节是压缩的，扫不出来）
                    say('  进包的 PRESET 块：')
                    for line in block.splitlines():
                        say('    ' + line)
                src_path = os.path.join(variant_dir, 'campus_console.py')
                with open(src_path, 'w', encoding='utf-8') as fh:
                    fh.write(text)

                exe, _log = build(name, src_path, work_root)
                say('  -> %s  (%.1f MB)'
                    % (exe, os.path.getsize(exe) / 1024.0 / 1024.0))
                results += run_verification([name], work_root)
                say()

        say('===== 汇总 =====')
        for name, exe, size, ok, pl in results:
            say('%-20s %7.1f MB  %-8s  payload=%s'
                % (name, size / 1024.0 / 1024.0,
                   '自检通过' if ok else '自检未全过', pl or '(未取到)'))
        say()
        say('输出目录: %s' % DIST)
        return 0 if results and all(r[3] for r in results) else 1
    finally:
        shutil.rmtree(work_root, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
