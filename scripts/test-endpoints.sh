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

# MCP SSE (just check if endpoint exists)
echo -n "GET /mcp    ... "
CURL_STATUS=0
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 2 "${BASE_URL}/mcp/sse" 2>/dev/null) || CURL_STATUS=$?
if [ "$HTTP_CODE" = "200" ]; then
  echo "OK (SSE endpoint accepted; streaming timeout expected)"
else
  echo "FAIL (${HTTP_CODE:-no response}, curl=${CURL_STATUS})"
fi

echo ""
echo "Service info:"
curl -s "${BASE_URL}/info" 2>/dev/null | python3 -m json.tool 2>/dev/null || echo "(could not fetch)"
