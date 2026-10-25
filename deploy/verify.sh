#!/bin/bash
# 端到端验收：逐个服务做真实读写测试，并清理测试数据。
set -uo pipefail
cd /opt/svcadmin || exit 1
set -a
# shellcheck disable=SC1091
. /etc/svcadmin/svcadmin.env
set +a
PY=/opt/svcadmin/venv/bin/python

pass=0
fail=0
check() {
  if [ "$1" = "0" ]; then pass=$((pass + 1)); echo "PASS  $2";
  else fail=$((fail + 1)); echo "FAIL  $2"; fi
}

echo "== 1. 服务状态 =="
for unit in svcadmin nginx dnsmasq pure-ftpd-mysql freeradius rsyslog snmpd chrony; do
  state=$(systemctl is-active "$unit")
  [ "$state" = "active" ]; check $? "$unit = $state"
done

echo "== 2. 面板 HTTP =="
code=$(curl -sk -o /dev/null -w '%{http_code}' https://127.0.0.1/healthz)
[ "$code" = "200" ]; check $? "面板 /healthz = $code"

echo "== 3. DNS 解析 =="
$PY - <<'PY'
import sys
import dns.resolver

resolver = dns.resolver.Resolver(configure=False)
resolver.nameservers = ["127.0.0.1"]
resolver.lifetime = 3
try:
    answers = [a.to_text() for a in resolver.resolve("example.com", "A")]
    print("example.com ->", answers)
    sys.exit(0)
except Exception as exc:
    print("resolve failed:", exc)
    sys.exit(1)
PY
check $? "dnsmasq 对外解析"

echo "== 4. FTP 登录 =="
$PY - <<'PY'
import sys

sys.path.insert(0, "/opt/svcadmin")
from app.config import settings
from app.db import SessionLocal
from app.models import FtpUser
from app.services import ftp
from app.services.store import load

with SessionLocal() as db:
    user = db.query(FtpUser).filter_by(username="verify").first()
    if user is None:
        user = FtpUser(username="verify", password=ftp.hash_password("Verify123!"),
                       uid=settings.ftp_uid, gid=settings.ftp_gid, homedir="/srv/ftp/verify", status=1)
        db.add(user)
    else:
        user.password = ftp.hash_password("Verify123!")
        user.status = 1
    db.commit()
    cfg = load(db, "ftp", ftp.DEFAULTS)
    result = ftp.apply_config(cfg, [u.username for u in db.query(FtpUser).all()])
    print("apply:", "ok" if result.ok else result.text())

login = ftp.test_login("verify", "Verify123!", use_tls=bool(cfg.get("tls", True)))
print(login)
sys.exit(0 if "成功" in login else 1)
PY
check $? "FTP/FTPS 虚拟用户登录"

echo "== 5. RADIUS 认证 =="
$PY - <<'PY'
import sys

sys.path.insert(0, "/opt/svcadmin")
from app.db import SessionLocal
from app.services import radius

with SessionLocal() as db:
    radius.save_user("verify", "Verify123!", "", "", "")
    secret, created = radius.ensure_test_client(db)
    if created:
        radius.apply_config(db)
    output = radius.test_auth("verify", "Verify123!", secret)
    print(output)
    sys.exit(0 if "Access-Accept" in output else 1)
PY
check $? "radtest 认证"

echo "== 6. Syslog 入库 =="
MSG="svcadmin-verify-$(date +%s)"
logger -n 127.0.0.1 -P 514 -d -t svcadmin-verify "$MSG"
sleep 2
COUNT=$(mysql -N -e "SELECT COUNT(*) FROM logs.SystemEvents WHERE Message LIKE '%${MSG}%'")
[ "${COUNT:-0}" -ge 1 ]; check $? "远程日志入库 ($COUNT 条)"

echo "== 7. SNMP 读取 =="
COMMUNITY=$(mysql -N -e "SELECT JSON_UNQUOTE(JSON_EXTRACT(data,'$.community')) FROM svcadmin.service_config WHERE service='snmp'")
OUT=$(snmpget -v2c -c "${COMMUNITY:-public}" -t 3 -r 1 127.0.0.1 1.3.6.1.2.1.1.1.0 2>&1)
echo "$OUT"
echo "$OUT" | grep -q "STRING:"; check $? "snmpget sysDescr"

echo "== 8. NTP 授时 =="
OUT=$(chronyc -n tracking 2>&1)
echo "$OUT" | grep -q "Stratum"; check $? "chronyc tracking 可读取"
echo "$OUT" | grep -qE "Leap status *: Normal"; check $? "时间同步正常 (Leap Normal)"
ss -lun 2>/dev/null | grep -q ':123 '; check $? "UDP 123 监听中"

echo "== 清理测试数据 =="
$PY - <<'PY'
import sys

sys.path.insert(0, "/opt/svcadmin")
from app.db import SessionLocal
from app.models import FtpUser
from app.services import radius

with SessionLocal() as db:
    user = db.query(FtpUser).filter_by(username="verify").first()
    if user:
        db.delete(user)
        db.commit()
    radius.delete_user("verify")
print("已删除 verify 测试账号")
PY

echo
echo "结果：$pass 通过，$fail 失败"
exit $([ "$fail" -eq 0 ] && echo 0 || echo 1)
