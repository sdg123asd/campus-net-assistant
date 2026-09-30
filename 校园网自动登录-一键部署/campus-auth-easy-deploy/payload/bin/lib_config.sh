#!/bin/sh
# =============================================================================
# lib_config.sh — 配置与凭据管理模块
#
# 目录约定（默认以本文件所在父目录为根，部署时保持一致即可）:
#   <root>/bin/          可执行模块与入口
#   <root>/conf/config.ini   非敏感参数（明文，600）
#   <root>/conf/cred.bin     凭据存放（600）。两种形态，见 lib_crypto.sh：
#                            有 openssl → AES-256-CBC + base64；
#                            没 openssl → 首行 CAMPUS-CRED-B64 标记 + base64（等同明文）
#   <root>/key/campus.key    32B 随机密钥（600），与密文分目录存放
#   可用环境变量 CAMPUS_ROOT 覆盖根目录（测试时指向临时目录）。
#
# 对外接口:
#   cfg_root                    输出根目录（stdout）
#   cfg_path_conf               输出 conf 目录
#   cfg_path_key                输出 key 目录
#   cfg_default <name> <val>    声明带默认值的配置项（供入口批量装载）
#   cfg_load                    载入 config.ini 到 CFG_<name>
#   cfg_get  <name>             输出 CFG_<name>（未载入返回空）
#   cred_ensure_key             密钥存在则输出其内容；否则生成并落盘（600）
#   cred_load                   解密 cred.bin，输出 "USER=..\nPASS=.."（调用方承接）
#   cred_save <user> <pass>     加密写盘（user/pass 经参数传入，勿出现在命令行）
#
# 调用约定: 使用本模块前必须先 source lib_log.sh 与 lib_crypto.sh。
# =============================================================================

CONF_VERSION="1"

_self_dir() {
  _sd=$(CDPATH= cd -- "$(dirname -- "$0")" 2>/dev/null && pwd)
  printf '%s' "$_sd"
}

cfg_root() {
  if [ -n "$CAMPUS_ROOT" ]; then
    printf '%s' "$CAMPUS_ROOT"
  else
    _sr=$(_self_dir)
    case "$_sr" in
      */bin) printf '%s' "${_sr%/bin}" ;;
      *)     printf '%s' "$_sr" ;;
    esac
  fi
}

cfg_path_conf() { printf '%s/conf' "$(cfg_root)"; }
cfg_path_key()  { printf '%s/key'  "$(cfg_root)"; }

CFG_DEFAULTS=""

cfg_default() {
  # 仅登记，不直接 export，避免污染
  CFG_DEFAULTS="$CFG_DEFAULTS $1=$2"
}

cfg_get() {
  eval "printf '%s' \"\$CFG_$1\""
}

cfg_load() {
  _cl_root=$(cfg_root)
  _cl_cfg="$_cl_root/conf/config.ini"
  # 先注入默认值
  for _d in $CFG_DEFAULTS; do
    eval "CFG_${_d%%=*}=\"${_d#*=}\""
  done
  [ -f "$_cl_cfg" ] || return 0
  # 注意: BusyBox ash 中「while read < file」与循环体内 $(cmd) 会共享 fd 导致
  # 读取错位，因此先把整文件载入内存，再用 heredoc 喂给循环。
  _cl_data=$(cat "$_cl_cfg") || return 0
  while IFS='=' read -r _k _v; do
    case "$_k" in
      ''|\#*) continue ;;
    esac
    # 注意: BusyBox tr/sed 对 [:space:] 类支持不稳定，这里只处理空格与制表符
    _k=$(printf '%s' "$_k" | tr -d ' \t')
    _v=$(printf '%s' "$_v" | sed 's/^[ \t]*//; s/[ \t]*$//')
    [ -n "$_k" ] || continue
    eval "CFG_${_k}=\"$_v\""
  done <<EOF
$_cl_data
EOF
}

cred_ensure_key() {
  _ck_dir=$(cfg_path_key)
  _ck_file="$_ck_dir/campus.key"
  mkdir -p "$_ck_dir" 2>/dev/null || {
    log_error "无法创建密钥目录 $_ck_dir"
    return 1
  }
  if [ -f "$_ck_file" ]; then
    _ck_key=$(cat "$_ck_file" 2>/dev/null)
    case "$_ck_key" in
      *[!0-9a-fA-F]*) log_error "密钥文件内容非法（非 hex）: $_ck_file" ; return 1 ;;
      "") log_error "密钥文件为空: $_ck_file" ; return 1 ;;
    esac
    printf '%s' "$_ck_key"
    return 0
  fi
  _ck_key=$(crypto_gen_key) || return 1
  umask 077
  printf '%s\n' "$_ck_key" > "$_ck_file"
  log_info "已生成新密钥: $_ck_file"
  printf '%s' "$_ck_key"
}

# 解密并打印明文凭据（含校验）。格式 USER=.. / PASS=..
cred_load() {
  _cl_root=$(cfg_root)
  _cl_bin="$_cl_root/conf/cred.bin"
  [ -f "$_cl_bin" ] || { log_error "凭据密文不存在: $_cl_bin（先运行 cred_admin.sh init）"; return 1; }
  _cl_key=$(cred_ensure_key) || return 1
  _cl_plain=$(crypto_decrypt_file "$_cl_key" "$_cl_bin")
  case "$_cl_plain" in
    "CAMPUS-CRED-V1"*) : ;;
    *) log_error "凭据解密失败或格式非法（密钥不匹配/文件损坏？）" ; return 1 ;;
  esac
  _cl_user=$(printf '%s' "$_cl_plain" | sed -n 's/^USER=//p')
  _cl_pass=$(printf '%s' "$_cl_plain" | sed -n 's/^PASS=//p')
  [ -n "$_cl_user" ] && [ -n "$_cl_pass" ] || {
    log_error "凭据字段缺失（USER 或 PASS 为空）"
    return 1
  }
  printf 'USER=%s\nPASS=%s\n' "$_cl_user" "$_cl_pass"
}

# 参数传明文，由调用方保证两值不出现在本进程命令行
cred_save() {
  _cs_user="$1"; _cs_pass="$2"
  [ -n "$_cs_user" ] || { log_error "cred_save: user 为空"; return 1; }
  [ -n "$_cs_pass" ] || { log_error "cred_save: pass 为空"; return 1; }
  _cs_root=$(cfg_root)
  _cs_bin="$_cs_root/conf/cred.bin"
  mkdir -p "$(cfg_path_conf)" 2>/dev/null || return 1
  _cs_key=$(cred_ensure_key) || return 1
  # 格式: 首行版本标记，随后 USER=/PASS= 两行
  _cs_plain="CAMPUS-CRED-V1
USER=$_cs_user
PASS=$_cs_pass"
  crypto_encrypt_file "$_cs_key" "$_cs_bin" <<EOF
$_cs_plain
EOF
  umask 077
  chmod 600 "$_cs_bin" 2>/dev/null
  log_info "凭据已加密写入: $_cs_bin"
  log_secret_add "$_cs_pass"
}
