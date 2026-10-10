"""Download the pinned, unmodified upstream management release and its license."""

import hashlib
from pathlib import Path
import urllib.request


VERSION = "v1.26.0"
COMMIT = "c9e2f7cb0b065c13211285d9525bcc8761f17837"
SHA256 = "aae0653e65dc81668ded759c8e93d474ce295e0b159a1ca205bf0b6316655df1"
REPOSITORY = "router-for-me/Cli-Proxy-API-Management-Center"
TARGET = Path(__file__).resolve().parents[1] / "fake_transfer_station" / "static"


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "fake-transfer-station-asset-fetcher"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def main():
    if not TARGET.is_dir():
        raise SystemExit(f"Static directory does not exist: {TARGET}")
    html = fetch(f"https://github.com/{REPOSITORY}/releases/download/{VERSION}/management.html")
    actual = hashlib.sha256(html).hexdigest()
    if actual != SHA256:
        raise SystemExit(f"HTML checksum mismatch: expected {SHA256}, got {actual}")
    license_body = fetch(f"https://raw.githubusercontent.com/{REPOSITORY}/{COMMIT}/LICENSE")
    if not license_body.startswith(b"MIT License"):
        raise SystemExit("Unexpected upstream license response")
    # Verify all downloads before touching the vendored files; retain exact bytes.
    (TARGET / "management.html").write_bytes(html)
    (TARGET / "management.LICENSE").write_bytes(license_body)
    print(f"Downloaded {VERSION}: {len(html)} bytes; SHA-256 {actual}")
    print("Upstream MIT license saved alongside the unmodified HTML.")


if __name__ == "__main__":
    main()
