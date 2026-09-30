#!/bin/sh
# =============================================================================
# lib_net.sh — 网络请求模块（curl 唯一封装点）
#
# 设计要点:
#   - 所有 curl 调用集中于此，统一超时（连接/总时长）与静默失败处理；
#   - 凭据禁止进入命令行：POST body 一律经 stdin（--data-binary @-）；
#   - 证书校验默认严格（该校验证书为公网有效 CA）；仅当 config 显式
#     net_insecure=1 才降级（并强制告警）；
#   - 只依赖系统 curl，无第三方库依赖；
#   - 每个函数只发一次请求，不内置轮询；重试由登录流程层控制。
#
# 实现注意: 不用 eval 拼 curl（-w 的 | 与引号会被误解析），用函数包装器。
#
# 对外接口:
#   net_init                读取超时/证书策略配置（先 cfg_load）
#   net_get <url>           GET 跟随重定向；body→stdout；设置 NET_CODE/NET_EFFURL/NET_ERR
#   net_post <url> <body>   POST；body 经 stdin；返回 http_code；设置 NET_CODE/NET_ERR
#   net_probe <url> [期望码] 在线探测；命中期望码(默认204)返回 0 否则 1
# =============================================================================

NET_CODE=""
NET_EFFURL=""
NET_ERR=""

net_init() {
  NET_CONNECT_T="${NET_CONNECT_T:-$(cfg_get net_connect_timeout)}"
  NET_CONNECT_T="${NET_CONNECT_T:-6}"
  NET_MAX_T="${NET_MAX_T:-$(cfg_get net_max_time)}"
  NET_MAX_T="${NET_MAX_T:-15}"
  NET_INSECURE="$(cfg_get net_insecure)"
  NET_INSECURE="${NET_INSECURE:-0}"
}

# curl 统一入口（函数包装器，参数原样透传，绝不 eval）
_net_curl() {
  if [ "$NET_INSECURE" = "1" ]; then
    log_warn "net_insecure=1: TLS 证书校验已关闭（仅门户证书自签时使用）"
    curl -sS -k "$@"
  else
    curl -sS "$@"
  fi
}

# GET（跟随重定向），body→stdout；设置 NET_CODE/NET_EFFURL/NET_ERR
net_get() {
  NET_CODE=""; NET_EFFURL=""; NET_ERR=""
  _ng_tmp=$(mktemp /tmp/ng.XXXXXX) 2>/dev/null || {
    NET_ERR="mktemp 失败"; return 1; }
  _ng_res=$(_net_curl -L --connect-timeout "$NET_CONNECT_T" \
    --max-time "$NET_MAX_T" -o "$_ng_tmp" \
    -w '%{http_code}|%{url_effective}' "$1" 2>/dev/null)
  _ng_rc=$?
  if [ "$_ng_rc" -ne 0 ]; then
    NET_ERR="curl 退出码 $_ng_rc"
    rm -f "$_ng_tmp"
    return 1
  fi
  NET_CODE=$(printf '%s' "$_ng_res" | cut -d'|' -f1)
  NET_EFFURL=$(printf '%s' "$_ng_res" | cut -d'|' -f2-)
  cat "$_ng_tmp"
  rm -f "$_ng_tmp"
  return 0
}

# POST：body 经 stdin（参数传入，不出现在 ps 命令行）；仅返回 http_code
net_post() {
  NET_CODE=""; NET_ERR=""
  _np_res=$(printf '%s' "$2" | _net_curl -X POST \
    --connect-timeout "$NET_CONNECT_T" --max-time "$NET_MAX_T" \
    -o /dev/null --data-binary @- -w '%{http_code}' "$1" 2>/dev/null)
  NET_CODE="$_np_res"
  [ -n "$_np_res" ] || NET_ERR="curl 无响应"
  printf '%s' "$_np_res"
}

# 在线探测：命中期望码(默认 204)返回 0，否则返回 1
net_probe() {
  _np_tmp=$(mktemp /tmp/np.XXXXXX) 2>/dev/null || return 1
  _res=$(_net_curl --connect-timeout "$NET_CONNECT_T" --max-time "$NET_MAX_T" \
    -o "$_np_tmp" -w '%{http_code}' "$1" 2>/dev/null)
  rm -f "$_np_tmp"
  NET_CODE="$_res"
  [ "$_res" = "${2:-204}" ]
}
