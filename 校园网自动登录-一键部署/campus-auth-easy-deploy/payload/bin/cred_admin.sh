#!/bin/sh
# =============================================================================
# cred_admin.sh — 凭据管理 CLI
#
# 用法:
#   cred_admin.sh init   [--stdin] [--force]
#       交互式录入账号/密码并加密保存。
#       --stdin: 从标准输入读两行（第一行账号、第二行密码），供 CI/自测无交互调用
#       --force:  密钥已存在时仍重新生成（谨慎）
#   cred_admin.sh verify          解密并校验凭据（只显示掩码）
#   cred_admin.sh status          文件与权限状态、密文指纹（不显示明文）
#   cred_admin.sh wipe  [--also-key]
#       删除凭据密文（默认保留密钥；--also-key 连同密钥一并删除）
#
# 约束: 全流程不把密码写入 argv/日志；交互读取时关闭回显。
# =============================================================================
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$HERE/lib_log.sh"
. "$HERE/lib_crypto.sh"
. "$HERE/lib_config.sh"

log_init warn

_mask() {  # user → u***（只留首字符）
  _m="$1"
  [ ${#_m} -ge 2 ] && printf '%s***' "$(printf '%s' "$_m" | cut -c1)" || printf '%s' "$_m"
}

_read_user() {
  printf '账号: ' >&2
  IFS= read -r _u || return 1
  printf '%s' "$_u"
}

_read_pass() {
  if [ -t 0 ]; then
    printf '密码: ' >&2
    stty -echo 2>/dev/null
    IFS= read -r _p || { stty echo 2>/dev/null; return 1; }
    stty echo 2>/dev/null
    printf '\n' >&2
    printf '%s' "$_p"
  else
    IFS= read -r _p || return 1
    printf '%s' "$_p"
  fi
}

cmd_init() {
  _stdin=0; _force=0
  for _a in "$@"; do
    case "$_a" in
      --stdin) _stdin=1 ;;
      --force) _force=1 ;;
    esac
  done
  # 幂等保护
  _cb="$(cfg_path_conf)/cred.bin"
  if [ -f "$_cb" ] && [ "$_force" -ne 1 ]; then
    echo "凭据已存在: $_cb（如需覆盖请加 --force）" >&2
    exit 1
  fi
  if [ "$_stdin" -eq 1 ]; then
    IFS= read -r _u || _u=""
    IFS= read -r _p || _p=""
  else
    _u=$(_read_user) || { echo "取消" >&2; exit 1; }
    _p=$(_read_pass) || { echo "取消" >&2; exit 1; }
  fi
  [ -n "$_u" ] || { echo "账号为空" >&2; exit 1; }
  [ -n "$_p" ] || { echo "密码为空" >&2; exit 1; }
  cred_save "$_u" "$_p"
  # 把实际用的存储模式报出来：本机没有 openssl 时会降级成 base64（等同明文），
  # 这种事必须让调用方看见，不能"保存成功"四个字糊过去。
  if [ "$(crypto_mode)" = "aes" ]; then
    echo "已加密保存（存储模式: AES-256-CBC）。用 verify 校验。" >&2
  else
    echo "已加密保存（存储模式: base64 —— 本机没有 openssl，安全强度等同明文，仅靠 600 权限保护）。用 verify 校验。" >&2
  fi
  exit 0
}

cmd_verify() {
  _cr=$(cred_load) || exit 1
  _u=$(printf '%s\n' "$_cr" | sed -n 's/^USER=//p')
  echo "凭据校验通过，账号: $(_mask "$_u")"
  exit 0
}

cmd_status() {
  _cf="$(cfg_path_conf)/cred.bin"
  _kf="$(cfg_path_key)/campus.key"
  echo "root      : $(cfg_root)"
  echo "conf 目录 : $(cfg_path_conf)"
  echo "key  目录 : $(cfg_path_key)"
  for _f in "$_cf" "$_kf"; do
    if [ -f "$_f" ]; then
      echo "$_f  存在  权限=$(ls -l "$_f" | awk '{print $1}')  指纹=$(crypto_fingerprint "$(cat "$_f")")"
    else
      echo "$_f  不存在"
    fi
  done
  exit 0
}

cmd_wipe() {
  _also=0
  case "$1" in --also-key) _also=1 ;; esac
  _cf="$(cfg_path_conf)/cred.bin"
  _kf="$(cfg_path_key)/campus.key"
  rm -f "$_cf"
  [ "$_also" -eq 1 ] && rm -f "$_kf"
  echo "已删除凭据（cred.bin=$([ -f "$_cf" ] && echo 仍在 || echo 已删)  key=$([ -f "$_kf" ] && echo 仍在 || echo 已删)）" >&2
  exit 0
}

case "${1:-}" in
  init)   shift; cmd_init "$@" ;;
  verify) cmd_verify ;;
  status) cmd_status ;;
  wipe)   shift; cmd_wipe "$@" ;;
  *)
    echo "用法: $0 init [--stdin] [--force] | verify | status | wipe [--also-key]" >&2
    exit 2 ;;
esac
