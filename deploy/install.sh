#!/bin/bash
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "请用 root 运行：sudo bash deploy/install.sh" >&2
  exit 1
fi

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CODE_ROOT=/opt/svcadmin
PKG_DIR=$CODE_ROOT/app
VENV=$CODE_ROOT/venv
DATA=/var/lib/svcadmin
CONF_DIR=/etc/svcadmin
ENV_FILE=$CONF_DIR/svcadmin.env
PRIMARY_IP=$(hostname -I | awk '{print $1}')
FALLBACK_DNS=$(ip route | awk '/^default/ {print $3; exit}')
TIMEZONE=Asia/Shanghai

echo "== 1/11 安装依赖包 =="
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
  -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold \
  nginx mariadb-server python3-venv python3-pip \
  dnsmasq pure-ftpd-mysql freeradius freeradius-mysql rsyslog-mysql snmpd snmp chrony logrotate >/dev/null
# chrony 与 systemd-timesyncd 互斥（安装 chrony 时通常已自动移除后者）
systemctl disable --now systemd-timesyncd >/dev/null 2>&1 || true

echo "== 2/11 创建系统用户、目录与时区 =="
timedatectl set-timezone "$TIMEZONE"
install -d -m 0755 /etc/mysql/mariadb.conf.d
printf '[mysqld]\ndefault-time-zone = "+08:00"\n' > /etc/mysql/mariadb.conf.d/99-svcadmin-timezone.cnf
id -u svcadmin >/dev/null 2>&1 || useradd --system --home "$CODE_ROOT" --shell /usr/sbin/nologin svcadmin
getent group 2001 >/dev/null || groupadd --system --gid 2001 ftpuser
id -u ftpuser >/dev/null 2>&1 || useradd --system --uid 2001 --gid 2001 --home-dir /srv/ftp --shell /usr/sbin/nologin ftpuser
usermod -aG ftpuser,adm svcadmin
install -d -m 0755 "$CODE_ROOT" "$DATA" "$DATA/staging" "$DATA/backups" /srv/ftp
install -d -m 0750 "$CONF_DIR"
chown -R svcadmin:svcadmin "$DATA"
chown root:svcadmin "$CONF_DIR"
install -d -m 0755 /var/log/pure-ftpd

echo "== 3/11 部署应用代码与虚拟环境 =="
rm -rf "$PKG_DIR"
install -d -m 0755 "$PKG_DIR"
cp -a "$SRC_DIR/app/." "$PKG_DIR/"
cp "$SRC_DIR/requirements.txt" "$CODE_ROOT/requirements.txt"
chown -R root:root "$PKG_DIR"
find "$PKG_DIR" -type d -exec chmod 0755 {} +
find "$PKG_DIR" -type f -exec chmod 0644 {} +
python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r "$CODE_ROOT/requirements.txt"

echo "== 4/11 生成密钥与配置 =="
if [ ! -f "$ENV_FILE" ]; then
  DB_PW=$(openssl rand -hex 16)
  RSYSLOG_PW=$(openssl rand -hex 16)
  SECRET_KEY=$(openssl rand -hex 32)
  cat > "$ENV_FILE" <<EOF
SVCADMIN_DATABASE_URL="mysql+pymysql://svcadmin:${DB_PW}@localhost/svcadmin?unix_socket=/run/mysqld/mysqld.sock&charset=utf8mb4"
SVCADMIN_RADIUS_URL="mysql+pymysql://svcadmin:${DB_PW}@localhost/radius?unix_socket=/run/mysqld/mysqld.sock&charset=utf8mb4"
SVCADMIN_LOGS_URL="mysql+pymysql://svcadmin:${DB_PW}@localhost/logs?unix_socket=/run/mysqld/mysqld.sock&charset=utf8mb4"
SVCADMIN_SECRET_KEY="${SECRET_KEY}"
SVCADMIN_DB_PASSWORD="${DB_PW}"
SVCADMIN_DB_NAME="svcadmin"
SVCADMIN_RSYSLOG_DB_PASSWORD="${RSYSLOG_PW}"
SVCADMIN_STAGING="/var/lib/svcadmin/staging"
SVCADMIN_APPLY_HELPER="/usr/local/sbin/svcadmin-apply"
SVCADMIN_TIMEZONE="${TIMEZONE}"
EOF
  chown root:svcadmin "$ENV_FILE"
  chmod 0640 "$ENV_FILE"
fi
if ! grep -q '^SVCADMIN_TIMEZONE=' "$ENV_FILE"; then
  echo "SVCADMIN_TIMEZONE=\"${TIMEZONE}\"" >> "$ENV_FILE"
fi
set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

echo "== 5/11 初始化 MariaDB =="
systemctl enable --now mariadb >/dev/null
mysql <<SQL
CREATE DATABASE IF NOT EXISTS svcadmin CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE IF NOT EXISTS radius CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE IF NOT EXISTS logs CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'svcadmin'@'localhost' IDENTIFIED BY '${SVCADMIN_DB_PASSWORD}';
GRANT ALL PRIVILEGES ON svcadmin.* TO 'svcadmin'@'localhost';
GRANT ALL PRIVILEGES ON radius.* TO 'svcadmin'@'localhost';
GRANT SELECT, INSERT, DELETE, UPDATE ON logs.* TO 'svcadmin'@'localhost';
CREATE USER IF NOT EXISTS 'rsyslog'@'localhost' IDENTIFIED BY '${SVCADMIN_RSYSLOG_DB_PASSWORD}';
GRANT SELECT, INSERT ON logs.* TO 'rsyslog'@'localhost';
FLUSH PRIVILEGES;
SQL

if [ "$(mysql -N -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='radius' AND table_name='radcheck'")" = "0" ]; then
  mysql radius < /etc/freeradius/3.0/mods-config/sql/main/mysql/schema.sql
  echo "已导入 FreeRADIUS SQL schema"
fi
mysql logs < "$SRC_DIR/deploy/sql/01-logs.sql"

echo "== 6/11 配置 FreeRADIUS SQL 后端 =="
ln -sf ../mods-available/sql /etc/freeradius/3.0/mods-enabled/sql
DB_PW="$SVCADMIN_DB_PASSWORD" python3 - <<'PY'
import os
import re
from pathlib import Path

path = Path("/etc/freeradius/3.0/mods-available/sql")
text = path.read_text(encoding="utf-8")
password = os.environ["DB_PW"]
text = re.sub(r'^\s*dialect\s*=\s*".*?"', '\tdialect = "mysql"', text, count=1, flags=re.M)
text = re.sub(r'^\s*driver\s*=\s*"rlm_sql_null"', '\tdriver = "rlm_sql_mysql"', text, count=1, flags=re.M)
text = re.sub(r'^\s*#\s*server\s*=\s*"localhost"', '\tserver = "localhost"', text, count=1, flags=re.M)
text = re.sub(r'^\s*#\s*port\s*=\s*3306', '\tport = 3306', text, count=1, flags=re.M)
text = re.sub(r'^\s*#\s*login\s*=\s*"radius"', '\tlogin = "svcadmin"', text, count=1, flags=re.M)
text = re.sub(r'^\s*#\s*password\s*=\s*"radpass"', '\tpassword = "%s"' % password, text, count=1, flags=re.M)
text = re.sub(r'^\s*#\s*read_clients\s*=\s*yes', '\tread_clients = yes', text, count=1, flags=re.M)
# 本地 socket 连接不需要 MySQL TLS，注释掉默认的证书路径避免模块加载失败
text = re.sub(
    r'^(\s*)(ca_file|ca_path|certificate_file|private_key_file|cipher|tls_required|tls_check_cert|tls_check_cert_cn)\s*=.*$',
    r'\1# \2 disabled-by-svcadmin',
    text,
    flags=re.M,
)
path.write_text(text, encoding="utf-8")
print("FreeRADIUS sql 模块已写入数据库凭据")
PY

# NAS 客户端统一由数据库 nas 表提供，清空包自带的 localhost 客户端避免 "duplicate client"
cat > /etc/freeradius/3.0/clients.conf <<'CONF'
# Managed by SVC Admin
# 所有 NAS 客户端来自数据库 radius.nas（mods-available/sql: read_clients = yes）
CONF

echo "== 7/11 配置 rsyslog 与 AppArmor =="
if [ -f /etc/rsyslog.d/mysql.conf ]; then
  mv /etc/rsyslog.d/mysql.conf /etc/rsyslog.d/mysql.conf.disabled
fi
cat > /etc/apparmor.d/local/usr.sbin.rsyslogd <<'EOF'
# SVC Admin: 允许 rsyslog 通过 unix socket 连接 MariaDB
network,
unix (connect, send, receive) type=stream,
/run/mysqld/mysqld.sock rw,
/var/run/mysqld/mysqld.sock rw,
EOF
apparmor_parser -r /etc/apparmor.d/usr.sbin.rsyslogd >/dev/null 2>&1 || true

echo "== 8/11 配置 dnsmasq 与系统解析器 =="
if ! grep -q '^conf-dir=/etc/dnsmasq.d' /etc/dnsmasq.conf; then
  printf '\nconf-dir=/etc/dnsmasq.d/,*.conf\n' >> /etc/dnsmasq.conf
fi
# 查询日志：面板用户（adm 组）需要读取，并配置轮转
if [ ! -f /var/log/dnsmasq.log ]; then
  install -o dnsmasq -g adm -m 0640 /dev/null /var/log/dnsmasq.log
fi
chown dnsmasq:adm /var/log/dnsmasq.log
chmod 0640 /var/log/dnsmasq.log
cat > /etc/logrotate.d/svcadmin-dnsmasq <<'EOF'
/var/log/dnsmasq.log {
    su root syslog
    weekly
    rotate 8
    compress
    missingok
    notifempty
    copytruncate
    create 0640 dnsmasq adm
}
EOF
install -d /etc/systemd/system/dnsmasq.service.d
cat > /etc/systemd/system/dnsmasq.service.d/svcadmin-log.conf <<'EOF'
[Service]
ExecStartPost=/bin/chown dnsmasq:adm /var/log/dnsmasq.log
ExecStartPost=/bin/chmod 0640 /var/log/dnsmasq.log
EOF
systemctl daemon-reload
mkdir -p /etc/systemd/resolved.conf.d
printf '[Resolve]\nDNSStubListener=no\n' > /etc/systemd/resolved.conf.d/svcadmin.conf
systemctl restart systemd-resolved
rm -f /etc/resolv.conf
{ echo "nameserver 127.0.0.1"; [ -n "$FALLBACK_DNS" ] && echo "nameserver $FALLBACK_DNS"; } > /etc/resolv.conf

echo "== 9/11 生成 TLS 证书 =="
if [ ! -f /etc/ssl/private/pure-ftpd.pem ]; then
  openssl req -x509 -nodes -newkey rsa:2048 -days 3650 \
    -keyout /tmp/pure-ftpd.key -out /tmp/pure-ftpd.crt \
    -subj "/CN=$(hostname)-ftp" >/dev/null 2>&1
  cat /tmp/pure-ftpd.key /tmp/pure-ftpd.crt > /etc/ssl/private/pure-ftpd.pem
  chmod 0600 /etc/ssl/private/pure-ftpd.pem
  rm -f /tmp/pure-ftpd.key /tmp/pure-ftpd.crt
fi
if [ ! -f /etc/ssl/private/svcadmin.key ]; then
  openssl req -x509 -nodes -newkey rsa:2048 -days 3650 \
    -keyout /etc/ssl/private/svcadmin.key -out /etc/ssl/certs/svcadmin.crt \
    -subj "/CN=svcadmin" \
    -addext "subjectAltName=IP:${PRIMARY_IP},DNS:$(hostname)" >/dev/null 2>&1
  chmod 0600 /etc/ssl/private/svcadmin.key
fi

echo "== 10/11 安装权限助手、systemd 与 Nginx =="
install -m 0755 "$SRC_DIR/deploy/svcadmin-apply" /usr/local/sbin/svcadmin-apply
install -m 0755 "$SRC_DIR/deploy/svcadmin-net" /usr/local/sbin/svcadmin-net
install -m 0644 "$SRC_DIR/deploy/svcadmin.service" /etc/systemd/system/svcadmin.service
install -m 0440 "$SRC_DIR/deploy/sudoers-svcadmin" /etc/sudoers.d/svcadmin
visudo -cf /etc/sudoers.d/svcadmin >/dev/null
rm -f /etc/nginx/sites-enabled/default
install -m 0644 "$SRC_DIR/deploy/nginx-svcadmin.conf" /etc/nginx/sites-available/svcadmin
ln -sf /etc/nginx/sites-available/svcadmin /etc/nginx/sites-enabled/svcadmin
nginx -t >/dev/null
systemctl enable nginx >/dev/null
systemctl restart nginx

if ufw status 2>/dev/null | grep -q "Status: active"; then
  for rule in "53/tcp" "53/udp" "161/udp" "1812/udp" "1813/udp" "514/tcp" "514/udp" "123/udp"; do
    ufw allow "$rule" >/dev/null 2>&1 || true
  done
fi

echo "== 11/11 初始化管理员与基线配置 =="
cd "$CODE_ROOT"
ADMIN_OUTPUT=$("$VENV/bin/python" -m app.cli init-admin 2>&1)
echo "$ADMIN_OUTPUT"
chown -R svcadmin:svcadmin "$DATA/staging"
sudo -u svcadmin bash -c 'set -a; . /etc/svcadmin/svcadmin.env; set +a; cd /opt/svcadmin; ./venv/bin/python -m app.cli bootstrap' || true

systemctl daemon-reload
systemctl enable --now svcadmin >/dev/null
sleep 2

echo
echo "======================= 部署完成 ======================="
echo "面板地址 : https://${PRIMARY_IP}/"
echo "管理员   : admin"
echo "$ADMIN_OUTPUT" | grep '^ADMIN_PASSWORD=' || echo "密码：已存在（使用现有密码）"
echo
echo "服务端口 : FTP 21 / DNS 53 / RADIUS 1812-1813 / Syslog 514 / SNMP 161"
echo "自签证书 ,浏览器需手动信任一次"
echo "========================================================"
