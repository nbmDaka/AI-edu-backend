"""Exercise the built image, without a provider key or any production data."""
import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

base = 'http://127.0.0.1:3301'
deadline = time.monotonic() + 90
while True:
    try:
        with urlopen(base + '/api/health', timeout=3) as response:
            health = json.load(response)
        assert health['status'] == 'ok', health['status']
        assert health['database']['status'] == 'ok', health['database']['status']
        break
    except (OSError, URLError):
        if time.monotonic() > deadline:
            raise
        time.sleep(1)

for key, expected in [('', 401), ('ci-only-service-key', 400)]:
    request = Request(base + '/api/chat', data=b'{}', method='POST',
                      headers={'Content-Type': 'application/json', 'X-API-Key': key})
    try:
        with urlopen(request, timeout=10) as response:
            status = response.status
    except HTTPError as error:
        status = error.code
        error.close()
    assert status == expected, (status, expected)
print('Gateway image: healthy SQLite, required service authentication, validated chat input.')
