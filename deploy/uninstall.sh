#!/bin/bash
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "请用 root 运行" >&2
  exit 1
fi

systemctl disable --now svcadmin 2>/dev/null || true
rm -f /etc/systemd/system/svcadmin.service
rm -f /etc/sudoers.d/svcadmin
rm -f /usr/local/sbin/svcadmin-apply
rm -rf /opt/svcadmin
rm -f /etc/nginx/sites-enabled/svcadmin /etc/nginx/sites-available/svcadmin
rm -f /etc/apparmor.d/local/usr.sbin.rsyslogd
rm -f /etc/systemd/resolved.conf.d/svcadmin.conf
systemctl daemon-reload
nginx -t >/dev/null 2>&1 && systemctl reload nginx || true
apparmor_parser -r /etc/apparmor.d/usr.sbin.rsyslogd >/dev/null 2>&1 || true

echo "面板已移除。"
echo "保留内容：MariaDB 三个数据库、/var/lib/svcadmin、FTP 家目录、五个服务本身及其配置。"
