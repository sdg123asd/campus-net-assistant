#Requires -Version 5.1
<#
============================================================
 校园网自动登录 · 一键部署工具
 ------------------------------------------------------------
 作用：把"自动登录校园网"的小程序装到你的小米路由器上。
       装好之后，路由器会每 2 分钟自己检查一次网络，
       一旦发现需要登录，就自动用你的账号密码登录。
 ------------------------------------------------------------
 用法：双击同目录的「一键部署.bat」即可（推荐）。
       也可以在 PowerShell 里直接跑这个脚本。
 ------------------------------------------------------------
 可选：在同目录放一个 deploy.ini，可免去手输路由器信息：
       RouterHost=192.168.31.1
       SshUser=root
       SshPassword=你的路由器密码
       （校园网账号密码出于安全考虑，不放进 ini）
============================================================
#>
[CmdletBinding()]
param(
    [string]$RouterHost,
    [string]$SshUser,
    [string]$SshPassword,
    [string]$CampusUser,
    [string]$CampusPassword,
    [string]$Port = '22',
    [switch]$DryRun,
    [switch]$SkipLoginTest,
    [switch]$Yes
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

# ---------- 路径 ----------
$Script:Root     = $PSScriptRoot
$Script:Payload  = Join-Path $Script:Root 'payload'
$Script:RemoteDir = '/data/campus/v2'
$Script:HostKeyFile = Join-Path $Script:Root 'hostkey.txt'
$Script:HostKey = $null
$Script:StepNow = 0
$Script:StepAll = 6
$Script:Warned = 0

# ---------- 输出助手 ----------
function Write-Bar { param([string]$c = '=') Write-Host ($c * 62) -ForegroundColor DarkCyan }
function Write-Title {
    param([string]$t)
    Write-Host ''
    Write-Bar
    Write-Host "  $t" -ForegroundColor Cyan
    Write-Bar
}
function Write-Step {
    param([string]$t)
    $Script:StepNow++
    Write-Host ''
    Write-Host ("[{0}/{1}] {2}" -f $Script:StepNow, $Script:StepAll, $t) -ForegroundColor Yellow
}
function Write-Ok   { param([string]$t) Write-Host "      √ $t" -ForegroundColor Green }
function Write-Info { param([string]$t) Write-Host "      · $t" -ForegroundColor Gray }
function Write-Warn2{ param([string]$t) $Script:Warned++; Write-Host "      ! $t" -ForegroundColor DarkYellow }
function Fail {
    param([string]$t, [string]$hint = '')
    Write-Host ''
    Write-Host "  × $t" -ForegroundColor Red
    if ($hint) { Write-Host $hint -ForegroundColor DarkYellow }
    Write-Host ''
    exit 1
}

# ---------- 参数引用（Windows 命令行转义） ----------
function Quote-Arg {
    param([string]$a)
    if ($null -eq $a) { return '""' }
    if ($a -match '[\s"]') {
        $e = $a -replace '(\\*)"', '$1$1\"'
        $e = $e -replace '(\\+)$', '$1$1'
        return '"' + $e + '"'
    }
    return $a
}

# ---------- 找 plink / pscp ----------
function Find-Tool {
    param([string]$name)
    $cands = @(
        (Join-Path $Script:Root "tools\$name.exe"),
        (Join-Path $Script:Root "$name.exe"),
        (Join-Path $env:ProgramFiles "PuTTY\$name.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "PuTTY\$name.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\PuTTY\$name.exe"),
        (Join-Path $env:USERPROFILE "scoop\apps\putty\current\$name.exe")
    )
    foreach ($c in $cands) {
        if ($c -and (Test-Path $c)) { return $c }
    }
    $cmd = Get-Command "$name.exe" -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

# ---------- 进程调用（精确控制 stdin / 输出编码） ----------
function Invoke-Exe {
    param(
        [Parameter(Mandatory)][string]$Exe,
        [Parameter(Mandatory)][string[]]$ArgList,
        [string]$Stdin,
        [switch]$HasStdin
    )
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $Exe
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.RedirectStandardInput = [bool]$HasStdin
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    $psi.StandardOutputEncoding = $utf8
    $psi.StandardErrorEncoding = $utf8
    $psi.Arguments = (($ArgList | ForEach-Object { Quote-Arg $_ }) -join ' ')

    if ($DryRun) {
        $show = $psi.Arguments
        if ($show.Length -gt 96) { $show = $show.Substring(0, 93) + '...' }
        Write-Info ("[演练] " + [System.IO.Path]::GetFileName($Exe) + " " + $show)
        return [pscustomobject]@{ Code = 0; Out = ''; Err = '' }
    }

    $p = [System.Diagnostics.Process]::Start($psi)
    if ($HasStdin) {
        $p.StandardInput.Write($Stdin)
        $p.StandardInput.Close()
    }
    $out = $p.StandardOutput.ReadToEnd()
    $err = $p.StandardError.ReadToEnd()
    $p.WaitForExit()
    # 注意: ExitCode 必须在 Dispose 之前取出, 否则释放后读到 null
    $code = $p.ExitCode
    $p.Dispose()
    return [pscustomobject]@{ Code = $code; Out = $out; Err = $err }
}

function Invoke-Plink {
    param([string[]]$ArgList, [string]$Stdin, [switch]$HasStdin)
    $base = @('-ssh', '-batch', '-P', $Script:Port)
    if ($Script:HostKey) { $base += @('-hostkey', $Script:HostKey) }
    $base += @('-pw', $Script:SshPassword, "$Script:SshUser@$Script:RouterHost")
    if ($HasStdin) { return Invoke-Exe -Exe $Script:Plink -ArgList ($base + $ArgList) -Stdin $Stdin -HasStdin }
    return Invoke-Exe -Exe $Script:Plink -ArgList ($base + $ArgList)
}

function Send-File {
    param([string]$Local, [string]$Remote)
    $a = @('-scp', '-batch', '-P', $Script:Port)
    if ($Script:HostKey) { $a += @('-hostkey', $Script:HostKey) }
    $a += @('-pw', $Script:SshPassword, $Local, "$Script:SshUser@$Script:RouterHost`:$Remote")
    return Invoke-Exe -Exe $Script:Pscp -ArgList $a
}

# ---------- 从输出里抓主机指纹 ----------
function Get-HostKey {
    param([string]$Text)
    if (-not $Text) { return $null }
    $m = [regex]::Match($Text, '(ssh-\S+)\s+(\d+)\s+(SHA256:[A-Za-z0-9+/=]+)')
    if ($m.Success) { return ($m.Groups[1].Value + ' ' + $m.Groups[2].Value + ' ' + $m.Groups[3].Value) }
    return $null
}

# ---------- 读不回显的密码 ----------
function Read-Secret {
    param([string]$Prompt)
    $sec = Read-Host -Prompt $Prompt -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}

# ============================================================
#  开场
# ============================================================
try { Clear-Host } catch { }
Write-Host ''
Write-Bar
Write-Host '   校园网自动登录 · 一键部署' -ForegroundColor Cyan
Write-Host '   给路由器装一个"自动帮你登录校园网"的小助手' -ForegroundColor Gray
Write-Bar
Write-Host ''
Write-Host '  它会自动完成 6 件事：' -ForegroundColor White
Write-Host '    1. 连上你的路由器          4. 把校园网账号密码加密存好'
Write-Host '    2. 上传程序文件            5. 设置定时任务 + 装一个总开关'
Write-Host '    3. 设置好文件权限          6. 立刻测试一遍'
Write-Host ''
Write-Host '  总开关是干什么的？' -ForegroundColor Yellow
Write-Host '    学校若限制「一个账号同时只能一台设备在线」，路由器开着自动登录时，' -ForegroundColor Gray
Write-Host '    你在教室用同一账号上网会被它顶掉。有了开关，离开宿舍点一下「关闭」即可。' -ForegroundColor Gray

if (-not (Test-Path $Script:Payload)) {
    Fail "找不到 payload 文件夹（程序文件）" "请确认你解压了整个压缩包，并且没有单独移动 deploy.ps1。"
}

# ---------- 读取可选的 deploy.ini ----------
$iniPath = Join-Path $Script:Root 'deploy.ini'
$ini = @{}
if (Test-Path $iniPath) {
    try {
        Get-Content -LiteralPath $iniPath -Encoding UTF8 | ForEach-Object {
            if ($_ -match '^\s*([^#;][^=]*?)\s*=\s*(.*)$') {
                $ini[$matches[1].Trim()] = $matches[2].Trim()
            }
        }
        Write-Host ''
        Write-Info "已读取 deploy.ini（路由器信息免输入）"
    } catch {
        Write-Warn2 "deploy.ini 读取失败，已忽略：$($_.Exception.Message)"
    }
}
function Ini-Get { param([string]$k) if ($ini.ContainsKey($k) -and $ini[$k]) { return $ini[$k] } return $null }

if (-not $RouterHost)     { $RouterHost = Ini-Get 'RouterHost' }
if (-not $SshUser)        { $SshUser = Ini-Get 'SshUser' }
if (-not $SshPassword)    { $SshPassword = Ini-Get 'SshPassword' }
if (-not $Port -or $Port -eq '22') { $p = Ini-Get 'Port'; if ($p) { $Port = $p } }
if (-not $RouterHost)     { $RouterHost = '192.168.31.1' }
if (-not $SshUser)        { $SshUser = 'root' }

# ============================================================
#  收集输入
# ============================================================
Write-Host ''
Write-Bar '-'
Write-Host '  请按提示输入。直接按回车 = 使用方括号里的默认值。'
Write-Bar '-'

if (-not $Yes -or -not $RouterHost) {
    $v = Read-Host "  路由器地址 [$RouterHost]"
    if ($v) { $RouterHost = $v.Trim() }
}
if (-not $Yes) {
    $v = Read-Host "  路由器登录用户 [$SshUser]"
    if ($v) { $SshUser = $v.Trim() }
}
if (-not $SshPassword) {
    Write-Host '  （这是登录路由器用的密码，输入时不会显示）' -ForegroundColor DarkGray
    $SshPassword = Read-Secret "  路由器登录密码"
    if (-not $SshPassword) { Fail '路由器密码不能为空' }
}

Write-Host ''
Write-Host '  ——— 下面是你的校园网上网账号 ———' -ForegroundColor White
if (-not $CampusUser) {
    $CampusUser = (Read-Host '  校园网账号（学号）').Trim()
    if (-not $CampusUser) { Fail '校园网账号不能为空' }
}
if (-not $CampusPassword) {
    Write-Host '  （输入时不会显示）' -ForegroundColor DarkGray
    $CampusPassword = Read-Secret '  校园网密码'
    if (-not $CampusPassword) { Fail '校园网密码不能为空' }
}

# ---------- 汇总确认 ----------
$routeTxt = $RouterHost + ':' + $Port
$userMask = $CampusUser
if ($CampusUser.Length -gt 1) { $userMask = $CampusUser.Substring(0, 1) + '***' }
Write-Host ''
Write-Bar
Write-Host '  请确认一下：'
Write-Host ("    路由器        : {0}" -f $routeTxt)
Write-Host ("    登录用户      : {0}" -f $SshUser)
Write-Host ("    路由器密码    : （已输入，共 {0} 位）" -f $SshPassword.Length)
Write-Host ("    校园网账号    : {0}（{1}）" -f $CampusUser, $userMask) -ForegroundColor Yellow
Write-Host ("    校园网密码    : （已输入，共 {0} 位）" -f $CampusPassword.Length)
Write-Bar
if (-not $Yes) {
    $go = Read-Host '  确认无误，开始部署吗？(Y/n)'
    if ($go -and $go.ToLower() -ne 'y' -and $go.ToLower() -ne 'yes') { Write-Host '  已取消。' ; exit 0 }
}

# ============================================================
#  找工具
# ============================================================
Write-Title '准备工作：查找上传工具'
$Script:Plink = Find-Tool 'plink'
$Script:Pscp  = Find-Tool 'pscp'
if (-not $Script:Plink -or -not $Script:Pscp) {
    $miss = @()
    if (-not $Script:Plink) { $miss += 'plink.exe' }
    if (-not $Script:Pscp)  { $miss += 'pscp.exe' }
    Fail ("缺少上传工具：" + ($miss -join '、')) @"
  这两个小程序属于 PuTTY 工具包，用来把你的电脑和路由器连起来。
  解决办法（二选一）：
    A. 你已经装过 PuTTY：把  plink.exe 和 pscp.exe  复制到本文件夹的 tools 里
    B. 还没装：到 https://www.putty.org 下载安装包，装好后重跑本脚本
"@
}
Write-Ok "找到 plink: $Script:Plink"
Write-Ok "找到 pscp : $Script:Pscp"

# ============================================================
#  第 1 步：连上路由器
# ============================================================
Write-Step "连接路由器 ($routeTxt)"

$hadKey = $false
if (Test-Path $Script:HostKeyFile) {
    $saved = (Get-Content -LiteralPath $Script:HostKeyFile -Raw).Trim()
    if ($saved) { $Script:HostKey = $saved ; $hadKey = $true ; Write-Info "使用上次记录的路由器指纹" }
}

$conn = $null
if (-not $DryRun) {
    if ($Script:HostKey) { $conn = Invoke-Plink -ArgList @('echo READY') }
    if (-not $conn -or ($conn.Out -notmatch 'READY')) {
        # 不带指纹试一次：若路由器重装过系统，指纹会变，plink 会把新指纹打印出来
        $probe = Invoke-Plink -ArgList @('echo READY')
        $probeText = "$($probe.Out)`n$($probe.Err)"
        if ($probeText -match 'READY') {
            $conn = $probe
        } else {
            $newFp = Get-HostKey $probeText
            if ($newFp) {
                if ($hadKey) {
                    Write-Warn2 '路由器身份指纹变了（路由器重装过系统/被重置时属正常），已自动更新'
                } else {
                    Write-Info '第一次连接这台路由器，已记住它的身份指纹'
                }
                $Script:HostKey = $newFp
                try { Set-Content -LiteralPath $Script:HostKeyFile -Value $newFp -Encoding ASCII } catch { }
                $conn = Invoke-Plink -ArgList @('echo READY')
            } else {
                $all = $probeText
                if ($all -match 'Access denied|Authentication failed|password') {
                    Fail '路由器密码不对（或用户名不是 root）' '  请确认你平时登录路由器用的密码，然后重跑本脚本。'
                }
                if ($all -match 'Connection refused|No route|timed out|Network error|Unable to open') {
                    Fail "连不上路由器 $routeTxt" @"
  常见原因：
    1. 你的电脑没有连到这个路由器的 Wi-Fi / 网线
    2. 路由器地址不对（小米 / 红米路由器默认就是 192.168.31.1）
    3. 路由器上没开 SSH
"@
                }
                Fail '无法与路由器建立 SSH 连接' ("  plink 提示：" + ($all.Trim() -split "`n" | Select-Object -First 2 | ForEach-Object { $_.Trim() } | Where-Object { $_ }) )
            }
        }
    }
    if (-not $conn -or ($conn.Out -notmatch 'READY')) {
        Fail 'SSH 命令执行失败' '  请确认路由器密码正确、SSH 已开启。'
    }
    Write-Ok '已连上路由器'
} else {
    Write-Ok '已连上路由器（演练模式：没有真的连接）'
}

$existCheck = Invoke-Plink -ArgList @('if [ -d /data/campus/v2 ]; then echo INSTALLED; else echo FRESH; fi')
if ($existCheck -and $existCheck.Out -match 'INSTALLED') {
    Write-Warn2 '路由器上已经装过一份，本次会覆盖程序并把上网账号密码重设为你刚输入的值'
} else {
    Write-Info '全新安装'
}

# ============================================================
#  第 2 步：上传程序
# ============================================================
Write-Step '上传程序文件到路由器'

$files = @()
Get-ChildItem -LiteralPath (Join-Path $Script:Payload 'bin') -Filter '*.sh' | Sort-Object Name | ForEach-Object { $files += @{ L = $_.FullName; R = "$RemoteDir/bin/$($_.Name)" } }
Get-ChildItem -LiteralPath (Join-Path $Script:Payload 'conf') -Filter '*.ini' | Sort-Object Name | ForEach-Object { $files += @{ L = $_.FullName; R = "$RemoteDir/conf/$($_.Name)" } }
if ($files.Count -lt 10) { Fail "程序文件不完整（只找到 $($files.Count) 个）" '  请重新解压完整压缩包，不要打开压缩包单独拖文件出来。' }
if (-not ($files | Where-Object { $_.R -like '*/bin/campus_switch.sh' })) { Fail '程序文件不完整（缺少总开关脚本 campus_switch.sh）' '  请重新解压完整压缩包。' }

# 先建目录（用 stdin 把一小段脚本交给远端 sh 执行）
$mk = "mkdir -p $RemoteDir/bin $RemoteDir/conf $RemoteDir/key`n"
$r = Invoke-Plink -ArgList @('/bin/sh', '-s') -Stdin $mk -HasStdin
if (-not $DryRun -and $r.Code -ne 0) {
    Fail '在路由器上创建目录失败' ("  返回码=[$($r.Code)]`n  输出=[$($r.Out)]`n  错误=[$($r.Err)]`n  送入的脚本=[$mk]")
}

$done = 0
foreach ($f in $files) {
    $s = Send-File -Local $f.L -Remote $f.R
    if ($DryRun) { $done++; continue }
    $txt = "$($s.Out)`n$($s.Err)"
    if ($txt -match 'Host key|host key') {
        $nf = Get-HostKey $txt
        if ($nf) {
            $Script:HostKey = $nf
            try { Set-Content -LiteralPath $Script:HostKeyFile -Value $nf -Encoding ASCII } catch { }
            $s = Send-File -Local $f.L -Remote $f.R
        }
    }
    if ($s.Code -ne 0) { Fail ("上传失败：" + (Split-Path $f.L -Leaf)) ("  " + $txt.Trim()) }
    $done++
    Write-Info ("已上传 {0}/{1}  {2}" -f $done, $files.Count, (Split-Path $f.L -Leaf))
}
Write-Ok "共上传 $done 个文件"

# ============================================================
#  第 3 步：设置权限 + 清理旧凭据
# ============================================================
Write-Step '设置文件权限'

$setupScript = @'
set -e
chmod 755 REMOTEDIR/bin/*.sh
chmod 644 REMOTEDIR/conf/config.ini
chmod 700 REMOTEDIR/key
chmod 755 REMOTEDIR
# 清掉旧凭据：接下来会用全新的密钥重新加密保存（旧密钥即使泄露也没用）
rm -f REMOTEDIR/conf/cred.bin REMOTEDIR/key/campus.key
echo PERM_OK
'@
$setupScript = $setupScript.Replace('REMOTEDIR', $RemoteDir).Replace("`r`n", "`n")
$r = Invoke-Plink -ArgList @('/bin/sh', '-s') -Stdin $setupScript -HasStdin
if (-not $DryRun -and ($r.Out -notmatch 'PERM_OK')) { Fail '设置权限失败' ("  " + $r.Err.Trim()) }
Write-Ok '权限已设置（凭据文件仅 root 可读）'

# ============================================================
#  第 4 步：加密保存账号密码
# ============================================================
Write-Step '加密保存校园网账号密码（密码不经过命令行，别人 ps 看不到）'

$credPayload = $CampusUser + "`n" + $CampusPassword + "`n"
$r = Invoke-Plink -ArgList @("$RemoteDir/bin/cred_admin.sh init --force --stdin") -Stdin $credPayload -HasStdin
if (-not $DryRun) {
    $t = "$($r.Out)`n$($r.Err)"
    if ($t -match '已加密保存|凭据已加密写入') {
        if ($t -match 'base64') {
            # 小米/红米原厂固件不带 openssl，凭据只能降级存放 —— 明确告警，别让用户以为还是加密的
            Write-Warn2 '路由器上没有 openssl，账号密码只能以 base64 存放（安全强度等同明文，仅靠文件权限保护）'
            Write-Info  '不影响自动登录；只是"别人拷走文件也看不到原文"这条不再成立'
        } else {
            Write-Ok '账号密码已用 AES-256 加密保存'
        }
    } else {
        Fail '保存账号密码失败' ("  " + $t.Trim())
    }
}

$v = Invoke-Plink -ArgList @("$RemoteDir/bin/cred_admin.sh verify")
if (-not $DryRun) {
    $vt = "$($v.Out)`n$($v.Err)"
    if ($vt -match '凭据校验通过') {
        $line = ($vt -split "`n" | Where-Object { $_ -match '凭据校验通过' } | Select-Object -First 1).Trim()
        Write-Ok $line
    } else {
        Fail '凭据写入后校验失败' ("  " + $vt.Trim())
    }
}

# ============================================================
#  第 5 步：设置定时任务 + 总开关
# ============================================================
Write-Step '设置定时任务 + 安装总开关'

# 先问开关：单会话学校若一直开着，会把用户在教室的登录顶掉
$enableNow = $true
if (-not $Yes) {
    Write-Host ''
    Write-Host '  你有没有在教室/图书馆用校园网「刚连上就断」过？' -ForegroundColor Gray
    Write-Host '  如果学校限制「一个账号同时只能一台设备在线」，那多半就是这个原因。' -ForegroundColor Gray
    $a = Read-Host '  现在启用自动登录吗？（人在宿舍选 Y；马上要去教室选 n）(Y/n)'
    if ($a -and $a.ToLower() -ne 'y' -and $a.ToLower() -ne 'yes') { $enableNow = $false }
}

$cronScript = @'
LINE='*/2 7-23 * * * [ -f /data/campus/v2/enabled ] && CAMPUS_ROOT=/data/campus/v2 /data/campus/v2/bin/campus_login.sh >/dev/null 2>&1'
for f in /etc/crontabs/root /data/etc/crontabs/root; do
  [ -f "$f" ] || : > "$f"
  # 迁移: 先删掉所有引用 campus_login.sh 的历史行。旧版本没有开关判定，
  # 留着会绕过总开关（开关关了它照样每 2 分钟登录并抢线）。
  grep -v 'campus_login\.sh' "$f" > "$f.new" 2>/dev/null
  [ -f "$f.new" ] || : > "$f.new"
  cat "$f.new" > "$f"
  rm -f "$f.new"
  [ -n "$(tail -c 1 "$f" 2>/dev/null)" ] && printf '\n' >> "$f"
  printf '%s\n' "$LINE" >> "$f"
done
pgrep crond >/dev/null 2>&1 || /etc/init.d/cron start >/dev/null 2>&1
/etc/init.d/cron restart >/dev/null 2>&1
sleep 1
n=0; o=0
for f in /etc/crontabs/root /data/etc/crontabs/root; do
  n=$((n + $(grep -cF "$LINE" "$f" 2>/dev/null || true)))
  o=$((o + $(grep -c 'campus_login\.sh' "$f" 2>/dev/null || true)))
done
[ "$n" -ge 1 ] || { echo CRON_FAIL reason=missing; exit 1; }
[ "$n" -eq "$o" ] || { echo "CRON_FAIL reason=stale stale=$((o - n))"; exit 1; }
echo "CRON_OK n=$n"
'@
$cronScript = $cronScript.Replace("`r`n", "`n")
$r = Invoke-Plink -ArgList @('/bin/sh', '-s') -Stdin $cronScript -HasStdin
if (-not $DryRun -and ($r.Out -notmatch 'CRON_OK')) { Fail '设置定时任务失败' ("  " + $r.Out + ' ' + $r.Err) }
Write-Ok '定时任务已生效（每天 7:00–23:00 每 2 分钟检查一次，且受总开关控制）'

# 写总开关状态（/data 持久分区，重启不丢）
$flagScript = @'
FLAG=REMOTEFLAG
if [ "ONOFF" = "1" ]; then
  umask 077
  date '+%F %T' > "$FLAG" && echo FLAG_OK
else
  rm -f "$FLAG" && echo FLAG_OK
fi
'@
$flagScript = $flagScript.Replace('REMOTEFLAG', "$RemoteDir/enabled").Replace('ONOFF', $(if ($enableNow) { '1' } else { '0' })).Replace("`r`n", "`n")
$r = Invoke-Plink -ArgList @('/bin/sh', '-s') -Stdin $flagScript -HasStdin
if (-not $DryRun -and ($r.Out -notmatch 'FLAG_OK')) { Fail '设置总开关失败' ("  " + $r.Out + ' ' + $r.Err) }
if ($enableNow) {
    Write-Ok '总开关：已开启（路由器现在会照看登录）'
} else {
    Write-Ok '总开关：已关闭（路由器不会自动登录，不会和你抢会话）'
    Write-Info '回到宿舍想上网时，双击「校园网开关.bat」选 1 即可。'
}

# ============================================================
#  第 6 步：测试
# ============================================================
Write-Step '测试一下'

$t = Invoke-Plink -ArgList @("ls -l $RemoteDir/bin/campus_login.sh; $RemoteDir/bin/cred_admin.sh status 2>&1 | tail -4")
if (-not $DryRun -and $t) {
    ($t.Out -split "`n") | Where-Object { $_.Trim() } | ForEach-Object { Write-Info $_.Trim() }
}

if ($DryRun) {
    Write-Info '（演练模式：跳过实际登录测试）'
} elseif (-not $enableNow) {
    Write-Info '总开关处于关闭状态，跳过登录测试（避免路由器抢走你的会话）。'
    Write-Info '想现在就验证一遍：双击「校园网开关.bat」选 1，它会立刻试一次并告诉你结果。'
} elseif (-not $SkipLoginTest) {
    $lt = Invoke-Plink -ArgList @("CAMPUS_ROOT=$RemoteDir $RemoteDir/bin/campus_login.sh; echo `"EXIT=`$?`"; tail -3 /tmp/campus_login.log 2>/dev/null")
    if ($lt) {
        $lines = ($lt.Out -split "`n") | Where-Object { $_.Trim() } | Select-Object -Last 4
        foreach ($l in $lines) { Write-Info $l.Trim() }
        if ("$($lt.Out)$($lt.Err)" -match 'EXIT=0') {
            Write-Ok '当前网络正常（要么已经在线，要么刚刚自动登录成功）'
        } else {
            Write-Warn2 '现在这一次没成功——通常是因为此刻不在校园网里（比如在家里）'
            Write-Info '不用担心：等回到学校连上校园网，它会在 2 分钟内自动登录。'
        }
    }
}

# ============================================================
#  收尾
# ============================================================
Write-Title '部署完成'
if ($DryRun) { Write-Host '  （以上是演练，没有真的改动路由器）' -ForegroundColor DarkYellow }
if ($enableNow) {
    Write-Host '  路由器现在会自己照看校园网登录，你不用再管它了。' -ForegroundColor Green
} else {
    Write-Host '  程序已装好，但总开关是「关闭」状态 —— 路由器不会自动登录。' -ForegroundColor Yellow
    Write-Host '  回到宿舍想上网时，双击「校园网开关.bat」选 1 即可。' -ForegroundColor Green
}
Write-Host ''
Write-Host '  ★ 重要：离开宿舍去教室/图书馆之前，双击「校园网开关.bat」选 2 关闭。' -ForegroundColor Yellow
Write-Host '     否则路由器会一直用你的账号在线，你在外面用同一账号登录会被顶掉。' -ForegroundColor Yellow
Write-Host ''
Write-Host '  想知道它有没有干活？在路由器上跑：' -ForegroundColor Gray
Write-Host '     /data/campus/v2/bin/campus_switch.sh status' -ForegroundColor White
Write-Host '     tail -20 /tmp/campus_login.log' -ForegroundColor White
Write-Host ''
Write-Host '  想换账号密码？再跑一次本脚本即可（会覆盖旧的）。' -ForegroundColor Gray
Write-Host ''
if ($Script:Warned -gt 0) {
    Write-Host ("  本次有 {0} 条提醒，见上方 ! 开头的行。" -f $Script:Warned) -ForegroundColor DarkYellow
    Write-Host ''
}
