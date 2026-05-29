import json
import sys
import urllib.request

BASE = "http://127.0.0.1:5000"


def get_json(path: str):
    with urllib.request.urlopen(BASE + path, timeout=8) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    try:
        status = get_json("/status")
        print("/status OK:", status)

        cams = get_json("/api/cameras")
        print("/api/cameras OK, count:", len(cams))

        vehicles = get_json("/api/vehicles")
        print("/api/vehicles OK, count:", len(vehicles))

        logs = get_json("/api/log?limit=5")
        print("/api/log OK, count:", len(logs))

        print("Smoke test passed.")
        return 0
    except Exception as exc:
        print("Smoke test failed:", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
