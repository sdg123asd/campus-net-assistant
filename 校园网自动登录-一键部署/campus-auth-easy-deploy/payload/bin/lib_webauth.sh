#!/bin/sh
# =============================================================================
# lib_webauth.sh — 门户认证协议模块
#
# 职责: 门户页抓取、隐藏字段解析、POST 报文构造、在线探测的编排语义。
# 协议事实（2026-09 实测 auth.gxstnu.edu.cn，tservertypeid=axe）:
#   - 拦截链: http://1.1.1.1 → (透明/302) → https://auth.gxstnu.edu.cn 表单页
#   - 主认证: POST /webauth.do?<urlParameter>（HTTPS，字段直传，无客户端哈希）
#   - 字段: userId, passwd, auth_type=0, pageid=5, templatetype=1,
#           wlanacip/wlanacname/wlanuserip/mac/vlan/url（urlParameter 与 body 均含）
#   - 无验证码；成功态返回 act=LOGINSUCC 页并 302 回 url
# 旧 axe 明文通道 userlogin.massrv 存在但弃用（HTTP 明文传密，不可用）。
#
# 对外接口（全部经 net_* / cfg_* / log_*）:
#   webauth_probe_online          在线探测（0=在线）
#   webauth_fetch_login_page      抓登录页，设置 WBAUTH_HTML/WBAUTH_HOST/WBAUTH_PAGEID 等
#   webauth_parse_fields          从 WBAUTH_HTML 解析隐藏字段 → WBAUTH_*
#   webauth_build_post_body <user> <pass>  构造 POST body（stdout）
#   webauth_build_post_url        构造 POST URL（stdout）
#   webauth_do_login <user> <pass> 完整一次登录尝试：抓页→解析→提交→复探
#                                 （重试由流程层负责）
# =============================================================================

WBAUTH_HTML=""
WBAUTH_HOST=""            # https://auth.gxstnu.edu.cn
WBAUTH_EFFURL=""
WBAUTH_URLPARAM=""        # wlanacip=..&wlanacname=..&...
WBAUTH_PAGEID=""
WBAUTH_FAIL="none"        # 最近一次登录尝试失败分类: none=成功|net=网络不可达|auth=认证被拒
                          # (流程层据此决定是否进入冷静期: 仅 auth 需冷静, net 为瞬断不惩罚)

webauth_probe_online() {
  net_probe "$(cfg_get probe_url)" "$(cfg_get probe_ok_code)"
}

# 抓取登录页（含重定向），成功后 WBAUTH_HTML/HOST/EFFURL/URLPARAM/PAGEID 就绪
webauth_fetch_login_page() {
  _wl_url="$(cfg_get portal_url)"
  [ -n "$_wl_url" ] || _wl_url="http://1.1.1.1/"
  WBAUTH_HTML=$(net_get "$_wl_url")
  [ -n "$WBAUTH_HTML" ] || { log_error "抓取登录页失败: $NET_ERR"; return 1; }
  WBAUTH_EFFURL="$NET_EFFURL"
  case "$NET_EFFURL" in
    https://*) WBAUTH_HOST=$(printf '%s' "$NET_EFFURL" | sed -E 's|^(https?://[^/]*).*|\1|') ;;
    *)        WBAUTH_HOST="https://auth.gxstnu.edu.cn" ;;
  esac
  webauth_parse_fields
}

# 从 HTML 中提取隐藏 input 的 value（原脚本同款正则，去重保留首个）
webauth_field() {
  _wf_id="$1"
  printf '%s' "$WBAUTH_HTML" | grep -o "id=\"$_wf_id\"[^>]*value=\"[^\"]*\"" |
    head -1 | sed -E 's/.*value="([^"]*)".*/\1/'
}

webauth_parse_fields() {
  WBAUTH_URLPARAM=""
  _wf_userip=$(webauth_field wlanuserip)
  _wf_acip=$(webauth_field wlanacip)
  _wf_acname=$(webauth_field wlanacname)
  _wf_mac=$(webauth_field mac)
  _wf_vlan=$(webauth_field vlan)
  WBAUTH_PAGEID=$(webauth_field pageid)
  # urlParameter 表单隐藏域优先；否则由字段拼装
  _wf_urltxt=$(printf '%s' "$WBAUTH_HTML" | grep -o "id=\"urlParameter\"[^>]*value=\"[^\"]*\"" |
    head -1 | sed -E 's/.*value="([^"]*)".*/\1/')
  if [ -n "$_wf_urltxt" ]; then
    WBAUTH_URLPARAM="$_wf_urltxt"
  else
    WBAUTH_URLPARAM="wlanacip=${_wf_acip}&wlanacname=${_wf_acname}&wlanuserip=${_wf_userip}&mac=${_wf_mac}&vlan=${_wf_vlan}&url=$(cfg_get portal_url)"
  fi
  [ -n "$_wf_userip" ] || log_warn "未解析到 wlanuserip（可能页面结构变化）"
}

webauth_build_post_url() {
  printf '%s/webauth.do?%s' "$WBAUTH_HOST" "$WBAUTH_URLPARAM"
}

# body 经函数返回（stdout），密码始终不落盘、不进 argv
webauth_build_post_body() {
  _wb_user="$1"; _wb_pass="$2"
  _wb_acip=$(webauth_field wlanacip)
  _wb_acname=$(webauth_field wlanacname)
  _wb_userip=$(webauth_field wlanuserip)
  _wb_mac=$(webauth_field mac)
  _wb_vlan=$(webauth_field vlan)
  _wb_pid="${WBAUTH_PAGEID:-5}"
  printf 'userId=%s&passwd=%s&auth_type=0&pageid=%s&wlanacip=%s&wlanacname=%s&wlanuserip=%s&mac=%s&vlan=%s&url=%s&templatetype=1' \
    "$_wb_user" "$_wb_pass" "$_wb_pid" "$_wb_acip" "$_wb_acname" \
    "$_wb_userip" "$_wb_mac" "$_wb_vlan" "$(cfg_get portal_url)"
}

# 单次登录尝试（不含重试）；成功（复探在线）返回 0。
# 失败分类写入 WBAUTH_FAIL: net=抓登录页即失败(网络不可达/瞬断), auth=可达但认证被拒。
webauth_do_login() {
  _dl_user="$1"; _dl_pass="$2"
  WBAUTH_FAIL="none"
  if ! webauth_fetch_login_page; then
    WBAUTH_FAIL="net"
    return 1
  fi
  _dl_url=$(webauth_build_post_url)
  _dl_body=$(webauth_build_post_body "$_dl_user" "$_dl_pass")
  log_info "提交认证: $WBAUTH_HOST/webauth.do (字段已就绪, pageid=$WBAUTH_PAGEID)"
  _dl_resp=$(net_post "$_dl_url" "$_dl_body")
  log_debug "POST http_code=$NET_CODE"
  # 轻微收敛后再复探，避免门户处理异步
  sleep 1
  if webauth_probe_online; then
    log_info "认证成功，已恢复在线"
    return 0
  fi
  WBAUTH_FAIL="auth"
  log_warn "提交后仍未在线 (probe=$NET_CODE)"
  return 1
}
