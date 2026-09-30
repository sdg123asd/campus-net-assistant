#!/bin/sh
# =============================================================================
# lib_log.sh — 分级日志模块（唯一写日志入口；自动脱敏注册的密钥）
#
# 对外接口:
#   log_init          <level>          初始化日志（level: debug|info|warn|error）
#   log_set_file      <path>           设置日志文件（默认 /tmp/campus_login.log）
#   log_secret_add    <string>         注册一段需要脱敏的文本（如明文密码）
#   log_redact        <string>         返回脱敏后的字符串（stdout）
#   log_debug|info|warn|error  <fmt> … 输出一条带时间戳与级别的日志
#   log_fmt           <fmt> [args...]  轻量 printf 包装
#
# 设计约束:
#   - 任何模块记录日志都必须经由本模块，以便统一脱敏；
#   - 禁止把明文密码直接交给 log_*（先 log_secret_add 注册一次）；
#   - 仅允许写 /tmp（ramfs），日志文件超限自动截断，不落 flash。
# =============================================================================

LOG_LEVEL=2          # 0=debug 1=info 2=warn 3=error（默认 warn）
LOG_FILE=/tmp/campus_login.log
LOG_SECRETS=""       # 冒号分隔的待脱敏串（冒号本身不参与匹配）
LOG_MAX_BYTES=65536

_loglevel_num() {
  case "$1" in
    debug) echo 0 ;; info) echo 1 ;; warn) echo 2 ;; error) echo 3 ;;
    *) echo 2 ;;
  esac
}

log_init() {
  LOG_LEVEL=$(_loglevel_num "${1:-warn}")
  log_set_file "${2:-/tmp/campus_login.log}"
}

log_set_file() {
  LOG_FILE="$1"
  # 日志文件仅允许在 tmpfs，防止磨损 flash
  case "$LOG_FILE" in
    /tmp/*) : ;;
    *) LOG_FILE=/tmp/campus_login.log ;;
  esac
  [ -e "$LOG_FILE" ] || : > "$LOG_FILE" 2>/dev/null || true
}

log_secret_add() {
  [ -n "$1" ] || return 0
  case ":$LOG_SECRETS:" in
    *":$1:"*) : ;;
    *) LOG_SECRETS="$LOG_SECRETS:$1" ;;
  esac
}

log_redact() {
  _lr_s="$1"
  _lr_old="$LOG_SECRETS"
  while [ -n "$_lr_old" ]; do
    case "$_lr_old" in
      :*) _lr_old="${_lr_old#:}" ;;
      *) ;;
    esac
    _lr_secret="${_lr_old%%:*}"
    case "$_lr_secret" in
      "") break ;;
    esac
    [ -n "$_lr_secret" ] && _lr_s=$(printf '%s' "$_lr_s" | sed "s|$_lr_secret|[REDACTED]|g")
    _lr_old="${_lr_old#$_lr_secret}"
    _lr_old="${_lr_old#:}"
  done
  printf '%s\n' "$_lr_s"
}

_log_rotate() {
  _lr_size=$(wc -c < "$LOG_FILE" 2>/dev/null || echo 0)
  if [ "$_lr_size" -gt "$LOG_MAX_BYTES" ]; then
    : > "$LOG_FILE" 2>/dev/null || true
  fi
}

_log_write() {
  _lw_lvl="$1"; _lw_lvlno="$2"; shift 2
  [ "$_lw_lvlno" -ge "$LOG_LEVEL" ] || return 0
  _lw_line=$(printf '%s %-5s ' "$(date '+%F %T')" "$_lw_lvl")
  _lw_line="$_lw_line$(printf '%s\n' "$*")"
  _lw_line=$(log_redact "$_lw_line")
  _log_rotate
  printf '%s\n' "$_lw_line" >> "$LOG_FILE" 2>/dev/null || true
}

log_debug() { _log_write debug 0 "$@"; }
log_info()  { _log_write info  1 "$@"; }
log_warn()  { _log_write warn  2 "$@"; }
log_error() { _log_write error 3 "$@"; }

# 断言：条件不成立则记录 error 并退出
log_assert() {
  _la_cond="$1"; _la_msg="$2"; _la_code="${3:-1}"
  if ! eval "$_la_cond"; then
    log_error "$_la_msg"
    exit "$_la_code"
  fi
}
