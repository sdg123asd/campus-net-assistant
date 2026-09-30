#!/bin/sh
# =============================================================================
# campus_login.sh — 校园网自动登录入口（编排层）
#
# 职责: 装配模块 → 读取加密凭据 → 在线探测 → 有限次重试登录 → 事件上报。
# 退出码: 0=已在线或登录成功; 1=重试后仍失败; 2=凭据/密钥异常(未初始化)。
#
# 保持原有流程与核心逻辑（probe → 抓页 → 解析 → POST → 复探），仅做:
#   - 模块化与职责拆分;
#   - 精简: 原脚本对 1.1.1.1 的两次 curl 合并为一次;
#   - 传输: 启用 TLS 证书校验（原 -k 取消），POST body 走 stdin;
#   - 凭据: 仅解密到内存，日志自动脱敏;
#   - 健壮: 连接/总超时与有限重试，无死循环等待。
# =============================================================================
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

. "$HERE/lib_log.sh"
. "$HERE/lib_crypto.sh"
. "$HERE/lib_config.sh"
. "$HERE/lib_net.sh"
. "$HERE/lib_webauth.sh"
. "$HERE/lib_ctl.sh"

log_init warn

# ---------- 配置默认值（config.ini 可覆盖） ----------
# 2026-09-06 默认联通性探测由小米站点改为华为云专用检测(规避对小米云的IP风控)
cfg_default probe_url          "http://connectivitycheck.platform.hicloud.com/generate_204"
cfg_default probe_ok_code      "204"
cfg_default portal_url         "http://1.1.1.1/"
cfg_default net_connect_timeout "6"
cfg_default net_max_time       "15"
cfg_default login_retries      "3"
cfg_default login_retry_sleep  "2"
cfg_default cooldown_after_fail "1800"   # 一轮全败后的冷静期（秒），默认 30 分钟
cfg_default state_file         ""        # 冷静期状态文件；留空则用 /tmp/campus_login.cooldown
cfg_default start_hour         ""     # 空=不设时段限制（由 cron 决定调度）
cfg_default end_hour           ""
cfg_load
net_init

# 冷静期状态文件路径
STATE_FILE=$(cfg_get state_file)
[ -n "$STATE_FILE" ] || STATE_FILE=/tmp/campus_login.cooldown
COOLDOWN=$(cfg_get cooldown_after_fail)

# ---------- 可选时段闸门（兼容原脚本 7-23 语义，改为可配置） ----------
_H=$(date +%H)
_SH=$(cfg_get start_hour); _EH=$(cfg_get end_hour)
if [ -n "$_SH" ] && [ "$_H" -lt "$_SH" ] 2>/dev/null; then
  log_debug "当前 $_H 点早于 $_SH 点，跳过"
  exit 0
fi
if [ -n "$_EH" ] && [ "$_H" -gt "$_EH" ] 2>/dev/null; then
  log_debug "当前 $_H 点晚于 $_EH 点，跳过"
  exit 0
fi

ctl_emit boot "$0"

# ---------- 读取加密凭据（仅内存） ----------
_creds=$(cred_load) || exit 2
_USER=$(printf '%s\n' "$_creds" | sed -n 's/^USER=//p')
_PASS=$(printf '%s\n' "$_creds" | sed -n 's/^PASS=//p')
unset _creds
log_secret_add "$_PASS"

# ---------- 1. 已在线则直接结束（并清除冷静期） ----------
if webauth_probe_online; then
  log_info "已在网络（probe=$NET_CODE），无需登录"
  rm -f "$STATE_FILE"
  ctl_emit online "code=$NET_CODE"
  exit 0
fi
log_warn "当前离线（probe=$NET_CODE），开始认证"

# ---------- 1.5 冷静期检查：距上次一轮全败未满阈值则跳过本轮（只探测不登录） ----------
if [ -f "$STATE_FILE" ]; then
  _last=$(cat "$STATE_FILE" 2>/dev/null | tr -dc '0-9')
  _now=$(date +%s)
  if [ -n "$_last" ] && [ "$_now" -lt "$((_last + COOLDOWN))" ] 2>/dev/null; then
    log_warn "冷静期中（已过 $((_now - _last))s / ${COOLDOWN}s），本轮跳过登录"
    ctl_emit login_skip "reason=cooldown"
    exit 0
  fi
  rm -f "$STATE_FILE"   # 冷静期已过，清除旧标记并允许尝试
fi

# ---------- 2. 有限次重试登录 ----------
_attempt=0
_auth_fail=0
_max=$(cfg_get login_retries)
while :; do
  _attempt=$((_attempt + 1))
  log_info "认证尝试 $_attempt/$_max"
  if webauth_do_login "$_USER" "$_PASS"; then
    rm -f "$STATE_FILE"
    ctl_emit login_success "attempt=$_attempt"
    exit 0
  fi
  # 分类记录: auth=portal 可达但认证被拒(需冷静期防爆破); net=网络瞬断(不惩罚)
  [ "$WBAUTH_FAIL" = "auth" ] && _auth_fail=1
  [ "$_attempt" -ge "$_max" ] && break
  _slp=$(cfg_get login_retry_sleep)
  log_debug "等待 ${_slp}s 后重试"
  sleep "$_slp"
done

# 一轮全败：仅"认证被拒"写冷静期；纯网络瞬断(probe=000/抓页失败)不写，
# 网络恢复后下一轮 cron 自动重新登录，避免重启/瞬断误卡 30 分钟。
if [ "$_auth_fail" = "1" ]; then
  printf '%s\n' "$(date +%s)" > "$STATE_FILE"
  ctl_emit login_fail "attempt=$_attempt"
  log_error "认证失败（已尝试 $_attempt 次），进入冷静期 ${COOLDOWN}s"
else
  ctl_emit login_fail "attempt=$_attempt reason=network"
  log_warn "网络不可达（已尝试 $_attempt 次），不进入冷静期，待网络恢复后自动重登"
fi
exit 1
