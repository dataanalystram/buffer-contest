# restore: bash <(git show) ... see usage in notes; copies e2e scripts to /tmp/e2e and installs mcp
if [ ! -f /tmp/e2e/live.py ]; then rm -rf /tmp/nb && timeout 30 git clone -q --depth 1 -b notes/e2e-mcp-pr73 -c http.sslCAInfo="$SSL_CERT_FILE" https://github.com/dataanalystram/buffer-contest.git /tmp/nb && mkdir -p /tmp/e2e && cp /tmp/nb/e2e/* /tmp/e2e/; fi
python3 -c "import mcp" 2>/dev/null || timeout 90 pip install -q mcp >/dev/null 2>&1
