# 查出本机局域网地址，供平板访问。
# 用 DHCP 时地址会变，所以需要时重跑一次即可。

$port = if ($env:API_PORT) { $env:API_PORT } else { "8000" }

Write-Host "本机局域网地址：" -ForegroundColor Cyan

$urls = @()
Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object {
        $_.IPAddress -notlike '127.*' -and
        $_.IPAddress -notlike '169.254.*' -and
        $_.PrefixOrigin -ne 'WellKnown'
    } |
    ForEach-Object {
        $url = "http://$($_.IPAddress):$port"
        $urls += $url
        Write-Host "  $url" -ForegroundColor Green
        Write-Host "    接口：$($_.InterfaceAlias)" -ForegroundColor DarkGray
    }

if ($urls.Count -eq 0) {
    Write-Host "  没找到可用的局域网地址，检查网络连接。" -ForegroundColor Yellow
    exit 1
}

Write-Host ""
Write-Host "在平板浏览器里打开上面任意一个地址。" -ForegroundColor Cyan
Write-Host "如果打不开，依次检查：" -ForegroundColor DarkGray
Write-Host "  1. docker compose ps   —— 两个容器都是 running" -ForegroundColor DarkGray
Write-Host "  2. Windows 防火墙是否放行了 $port 端口（专用网络）" -ForegroundColor DarkGray
Write-Host "  3. 平板和电脑是否在同一个 Wi-Fi 下" -ForegroundColor DarkGray