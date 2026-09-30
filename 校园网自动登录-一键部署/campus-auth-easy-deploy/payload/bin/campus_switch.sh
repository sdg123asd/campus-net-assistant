#!/bin/sh
# =============================================================================
# campus_switch.sh — 自动登录总开关（单账号单会话校园网必备）
#
# 为什么需要它:
#   部分学校限制「一个账号同时只能一台设备在线」。路由器一旦自动登录成功，
#   你在教室/图书馆用同一账号登录就会把它顶下线; 而 cron 每 2 分钟又会把它
#   抢回来 —— 结果是你刚连上就断。所以离开宿舍前必须能把它关掉。
#
#   开关关闭时，cron 行里的 [ -f .../enabled ] 判定不成立，登录脚本连启动都
#   不会启动，零开销、零副作用。
#
# 用法:
#   campus_switch.sh on       开启（写开关文件 + 立刻尝试登录一次）
#   campus_switch.sh off      关闭（删开关文件，此后不再自动登录）
#   campus_switch.sh status   查看状态（开关 / 定时任务 / 最近日志）
#   campus_switch.sh toggle   取反
#
# 开关文件: <root>/enabled
#   - 必须放 /data 持久分区: 重启不丢。（放 /tmp 是 ramfs，重启即失效，不能用）
#   - 只在切换时写一次，不造成闪存磨损。
# =============================================================================
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=${CAMPUS_ROOT:-${HERE%/bin}}
FLAG="$ROOT/enabled"
LOGIN="$HERE/campus_login.sh"
LOG=/tmp/campus_login.log

die() { printf '%s\n' "$1" >&2; exit 1; }

_require() {
  [ -d "$ROOT" ] || die "找不到安装目录: $ROOT（请先运行「一键部署.bat」）"
  [ -f "$LOGIN" ] || die "找不到登录脚本: $LOGIN（请重新运行「一键部署.bat」）"
}

flag_on() { [ -f "$FLAG" ]; }

flag_set() {
  umask 077
  printf '%s\n' "$(date '+%F %T')" > "$FLAG" 2>/dev/null || return 1
  return 0
}

cron_lines() {
  # /etc 是 /data/etc 的 bind mount（同一个 inode），这两个路径 grep 出来的是
  # 同一条 → 只认 crond 真正读的那一份（crond 以 -c /etc/crontabs 启动）。
  # 否则 status 会把同一行打印两次，看着像装了两条定时任务。
  # 保留第二个路径只为兼容「非 bind mount」的固件：第一个路径没有命中时才看它。
  for _f in /etc/crontabs/root /data/etc/crontabs/root; do
    [ -f "$_f" ] || continue
    _cl_hit=$(grep 'campus_login\.sh' "$_f" 2>/dev/null)
    [ -n "$_cl_hit" ] || continue
    printf '%s\n' "$_cl_hit"
    return 0
  done
}

cmd_on() {
  _require
  if flag_on; then
    echo "已经是开启状态，不用重复开启。"
  else
    flag_set || die "无法写入开关文件: $FLAG（磁盘满或权限异常？）"
    echo "【已开启】路由器会继续自动帮你登录校园网。"
  fi
  echo
  echo "提醒: 现在是「路由器优先」。你在教室/图书馆用同一账号登录，会把它"
  echo "      顶下线、并在 2 分钟内被它抢回去。离开宿舍前记得关掉。"
  echo
  echo "立刻尝试登录一次 ..."
  CAMPUS_ROOT="$ROOT" "$LOGIN" >/dev/null 2>&1
  _rc=$?
  case "$_rc" in
    0) echo "结果: 已在线（或刚刚登录成功），宿舍网络现在可用。" ;;
    2) echo "结果: 账号密码未初始化或不匹配 → 请重新运行「一键部署.bat」。" ;;
    *) echo "结果: 这次没成功（多半是当前不在校园网内）。" ;;
  esac
  _tail=$(tail -3 "$LOG" 2>/dev/null)
  if [ -n "$_tail" ]; then
    echo
    printf '%s\n' "$_tail"
  fi
  return 0
}

cmd_off() {
  if flag_on; then
    rm -f "$FLAG"
    echo "【已关闭】路由器不再自动登录校园网。"
  else
    echo "本来就是关闭状态。"
  fi
  echo
  echo "现在你在教室/任何地方用校园网都不会被打扰。"
  echo "（若路由器此刻还占着在线会话，你在外面的登录会把它顶下线，属正常现象。）"
  echo "想恢复宿舍自动上网，重新执行: $0 on"
}

cmd_status() {
  echo "安装目录  : $ROOT"
  if [ -f "$LOGIN" ]; then
    echo "登录脚本  : 就绪"
  else
    echo "登录脚本  : 缺失！请重新运行「一键部署.bat」"
  fi
  if flag_on; then
    _since=$(sed -n 1p "$FLAG" 2>/dev/null)
    echo "开关状态  : 【已开启】路由器会自动登录${_since:+（$_since 开启）}"
  else
    echo "开关状态  : 【已关闭】路由器不会自动登录"
  fi
  echo "开关文件  : $FLAG  $([ -f "$FLAG" ] && echo 存在 || echo 不存在)"
  echo
  echo "定时任务  :"
  _cl=$(cron_lines)
  if [ -n "$_cl" ]; then
    printf '%s\n' "$_cl" | while IFS= read -r _l; do
      printf '  %s\n' "$_l"
    done
    case "$_cl" in
      *enabled*) : ;;
      *) echo "  ! 上面这行不含开关判定（属旧版本部署），开关不会生效，请重新运行「一键部署.bat」" ;;
    esac
  else
    echo "  （没找到定时任务，请重新运行「一键部署.bat」）"
  fi
  if pgrep crond >/dev/null 2>&1; then
    echo "crond     : 运行中"
  else
    echo "crond     : 未运行！自动登录不会触发"
  fi
  echo
  echo "最近日志  :"
  if [ -s "$LOG" ]; then
    tail -5 "$LOG" 2>/dev/null | while IFS= read -r _l; do
      printf '  %s\n' "$_l"
    done
  else
    echo "  （空 —— 说明网络一直正常，它不需要干活）"
  fi
}

cmd_toggle() {
  if flag_on; then
    cmd_off
  else
    cmd_on
  fi
}

case "${1:-status}" in
  on)          cmd_on ;;
  off)         cmd_off ;;
  toggle)      cmd_toggle ;;
  status|st|'') cmd_status ;;
  *)
    echo "用法: $0 on | off | status | toggle" >&2
    exit 2 ;;
esac
