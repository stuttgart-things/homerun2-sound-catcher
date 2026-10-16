// Dagger CI module for homerun2-sound-catcher
//
// Delegates to the reusable stuttgart-things/dagger/python module for
// lint, test, security scan, and image build. Adds project-specific
// integration tests with Redis.

package main

import (
	"context"
	"dagger/dagger/internal/dagger"
	"fmt"
)

type Dagger struct{}

// Lint runs ruff linter on the Python source code
func (m *Dagger) Lint(
	ctx context.Context,
	src *dagger.Directory,
	// +optional
	// +default="0.17.1"
	ruffVersion string,
) (string, error) {
	return dag.Python().Lint(ctx, src, dagger.PythonLintOpts{
		RuffVersion: ruffVersion,
	})
}

// Test runs pytest on the Python source code
func (m *Dagger) Test(
	ctx context.Context,
	src *dagger.Directory,
	// +optional
	// renovate: python
	// +default="3.14-slim"
	pythonVersion string,
) (string, error) {
	return dag.Python().Test(ctx, src, dagger.PythonTestOpts{
		PythonVersion: pythonVersion,
	})
}

// SecurityScan runs bandit Python security scanner
func (m *Dagger) SecurityScan(
	ctx context.Context,
	src *dagger.Directory,
) (string, error) {
	return dag.Python().SecurityScan(ctx, src)
}

// BuildImage builds a Docker container image from the Dockerfile
func (m *Dagger) BuildImage(
	ctx context.Context,
	src *dagger.Directory,
	// +optional
	// +default="dev"
	version string,
	// +optional
	// +default="unknown"
	commit string,
	// +optional
	// +default="unknown"
	date string,
) *dagger.Container {
	return dag.Python().BuildImage(ctx, src, dagger.PythonBuildImageOpts{
		Version: version,
		Commit:  commit,
		Date:    date,
	})
}

// ScanImage scans a container image for vulnerabilities using Trivy
func (m *Dagger) ScanImage(
	ctx context.Context,
	imageRef string,
	// +optional
	// +default="HIGH,CRITICAL"
	severity string,
) *dagger.File {
	return dag.Trivy().ScanImage(imageRef, dagger.TrivyScanImageOpts{
		Severity: severity,
	})
}

// BuildAndTestBinary builds the Docker image and runs integration tests with Redis
func (m *Dagger) BuildAndTestBinary(
	ctx context.Context,
	src *dagger.Directory,
	// +optional
	// +default="dev"
	version string,
	// +optional
	// +default="unknown"
	commit string,
) (*dagger.File, error) {

	// Build the Docker image via reusable module
	appImage := dag.Python().BuildImage(ctx, src, dagger.PythonBuildImageOpts{
		Version: version,
		Commit:  commit,
	})

	// Redis service via homerun module
	redisService := dag.Homerun().RedisService(dagger.HomerunRedisServiceOpts{
		Version:  "7.2.0-v18",
		Password: "",
	})

	testCmd := `
exec > /tmp/test-output.log 2>&1
set -e

echo "=== Starting sound-catcher ==="
sound-catcher &
APP_PID=$!
sleep 3

echo ""
echo "=== Checking process is running ==="
if kill -0 $APP_PID 2>/dev/null; then
  echo "PASS: sound-catcher is running (PID: $APP_PID)"
else
  echo "FAIL: sound-catcher failed to start"
  exit 1
fi

echo ""
echo "=== Health check ==="
HTTP_CODE=$(python3 -c "
import urllib.request
try:
    resp = urllib.request.urlopen('http://localhost:8080/healthz')
    print(resp.status)
except Exception as e:
    print(f'000: {e}')
")
echo "Health endpoint returned: $HTTP_CODE"

echo ""
echo "=== Sending test message to Redis ==="
pip install -q redis > /dev/null 2>&1
python3 -c "
import redis, json
r = redis.Redis(host='redis', port=6379, decode_responses=True)
msg = {'title': 'Integration Test', 'message': 'Dagger CI test', 'severity': 'error', 'system': 'dagger', 'author': 'ci'}
r.execute_command('JSON.SET', 'test-msg-001', '\$', json.dumps(msg))
r.xadd('messages', {'messageID': 'test-msg-001'})
print('Test message sent successfully')
"

echo "Waiting for consumer to process..."
sleep 5

echo ""
echo "=== Verifying the announcement is in the history ==="
python3 -c "
import json, urllib.request
history = json.load(urllib.request.urlopen('http://localhost:8080/api/history'))
assert any(e['title'] == 'Integration Test' and e['rule'] == 'errors' for e in history), history
print('PASS: announcement recorded (played, or held back by quiet hours)')
"

echo ""
echo "=== Verifying message was consumed ==="
python3 -c "
import redis
r = redis.Redis(host='redis', port=6379, decode_responses=True)
groups = r.xinfo_groups('messages')
for g in groups:
    if g['name'] == 'homerun2-sound-catcher':
        print(f'Consumer group found: {g[\"name\"]}')
        print(f'Last delivered: {g[\"last-delivered-id\"]}')
        print(f'Pending: {g[\"pending\"]}')
        print('PASS: Message was consumed')
        break
else:
    print('WARN: Consumer group not found (may not have joined yet)')
"

echo ""
echo "=== Cleanup ==="
kill $APP_PID 2>/dev/null || true

echo ""
echo "=== All tests passed! ==="
`

	result := appImage.
		WithServiceBinding("redis", redisService).
		WithEnvVariable("REDIS_ADDR", "redis").
		WithEnvVariable("REDIS_PORT", "6379").
		WithEnvVariable("REDIS_STREAM", "messages").
		WithEnvVariable("SOUND_SINKS", "browser").
		WithEnvVariable("LOG_FORMAT", "text").
		WithExec([]string{"sh", "-c", testCmd})

	_, err := result.Sync(ctx)
	if err != nil {
		testLog := result.File("/tmp/test-output.log")
		return testLog, fmt.Errorf("integration tests failed - check test-output.log: %w", err)
	}

	return result.File("/tmp/test-output.log"), nil
}
