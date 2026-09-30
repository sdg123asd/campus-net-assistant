#!/bin/sh
# =============================================================================
# test_payload.sh — 路由器侧 payload 的本机回归测试
#
# 用法（Windows 上用 Git 自带的 bash）:
#   "C:\Program Files\Git\bin\bash.exe" tests/test_payload.sh
#
# 覆盖：
#   1. 全部 .sh 的 POSIX 语法
#   2. lib_crypto 两种模式（aes / b64）的加解密往返 + 边界字符
#   3. cred_admin.sh init --stdin → verify 的端到端链路（两种模式）
#
# 为什么需要它：payload 是跑在路由器上的 POSIX sh，本机改完没法直接试。
# 这一套能在本机把绝大多数改动提前验掉 —— 2026-09-18 就靠它抓到了
# crypto_decrypt_file 里把变量名写成 $_CRYPTO_B64_MARK 的静默解错 bug。
#
# 注意：PATH 必须显式给，否则这个执行环境里连 dirname/head 都找不到。
# =============================================================================
export PATH="/usr/bin:/bin:/c/Windows/System32:/c/Windows:${PATH:-}"

HERE=$(cd "$(dirname "$0")" && pwd)
PROJ=$(cd "$HERE/.." && pwd)
PAYLOAD="$PROJ/campus-auth-easy-deploy/payload"
WORK="${TMPDIR:-/tmp}/campus_payload_test.$$"

fail=0
ok()  { echo "  [OK]   $1"; }
bad() { echo "  [FAIL] $1"; fail=$((fail+1)); }
chk() { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (期望 [$3] 实得 [$2])"; fi; }

echo "payload = $PAYLOAD"
[ -d "$PAYLOAD" ] || { echo "找不到 payload 目录"; exit 1; }
rm -rf "$WORK"; mkdir -p "$WORK"

# ---------------------------------------------------------------- 1. 语法
echo
echo "== 1. 语法（sh -n） =="
for f in "$PAYLOAD"/bin/*.sh; do
  if sh -n "$f" 2>"$WORK/err.txt"; then
    ok "$(basename "$f")"
  else
    bad "$(basename "$f")"
    sed 's/^/         /' "$WORK/err.txt"
  fi
done

# 去掉 CRLF 的副本，后面所有用例都用它（路由器上是 LF，但 Windows 上编辑容易带 CRLF）
STAGE="$WORK/stage"
mkdir -p "$STAGE/bin" "$STAGE/conf"
for f in "$PAYLOAD"/bin/*.sh "$PAYLOAD"/conf/*.ini; do
  tr -d '\r' < "$f" > "$STAGE/$(basename "$(dirname "$f")")/$(basename "$f")"
done
chmod 755 "$STAGE"/bin/*.sh

# ---------------------------------------------------------------- 2. 加解密
echo
echo "== 2. lib_crypto 往返 =="
. "$STAGE/bin/lib_crypto.sh"
HAS_OPENSSL=$(command -v openssl >/dev/null 2>&1 && echo yes || echo no)
echo "  本机 openssl: $HAS_OPENSSL"

for M in b64 aes; do
  [ "$M" = "aes" ] && [ "$HAS_OPENSSL" = "no" ] && { echo "  ($M 跳过：本机没 openssl)"; continue; }
  echo "  --- 模式 $M ---"
  CRYPTO_MODE="$M"
  chk "  crypto_mode=$M" "$(crypto_mode)" "$M"
  K=$(crypto_gen_key)
  chk "  密钥 64 hex" "${#K}" "64"

  printf 'CAMPUS-CRED-V1\nUSER=2023000000\nPASS=TestPass123\n' > "$WORK/p.txt"
  crypto_encrypt_file "$K" "$WORK/c.bin" < "$WORK/p.txt"
  chk "  文件往返一致" "$(crypto_decrypt_file "$K" "$WORK/c.bin")" "$(cat "$WORK/p.txt")"

  SP='p@ss w'"'"'ord"$!中文%&*()'
  chk "  字符串往返（特殊字符/中文）" \
      "$(crypto_decrypt "$K" "$(crypto_encrypt "$K" "$SP")")" "$SP"

  i=0; : > "$WORK/long.txt"
  while [ $i -lt 40 ]; do
    printf 'CAMPUS-CRED-V1 line %s padding padding padding\n' "$i" >> "$WORK/long.txt"
    i=$((i+1))
  done
  crypto_encrypt_file "$K" "$WORK/cl.bin" < "$WORK/long.txt"
  chk "  长内容往返（40 行）" "$(crypto_decrypt_file "$K" "$WORK/cl.bin")" "$(cat "$WORK/long.txt")"

  if [ "$M" = "b64" ]; then
    chk "  cred.bin 首行是模式标记" "$(head -n 1 "$WORK/c.bin")" "CAMPUS-CRED-B64"
    printf 'CAMPUS-CRED-V1\nUSER=x\nPASS=y\n' | base64 > "$WORK/bare.bin"
    chk "  裸 base64 也能解（向后兼容）" \
        "$(crypto_decrypt_file "$K" "$WORK/bare.bin")" "$(printf 'CAMPUS-CRED-V1\nUSER=x\nPASS=y')"
  fi
done

# ---------------------------------------------------------------- 3. 端到端
echo
echo "== 3. cred_admin.sh 端到端 =="
for M in b64 aes; do
  [ "$M" = "aes" ] && [ "$HAS_OPENSSL" = "no" ] && { echo "  ($M 跳过：本机没 openssl)"; continue; }
  RT="$WORK/rt_$M"
  mkdir -p "$RT"
  cp -r "$STAGE/bin" "$STAGE/conf" "$RT/"

  OUT=$(CAMPUS_ROOT="$RT" CAMPUS_CRYPTO_MODE="$M" "$RT/bin/cred_admin.sh" init --force --stdin 2>&1 <<'EOF'
2023000000
TestPass123
EOF
)
  echo "    $OUT"
  case "$OUT" in *"已加密保存"*) ok "  [$M] init 成功" ;; *) bad "  [$M] init 失败" ;; esac
  case "$M:$OUT" in
    b64:*base64*)  ok "  [$M] 降级有明确告警" ;;
    aes:*AES-256*) ok "  [$M] 报的是 AES" ;;
  esac

  VB=$(CAMPUS_ROOT="$RT" CAMPUS_CRYPTO_MODE="$M" "$RT/bin/cred_admin.sh" verify 2>&1)
  case "$VB" in *"凭据校验通过"*) ok "  [$M] verify 通过" ;; *) bad "  [$M] verify 失败: $VB" ;; esac

  PL=$(CAMPUS_ROOT="$RT" CAMPUS_CRYPTO_MODE="$M" sh -c \
       '. '"$RT"'/bin/lib_log.sh; . '"$RT"'/bin/lib_crypto.sh; . '"$RT"'/bin/lib_config.sh; cred_load')
  case "$PL" in
    *"USER=2023000000"*"PASS=TestPass123"*) ok "  [$M] 账号密码原样还原" ;;
    *) bad "  [$M] 还原内容不符: $PL" ;;
  esac
done

# ---------------------------------------------------------------- 4. 强制模式
echo
echo "== 4. CAMPUS_CRYPTO_MODE 强制生效 =="
M=$(CAMPUS_CRYPTO_MODE=b64 sh -c ". '$STAGE/bin/lib_crypto.sh'; crypto_mode")
chk "  强制 b64 被尊重" "$M" "b64"

# ---------------------------------------------------------------- 5. 日志不刷屏
echo
echo "== 5. 降级告警只在写凭据时出现（防日志刷屏） =="
# 背景（2026-09-20 实测）: cron 每 2 分钟起一个新进程，走 cred_load（只读）
# → crypto_decrypt_file → crypto_mode。降级告警若写在 crypto_mode 里，
# /tmp 那个 64KB 的日志约 1.5 天就会被同一条 warn 填满，真正的登录成败
# 记录全被挤掉。所以告警必须只在「写凭据」路径（crypto_encrypt_file）发一次。
LT="$WORK/logtest"
mkdir -p "$LT"
cp -r "$STAGE/bin" "$STAGE/conf" "$LT/"
LFB="/tmp/campus_logtest.log"
rm -f "$LFB"

# 探针脚本：$1=bin 目录  $2=日志文件  $3=save|read
# 写成独立文件而不是 sh -c '...多行...' —— 后者在这台机器的执行环境里会被
# 安全层连带把外层脚本一起干掉（bash -x 追到那一行就没了，rc=1）。
cat > "$LT/probe.sh" <<'EOS'
B="$1"; L="$2"
. "$B/lib_log.sh"
. "$B/lib_crypto.sh"
. "$B/lib_config.sh"
log_init warn "$L"
case "$3" in
  save) cred_save 2023000000 TestPass123 >/dev/null 2>&1 ;;
  read) i=0; while [ $i -lt 5 ]; do cred_load >/dev/null 2>&1; i=$((i+1)); done ;;
esac
EOS

# (a) 写一次凭据（会产生告警），清空日志，再模拟 5 个 cron 周期的「只读」
CAMPUS_ROOT="$LT" CAMPUS_CRYPTO_MODE=b64 sh "$LT/probe.sh" "$LT/bin" "$LFB" save
: > "$LFB"
CAMPUS_ROOT="$LT" CAMPUS_CRYPTO_MODE=b64 sh "$LT/probe.sh" "$LT/bin" "$LFB" read
chk "  只读凭据 5 次零日志" "$(wc -l < "$LFB" 2>/dev/null | tr -d ' ')" "0"

# (b) 写凭据时必须留下恰好一条降级告警（不能静默）
CAMPUS_ROOT="$LT" CAMPUS_CRYPTO_MODE=b64 sh "$LT/probe.sh" "$LT/bin" "$LFB" save
chk "  写凭据时恰好 1 条降级告警" \
    "$(grep -c '没有 openssl' "$LFB" 2>/dev/null)" "1"
rm -f "$LFB"

rm -rf "$WORK"
echo
if [ "$fail" -eq 0 ]; then echo "结果：全部通过"; else echo "结果：失败 $fail 项"; fi
exit $fail
