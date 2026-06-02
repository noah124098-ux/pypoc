# setup_nginx_auth.ps1
# Run once to configure nginx basic auth for the pypoc Streamlit dashboard.
#
# Usage:
#   .\setup_nginx_auth.ps1 -Password "your_secure_password"
#
# After running, start (or reload) nginx:
#   nginx -c C:\Users\Administrator\pypoc\deploy\nginx.conf
#
# To reload an already-running nginx without downtime:
#   nginx -s reload
#
# IMPORTANT: Change the default password before exposing port 80 to the internet.

param(
    [string]$User     = "admin",
    [string]$Password = "changeme"
)

$htpasswdPath = "C:\Users\Administrator\pypoc\deploy\.htpasswd"

if ($Password -eq "changeme") {
    Write-Warning "You are using the default password 'changeme'. Set a strong password with -Password."
}

# nginx supports plain-text passwords in .htpasswd on Windows (no bcrypt needed).
# For stronger security, install Apache httpd tools and use htpasswd.exe -B.
$line = "${User}:${Password}"
Set-Content -Path $htpasswdPath -Value $line -Encoding ASCII
Write-Host "Written: $htpasswdPath"
Write-Host "User   : $User"
Write-Host "To start nginx: nginx -c C:\Users\Administrator\pypoc\deploy\nginx.conf"
Write-Host "To reload   : nginx -s reload"
