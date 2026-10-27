#!/bin/bash
# =============================================================================
# Test all service endpoints
# =============================================================================
# Usage: ./scripts/test-endpoints.sh [base_url]

set -e

BASE_URL="${1:-http://localhost:8800}"

echo "Testing service at ${BASE_URL}"
echo "================================="

# Health
echo -n "GET /health ... "
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "${BASE_URL}/health")
if [ "$HTTP_CODE" = "200" ]; then echo "OK (${HTTP_CODE})"; else echo "FAIL (${HTTP_CODE})"; fi

# Ready
echo -n "GET /ready  ... "
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "${BASE_URL}/ready")
if [ "$HTTP_CODE" = "200" ]; then echo "OK (${HTTP_CODE})"; else echo "FAIL (${HTTP_CODE})"; fi

# Info
echo -n "GET /info   ... "
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "${BASE_URL}/info")
if [ "$HTTP_CODE" = "200" ]; then echo "OK (${HTTP_CODE})"; else echo "FAIL (${HTTP_CODE})"; fi

# API root
echo -n "GET /api/v1 ... "
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "${BASE_URL}/api/v1/")
if [ "$HTTP_CODE" = "200" ]; then echo "OK (${HTTP_CODE})"; else echo "FAIL (${HTTP_CODE})"; fi

# OpenAPI docs
echo -n "GET /docs   ... "
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "${BASE_URL}/docs")
if [ "$HTTP_CODE" = "200" ]; then echo "OK (${HTTP_CODE})"; else echo "FAIL (${HTTP_CODE})"; fi

# MCP Streamable HTTP (mounted: 200, or 401 when the service needs a key this script does not send)
echo -n "POST /mcp/http ... "
CURL_STATUS=0
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 -X POST   -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream"   -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' "${BASE_URL}/mcp/http" 2>/dev/null) || CURL_STATUS=$?
if [ "$HTTP_CODE" = "200" ] || [ "$HTTP_CODE" = "401" ]; then
  echo "OK (${HTTP_CODE})"
else
  echo "FAIL (${HTTP_CODE:-no response}, curl=${CURL_STATUS})"
fi

echo ""
echo "Service info:"
curl -s "${BASE_URL}/info" 2>/dev/null | python3 -m json.tool 2>/dev/null || echo "(could not fetch)"
