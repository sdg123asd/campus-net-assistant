# -*- coding: utf-8 -*-
"""deploy_headless.py — 不点界面，直接从命令行把自动登录程序部署到路由器。

等价于校园网助手主界面上的「一键部署」，只是没有 GUI。
配置（路由地址、SSH 账号密码、校园网账号密码、payload 目录）全部读
`~/.campusnet_console.json` —— 也就是界面里保存的那份，不额外维护一套。

用法（**必须用系统 Python**，隔离 venv 里没有 tkinter）：
    "C:\\Users\\李\\AppData\\Local\\Programs\\Python\\Python313\\python.exe" ^
        tools\\deploy_headless.py

    ...\\python.exe tools\\deploy_headless.py --enable      # 部署后顺手打开总开关
    ...\\python.exe tools\\deploy_headless.py --report x.txt

为什么要这个脚本：界面轮询、托盘、DPI 这些东西在无人值守/自动化场景下都是干扰，
而且排查问题时能看到干净的完整日志。

它会写一份报告文件（默认在脚本同目录 `_deploy_report.txt`），失败也会写 ——
出问题直接看那份，比翻终端滚动条可靠。
"""
import argparse
import glob
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)                     # 校园网自动登录-一键部署/
WORKSPACE = os.path.dirname(PROJ)                # 校园网/
ASSISTANT = os.path.join(WORKSPACE, '校园网助手')

sys.path.insert(0, ASSISTANT)
try:
    import campus_console as cc
except ImportError as e:
    sys.stderr.write('找不到 校园网助手/campus_console.py（%s）\n'
                     '期望位置：%s\n' % (e, ASSISTANT))
    raise SystemExit(2)

# 沙箱/服务环境下 HOME **和 USERPROFILE 都会被重定向**（实测：指到
# C:\ProgramData\WorkBuddy\chromium-env\*\），而那个目录里往往残留着一份
# payload_dir 为空的旧配置 → 会以「payload 目录不存在: ''」这种莫名其妙的方式失败。
#   - 从 LOCALAPPDATA 反推真实用户目录（它不被重定向）；
#   - 候选里优先挑「payload_dir 真的存在」的那一份，挑不到再退回第一个存在的文件。
def _cfg_usable(path):
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
    except Exception:
        return False
    pd = (data or {}).get('payload_dir') or ''
    return bool(pd) and os.path.isdir(pd)


def _find_config():
    cands = []
    up = os.environ.get('USERPROFILE')
    if up:
        cands.append(os.path.join(up, '.campusnet_console.json'))
    cands.append(os.path.join(os.path.expanduser('~'), '.campusnet_console.json'))
    # 沙箱把 HOME / USERPROFILE / LOCALAPPDATA / HOMEPATH 全都指到
    # C:\ProgramData\WorkBuddy\chromium-env\*  —— 且那里还残留着一份 payload_dir
    # 为空的旧配置，所以「谁存在用谁」会选错。USERNAME 是真的，优先按它拼，
    # 再不行就扫 C:\Users\*\。最后挑「payload_dir 真的存在」的那一份。
    un = os.environ.get('USERNAME')
    if un:
        cands.append(os.path.join('C:\\Users', un, '.campusnet_console.json'))
    try:
        cands.extend(sorted(glob.glob('C:\\Users\\*\\.campusnet_console.json')))
    except Exception:
        pass
    seen, uniq = set(), []
    for p in cands:
        if p and p not in seen:
            seen.add(p)
            uniq.append(p)
    for p in uniq:
        if os.path.isfile(p) and _cfg_usable(p):
            return p
    for p in uniq:
        if os.path.isfile(p):
            return p
    return uniq[0]


REAL_CFG = _find_config()


def main():
    ap = argparse.ArgumentParser(description='无头部署校园网自动登录到路由器')
    ap.add_argument('--enable', action='store_true',
                    help='部署完顺手打开总开关（默认保持关闭，避免顶掉你在外面教室的会话）')
    ap.add_argument('--report', default=os.path.join(HERE, '_deploy_report.txt'),
                    help='报告文件路径')
    ap.add_argument('--config', default=REAL_CFG, help='配置文件路径')
    args = ap.parse_args()

    cc.CONFIG_FILE = args.config
    buf = io.StringIO()

    def say(msg=''):
        line = str(msg)
        print(line)
        buf.write(line + '\n')

    def finish(code):
        try:
            with open(args.report, 'w', encoding='utf-8') as fh:
                fh.write(buf.getvalue())
        except OSError as e:
            print('写报告失败: %s' % e)
        return code

    say('配置文件 : %s' % cc.CONFIG_FILE)
    cfg = cc.load_config()
    say('路由器   : %s:%s  用户 %s' % (cfg['host'], cfg['port'], cfg['user']))
    say('预置版本 : %s' % cfg.get('preset_version'))

    pw = cc.dpapi_decrypt(cfg.get('router_password_enc', ''))
    cuser = cfg.get('campus_user', '')
    cpw = cc.dpapi_decrypt(cfg.get('campus_password_enc', ''))
    payload = cfg.get('payload_dir', '')

    for label, val in (('路由器密码', pw), ('校园网密码', cpw)):
        if not val:
            say('!! %s 解不出来 —— 先在界面「设置」里填一次并保存' % label)
            return finish(3)
    if not payload or not os.path.isdir(payload):
        say('!! payload 目录不存在: %r' % payload)
        say('   这次读的配置是: %s' % cc.CONFIG_FILE)
        say('   如果是沙箱/无头环境，请显式指定: --config C:\\Users\\<你>\\.campusnet_console.json')
        return finish(3)
    if not cuser:
        say('!! 校园网账号为空')
        return finish(3)

    say('校园网   : %s' % cuser)
    say('payload  : %s' % payload)
    say()

    ops = cc.RouterOps(cfg, log=lambda m: say('  . ' + m))
    try:
        ops.connect(pw, note=lambda m: say('  ! ' + m))
        say('已连接，后端 = %s' % ops.be.name)

        say()
        say('===== 部署（总开关 %s） =====' % ('开启' if args.enable else '关闭'))
        ops.deploy(pw, cuser, cpw, payload, args.enable, lambda m: say('  - ' + m))
        say('部署流程返回成功')

        say()
        say('===== 部署后核对 =====')
        for label, cmd in [
            ('文件清单', 'ls -la %s %s/bin %s/conf %s/key'
                         % ((cc.REMOTE_ROOT,) * 4)),
            ('cron 行数（应为 1）', "grep -c campus /etc/crontabs/root"),
            ('cron 内容', 'grep campus /etc/crontabs/root'),
            ('凭据校验', '%s verify 2>&1' % cc.REMOTE_CRED),
            ('开关状态', '%s status 2>&1' % cc.REMOTE_SWITCH),
            ('enabled 标记', 'ls -l %s/enabled 2>&1' % cc.REMOTE_ROOT),
        ]:
            rc, out = ops.be.run(cmd, timeout=45)
            say('[%s] rc=%s' % (label, rc))
            say(out.rstrip())
            say('-' * 56)

        st = ops.status(pw)
        say('status = %r' % (st,))
        if not st.get('deployed'):
            say('!! 状态显示未部署，请检查上面的输出')
            return finish(4)

        fp = (getattr(ops.be, 'new_fingerprint', '')
              or getattr(ops.be, 'fingerprint', ''))
        if fp and fp != cfg.get('host_fingerprint', ''):
            cfg['host_fingerprint'] = fp
            cc.save_config(cfg)
            say('host_fingerprint 已更新: %s' % fp)

        say()
        say('完成。总开关 = %s' % ('开启（回宿舍即用）' if args.enable
                                  else '关闭（回宿舍后点「开启自动登录」）'))
        return finish(0)
    except cc.OpsError as e:
        say()
        say('!! 部署失败: %s' % e)
        return finish(1)
    except Exception as e:
        say()
        say('!! 意外错误: %r' % e)
        return finish(1)
    finally:
        try:
            ops.close()
        except Exception:
            pass


if __name__ == '__main__':
    sys.exit(main())
