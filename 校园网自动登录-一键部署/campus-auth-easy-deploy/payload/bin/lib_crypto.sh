#!/bin/sh
# =============================================================================
# lib_crypto.sh — 凭据加解密模块
#
# 有两种模式，启动时自动选择：
#
#   [aes]  本机有 openssl
#      AES-256-CBC，密钥 32 字节随机（openssl rand 生成）；
#      IV 由密钥的 SHA-256 前 16 字节派生，避免额外存储；
#      「密钥与密文分离」：cred.bin 与 campus.key 分属不同目录、不同权限，
#      仅拿到其中一个无法还原明文。
#
#   [b64]  本机没有 openssl（**小米/红米原厂固件就是这种**）
#      退化成 base64 存放，**安全强度等同明文**，只靠文件权限（600 / 目录 700）保护。
#      cred.bin 首行为 `CAMPUS-CRED-B64` 标记，便于一眼看出是哪种模式。
#
# 为什么必须让 [b64] 存在（2026-09-18 实测记录）:
#   小米路由器 R4AV2 原厂固件 2.30.28 **不带 openssl**（busybox 也没有 openssl/aes
#   applet，只有 sha256sum），而 /data 与 /etc 共用同一个 1MB 的 mtdblock7 分区，
#   实测只剩 ~130KB 空闲，装不下 openssl-util。若不降级，凭据永远写不进去，
#   campus_login.sh 拿不到账号密码 —— 整个方案在这类机器上完全不可用。
#
#   **告警只在「真的要写凭据」时打一条（crypto_mode_notice），绝不静默，
#   也绝不在每次探测时重复打** —— 原因见 crypto_mode() 上方注释。
#   （2026-09-20 踩过：告警写在探测里，cron 每 2 分钟一条，/tmp 里 64KB 的
#    日志约 1.5 天就被同一条 warn 刷满，真正的登录成败记录全被挤掉。）
#
# 通用约束（两种模式都遵守）:
#   - 明文不进入命令行参数/文件/日志：一律经 stdin/stdout 管道传递；
#   - 密文文件权限 600（由 umask 077 保证）；
#   - 固有限制（接受并记录）: 同机同权限（root）的读取者两种模式都能还原。
#     客户端加密挡不住 root —— 见 docs/deployment.md「残余风险」。
#
# 对外接口:
#   crypto_mode                         输出 aes / b64（只探测，不写日志）
#   crypto_mode_notice                  若是 b64 模式则提示一次（仅在写凭据的路径调用）
#   crypto_require                      兼容旧接口；不再 exit，只做初始化
#   crypto_gen_key                      生成 32B 十六进制密钥（stdout）
#   crypto_encrypt <key> <明文>         stdout 输出单行密文
#   crypto_decrypt <key> <密文>         stdout 输出明文
#   crypto_encrypt_file <key> <密文路径>  stdin → 写密文文件（600）
#   crypto_decrypt_file <key> <密文路径>  读密文文件 → stdout 明文
#   crypto_fingerprint <密文串>          输出 sha256 前 12 位（用于状态展示，非密钥）
#
# 可用环境变量 CAMPUS_CRYPTO_MODE=aes|b64 强制指定模式（不设则自动探测）。
# 用途：本机自测、或换了固件后想固定住某种模式。强制 aes 但本机没有 openssl
# 会直接报错退出 —— 免得写进去的密文到时候谁也解不开。
# =============================================================================

CRYPTO_MODE="${CAMPUS_CRYPTO_MODE:-}"    # "" = 尚未探测；aes / b64
CRYPTO_B64_MARK="CAMPUS-CRED-B64"
CRYPTO_NOTICE_DONE=""                   # 同一进程内只提示一次

_crypto_warn() {
  if command -v log_warn >/dev/null 2>&1; then
    log_warn "$1"
  else
    echo "WARN: $1" >&2
  fi
}

# 只探测，**不产生任何日志**。
# 这一点很关键：cron 每 2 分钟就新起一个进程，cred_load → crypto_decrypt_file
# → crypto_mode，若在这里告警，日志会被同一条 warn 刷满（见文件头注释）。
# 降级提示改由 crypto_mode_notice 在「真的要写凭据」那一刻发一次。
crypto_mode() {
  if [ -z "$CRYPTO_MODE" ]; then
    if command -v openssl >/dev/null 2>&1; then
      CRYPTO_MODE=aes
    else
      CRYPTO_MODE=b64
    fi
  elif [ "$CRYPTO_MODE" = "aes" ] && ! command -v openssl >/dev/null 2>&1; then
    echo "ERROR: CAMPUS_CRYPTO_MODE=aes 但本机没有 openssl" >&2
    return 1
  fi
  printf '%s' "$CRYPTO_MODE"
}

# 降级提示：本机没 openssl（b64 模式）时提醒一次。
# 只在「写凭据」的路径上调用 —— 那种时候才会真的产生一份等同明文的密文，
# 用户此刻必须知道。日常登录（只读凭据）不该再提醒，否则就是刷屏。
crypto_mode_notice() {
  [ -z "${CRYPTO_NOTICE_DONE:-}" ] || return 0
  [ "$(crypto_mode)" = "b64" ] || return 0
  CRYPTO_NOTICE_DONE=1
  _crypto_warn "本机没有 openssl，凭据以 base64 存放（安全强度等同明文，仅靠 600 权限保护）"
}

# 兼容旧调用点：以前是「没有 openssl 就 exit 1」，现在只做探测（且不写日志）。
crypto_require() {
  crypto_mode >/dev/null
  return 0
}

crypto_gen_key() {
  _cgk_mode=$(crypto_mode) || return 1        # 强制 aes 但没有 openssl 时在这里就断掉
  case "$_cgk_mode" in
    aes) openssl rand -hex 32 2>/dev/null ;;
    # 无 openssl 时用 /dev/urandom + sha256sum 拿 64 个 hex 字符。
    # b64 模式下这个密钥其实不参与还原，生成它是为了保持文件布局与调用约定一致。
    b64) dd if=/dev/urandom bs=32 count=1 2>/dev/null | sha256sum 2>/dev/null | cut -c1-64 ;;
  esac
}

# IV := sha256(key) 前 16 字节（= 前 32 个 hex 字符）
crypto_iv() {
  printf '%s' "$1" | openssl dgst -sha256 2>/dev/null | sed -E 's/^.*= //' | cut -c1-32
}

crypto_encrypt() {
  _ce_key="$1"; _ce_plain="$2"
  _ce_mode=$(crypto_mode) || return 1
  case "$_ce_mode" in
    aes)
      printf '%s' "$_ce_plain" | openssl enc -aes-256-cbc -a -A \
        -K "$_ce_key" -iv "$(crypto_iv "$_ce_key")" 2>/dev/null ;;
    b64)
      printf '%s' "$_ce_plain" | base64 | tr -d '\n' ;;
  esac
}

crypto_decrypt() {
  _cd_key="$1"; _cd_b64="$2"
  _cd_mode=$(crypto_mode) || return 1
  case "$_cd_mode" in
    aes)
      printf '%s' "$_cd_b64" | openssl enc -d -aes-256-cbc -a -A \
        -K "$_cd_key" -iv "$(crypto_iv "$_cd_key")" 2>/dev/null ;;
    b64)
      printf '%s' "$_cd_b64" | tr -d ' \n' | base64 -d 2>/dev/null ;;
  esac
}

crypto_encrypt_file() {
  _cef_key="$1"; _cef_out="$2"
  _cef_mode=$(crypto_mode) || return 1
  # 唯一会产出密文的地方 —— 降级提示就在这里发，只此一次。
  crypto_mode_notice
  umask 077
  case "$_cef_mode" in
    aes)
      openssl enc -aes-256-cbc -a -A -K "$_cef_key" \
        -iv "$(crypto_iv "$_cef_key")" 2>/dev/null > "$_cef_out" ;;
    b64)
      # 首行写模式标记，其余是明文块的 base64。解密端据此判断，避免把
      # 两种模式的密文搞混（换固件/换机器时最容易踩）。
      { printf '%s\n' "$CRYPTO_B64_MARK"; base64; } > "$_cef_out" ;;
  esac
  chmod 600 "$_cef_out" 2>/dev/null
}

crypto_decrypt_file() {
  _cdf_key="$1"; _cdf_in="$2"
  _cdf_mode=$(crypto_mode) || return 1
  [ -f "$_cdf_in" ] || return 1
  case "$_cdf_mode" in
    aes)
      openssl enc -d -aes-256-cbc -a -A -K "$_cdf_key" \
        -iv "$(crypto_iv "$_cdf_key")" < "$_cdf_in" 2>/dev/null ;;
    b64)
      # 兼容两种情况：带标记的新格式，和不带标记的裸 base64。
      if head -n 1 "$_cdf_in" 2>/dev/null | grep -q "^$CRYPTO_B64_MARK\$"; then
        sed -n '2,$p' "$_cdf_in" | tr -d ' \n' | base64 -d 2>/dev/null
      else
        tr -d ' \n' < "$_cdf_in" | base64 -d 2>/dev/null
      fi ;;
  esac
}

crypto_fingerprint() {
  # BusyBox 可能无 sha256sum，逐级回退 md5sum / openssl dgst
  if command -v sha256sum >/dev/null 2>&1; then
    printf '%s' "$1" | sha256sum 2>/dev/null | cut -c1-12
  elif command -v md5sum >/dev/null 2>&1; then
    printf '%s' "$1" | md5sum 2>/dev/null | cut -c1-12
  else
    printf '%s' "$1" | openssl dgst -sha256 2>/dev/null | sed -E 's/^.*= //' | cut -c1-12
  fi
}
