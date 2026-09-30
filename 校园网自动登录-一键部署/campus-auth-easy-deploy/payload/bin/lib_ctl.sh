#!/bin/sh
# =============================================================================
# lib_ctl.sh — 远程控制扩展点（仅接口与调度，不含任何远程实现）
#
# 当前状态: 预留。本模块被入口与各流程调用，但默认「什么都不做」——
# 只有当 hooks 目录存在且含可执行脚本时才逐一调用。因此远程控制功能的
# 接入完全不需要改动 campus_login.sh 的核心流程。
#
# ---- 事件接口（hooks 收到的固定参数） ----
#   ctl_emit <event> [key=value ...]
#   event ∈ boot|online|login_success|login_fail|offline
# 每个 hook 以独立进程执行:  hook.sh <event> <k=v>...
# 超时: 每个 hook 最多运行 CTL_HOOK_TIMEOUT（默认 5s），防拖垮登录主流程。
#
# ---- hooks 目录 ----
#   默认: <root>/conf/ctl_hooks.d/  （由 CTL_HOOKS_DIR 覆盖）
#   目录不存在或为空 → ctl_emit 立即返回，零开销。
#
# ---- 安全约定（远程控制正式设计见 docs/remote_control_plan.md） ----
#   * hooks 永远收不到明文密码等敏感数据（事件参数仅限状态与计数）；
#   * 任何未来接入的“命令下发”都必须走 出站 通道（如 MQTT over TLS /
#     HTTPS 长轮询），禁止在路由器上开放入站端口;
#   * 设备身份与会话校验在通道层完成，本模块只做事件扇出。
# =============================================================================

CTL_HOOKS_DIR="${CTL_HOOKS_DIR:-$(cfg_root)/conf/ctl_hooks.d}"
CTL_HOOK_TIMEOUT="${CTL_HOOK_TIMEOUT:-5}"

_ctl_emit() {
  _ev="$1"; shift
  [ -d "$CTL_HOOKS_DIR" ] || return 0
  _cnt=0
  for _h in "$CTL_HOOKS_DIR"/*.sh; do
    [ -x "$_h" ] || continue
    _cnt=$((_cnt + 1))
    timeout "$CTL_HOOK_TIMEOUT" "$_h" "$_ev" "$@" >/dev/null 2>&1 &
  done
  [ "$_cnt" -gt 0 ] && wait 2>/dev/null
  return 0
}

# 供主流程调用（模块内部函数名前缀 ctl_）
ctl_emit() {
  _ctl_emit "$@"
}

# 自检：打印当前 hooks 目录与事件约定（调试用）
ctl_selfcheck() {
  echo "CTL_HOOKS_DIR=$CTL_HOOKS_DIR"
  echo "CTL_HOOK_TIMEOUT=$CTL_HOOK_TIMEOUT"
  echo "事件集: boot online offline login_success login_fail"
}
