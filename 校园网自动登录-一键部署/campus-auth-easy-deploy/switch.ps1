#Requires -Version 5.1
<#
============================================================
 校园网自动登录 · 总开关（Windows 侧）
 ------------------------------------------------------------
 作用：远程切换路由器上的「自动登录」开关。

   ★ 离开宿舍去教室 / 图书馆之前，选 2 关闭。
     学校若限制「一个账号同时只能一台设备在线」，路由器一直
     用你的账号在线，你在外面用同一账号登录就会被它顶下线。

 ------------------------------------------------------------
 用法：双击同目录的「校园网开关.bat」（推荐，有菜单）
       也可以直接调用：
         powershell -ExecutionPolicy Bypass -File switch.ps1 -Action status

 ------------------------------------------------------------
 注意：本脚本需要你的电脑正连着这台路由器的 WiFi / 网线 ——
       开关只能在宿舍网络里切换，人已经在教室就够不着了。

 ------------------------------------------------------------
 路由器的连接信息从同目录 deploy.ini 读取：
   RouterHost=192.168.31.1
   SshUser=root
   SshPassword=你的路由器密码   ← 填了就不用每次输密码
============================================================
#>
[CmdletBinding()]
param(
    [ValidateSet('Menu', 'On', 'Off', 'Status')]
    [string]$Action = 'Menu',
    [string]$RouterHost,
    [string]$SshUser,
    [string]$SshPassword,
    [string]$Port = '22'
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$Script:Root = $PSScriptRoot
$Script:RemoteDir = '/data/campus/v2'
$Script:RemoteSwitch = '/data/campus/v2/bin/campus_switch.sh'
$Script:HostKeyFile = Join-Path $Script:Root 'hostkey.txt'
$Script:HostKey = $null
$Script:Plink = $null
$Script:Connected = $false

# ---------- 输出助手 ----------
function Write-Bar { param([string]$c = '=') Write-Host ($c * 62) -ForegroundColor DarkCyan }
function Write-Title {
    param([string]$t)
    Write-Host ''
    Write-Bar
    Write-Host "  $t" -ForegroundColor Cyan
    Write-Bar
}
function Write-Ok    { param([string]$t) Write-Host "  √ $t" -ForegroundColor Green }
function Write-Info  { param([string]$t) Write-Host "  · $t" -ForegroundColor Gray }
function Write-Warn2 { param([string]$t) Write-Host "  ! $t" -ForegroundColor DarkYellow }
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

# ---------- 找 plink ----------
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

# ---------- 进程调用 ----------
function Invoke-Exe {
    param(
        [Parameter(Mandatory)][string]$Exe,
        [Parameter(Mandatory)][string[]]$ArgList
    )
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $Exe
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    $psi.StandardOutputEncoding = $utf8
    $psi.StandardErrorEncoding = $utf8
    $psi.Arguments = (($ArgList | ForEach-Object { Quote-Arg $_ }) -join ' ')
    $p = [System.Diagnostics.Process]::Start($psi)
    $out = $p.StandardOutput.ReadToEnd()
    $err = $p.StandardError.ReadToEnd()
    $p.WaitForExit()
    $code = $p.ExitCode
    $p.Dispose()
    return [pscustomobject]@{ Code = $code; Out = $out; Err = $err }
}

function Invoke-Remote {
    param([Parameter(Mandatory)][string]$Cmd)
    $a = @('-ssh', '-batch', '-P', $Script:Port)
    if ($Script:HostKey) { $a += @('-hostkey', $Script:HostKey) }
    $a += @('-pw', $SshPassword, "$SshUser@$RouterHost", $Cmd)
    return Invoke-Exe -Exe $Script:Plink -ArgList $a
}

function Get-HostKey {
    param([string]$Text)
    if (-not $Text) { return $null }
    $m = [regex]::Match($Text, '(ssh-\S+)\s+(\d+)\s+(SHA256:[A-Za-z0-9+/=]+)')
    if ($m.Success) { return ($m.Groups[1].Value + ' ' + $m.Groups[2].Value + ' ' + $m.Groups[3].Value) }
    return $null
}

function Read-Secret {
    param([string]$Prompt)
    $sec = Read-Host -Prompt $Prompt -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}

# ============================================================
#  读取 deploy.ini
# ============================================================
$iniPath = Join-Path $Script:Root 'deploy.ini'
$ini = @{}
if (Test-Path $iniPath) {
    try {
        Get-Content -LiteralPath $iniPath -Encoding UTF8 | ForEach-Object {
            if ($_ -match '^\s*([^#;][^=]*?)\s*=\s*(.*)$') { $ini[$matches[1].Trim()] = $matches[2].Trim() }
        }
    } catch { }
}
function Ini-Get { param([string]$k) if ($ini.ContainsKey($k) -and $ini[$k]) { return $ini[$k] } return $null }

if (-not $RouterHost) { $RouterHost = Ini-Get 'RouterHost' }
if (-not $SshUser) { $SshUser = Ini-Get 'SshUser' }
if (-not $SshPassword) { $SshPassword = Ini-Get 'SshPassword' }
$p = Ini-Get 'Port'
if ($p) { $Port = $p }
if (-not $RouterHost) { $RouterHost = '192.168.31.1' }
if (-not $SshUser) { $SshUser = 'root' }

$Script:Plink = Find-Tool 'plink'
if (-not $Script:Plink) {
    Fail '缺少 plink.exe（PuTTY 工具包里的一个小程序）' @"
  解决办法（二选一）：
    A. 把 plink.exe 复制到本文件夹的 tools 里
    B. 到 https://www.putty.org 下载安装 PuTTY，然后重跑
"@
}

if (Test-Path $Script:HostKeyFile) {
    $saved = (Get-Content -LiteralPath $Script:HostKeyFile -Raw).Trim()
    if ($saved) { $Script:HostKey = $saved }
}

# ============================================================
#  连接路由器
# ============================================================
function Connect-Router {
    if ($Script:Connected) { return $true }

    if (-not $SshPassword) {
        Write-Host "  需要路由器登录密码（$SshUser@$RouterHost）" -ForegroundColor Gray
        Write-Host '  （输入时不会显示；想免输就在 deploy.ini 里加一行 SshPassword=你的路由器密码）' -ForegroundColor DarkGray
        $Script:SshPassword = Read-Secret '  路由器登录密码'
        if (-not $SshPassword) { return $false }
    }

    $r = Invoke-Remote 'echo READY'
    $txt = "$($r.Out)`n$($r.Err)"
    if ($txt -notmatch 'READY') {
        $nf = Get-HostKey $txt
        if ($nf) {
            $Script:HostKey = $nf
            try { Set-Content -LiteralPath $Script:HostKeyFile -Value $nf -Encoding ASCII } catch { }
            $r = Invoke-Remote 'echo READY'
            $txt = "$($r.Out)`n$($r.Err)"
        }
    }
    if ($txt -notmatch 'READY') {
        if ($txt -match 'Access denied|Authentication failed|password') {
            Write-Host ''
            Write-Host '  × 路由器密码不对（或用户名不是 root）' -ForegroundColor Red
            Write-Host '    这里要填的是「路由器后台登录密码」，不是校园网密码。' -ForegroundColor DarkYellow
        } else {
            Write-Host ''
            Write-Host "  × 连不上路由器 $RouterHost" -ForegroundColor Red
            Write-Host '    最常见的两个原因：' -ForegroundColor DarkYellow
            Write-Host '      1. 你的电脑现在没连这台路由器的 WiFi —— 开关只能在宿舍网络里切换' -ForegroundColor DarkYellow
            Write-Host '      2. 路由器地址不对（小米 / 红米路由器默认就是 192.168.31.1）' -ForegroundColor DarkYellow
        }
        Write-Host ''
        return $false
    }
    $Script:Connected = $true
    return $true
}

# ============================================================
#  动作
# ============================================================
function Do-Status {
    if (-not (Connect-Router)) { return $false }
    $r = Invoke-Remote "$Script:RemoteSwitch status 2>&1"
    $txt = "$($r.Out)"
    if ($txt -match 'not found|No such file') {
        Write-Host ''
        Write-Warn2 '路由器上还没装总开关脚本（可能是旧版本部署）'
        Write-Info '请重新双击「一键部署.bat」跑一遍，会自动补上。'
        Write-Host ''
        return $false
    }
    Write-Host ''
    ($txt -split "`n") | Where-Object { $_.Trim() } | ForEach-Object { Write-Host "  $_" }
    Write-Host ''
    if ($txt -match '已开启') {
        Write-Host '  → 现在是「路由器优先」：你离开宿舍前记得关掉。' -ForegroundColor Yellow
        Write-Host ''
    }
    return $true
}

function Do-On {
    if (-not (Connect-Router)) { return $false }
    Write-Host ''
    Write-Info '正在开启并立刻试一次登录，请稍等（最多 20 秒）...'
    $r = Invoke-Remote "$Script:RemoteSwitch on 2>&1"
    Write-Host ''
    ($r.Out -split "`n") | Where-Object { $_.Trim() } | ForEach-Object { Write-Host "  $_" }
    Write-Host ''
    Write-Ok '宿舍 WiFi 现在应该可以上网了'
    return $true
}

function Do-Off {
    if (-not (Connect-Router)) { return $false }
    $r = Invoke-Remote "$Script:RemoteSwitch off 2>&1"
    Write-Host ''
    ($r.Out -split "`n") | Where-Object { $_.Trim() } | ForEach-Object { Write-Host "  $_" }
    Write-Host ''
    Write-Ok '已关闭：你在教室/图书馆可以安心用校园网了'
    return $true
}

# ============================================================
#  直接命令行模式
# ============================================================
if ($Action -ne 'Menu') {
    switch ($Action) {
        'Status' { $null = Do-Status ; exit 0 }
        'On'     { $null = Do-On     ; exit 0 }
        'Off'    { $null = Do-Off    ; exit 0 }
    }
}

# ============================================================
#  交互菜单
# ============================================================
try { Clear-Host } catch { }
Write-Bar
Write-Host '   校园网自动登录 · 总开关' -ForegroundColor Cyan
Write-Host '   控制路由器要不要帮你自动登录校园网' -ForegroundColor Gray
Write-Bar

Write-Host ''
Write-Host '   1) 开启   —— 回宿舍了，让路由器自动上网' -ForegroundColor Green
Write-Host '   2) 关闭   —— 要去教室/图书馆，别和我抢账号' -ForegroundColor Yellow
Write-Host '   3) 查看当前状态' -ForegroundColor Gray
Write-Host '   0) 退出' -ForegroundColor DarkGray
Write-Host ''
Write-Host '   ★ 单会话学校（一个账号只能一台设备在线）：' -ForegroundColor Yellow
Write-Host '     离开宿舍前一定选 2，否则你在外面会被反复顶下线。' -ForegroundColor Yellow
Write-Host ''

while ($true) {
    $c = (Read-Host '  请选择 [0-3]').Trim()
    switch ($c) {
        '1' { $null = Do-On }
        '2' { $null = Do-Off }
        '3' { $null = Do-Status }
        '0' { Write-Host '' ; Write-Host '  再见。' -ForegroundColor Gray ; Write-Host '' ; exit 0 }
        default { Write-Host '  请输入 0、1、2 或 3' -ForegroundColor DarkYellow ; continue }
    }
    Write-Host ''
    Read-Host '  按回车继续（或直接关窗口退出）' | Out-Null
    Write-Host ''
}
