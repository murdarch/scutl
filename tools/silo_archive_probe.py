"""cst-px98.2 build-time probe: byte-check Vultr Archival-tier semantics
on a THROWAWAY bucket (never the live silo bucket).

Questions (from the 2026-09-16 design draft, comment on cst-px98.2):
  P1  Does the Archival tier accept a direct PUT, and what storage class
      does the object land in (HEAD x-amz-storage-class)?
  P2  Is x-amz-storage-class honored on PUT (STANDARD vs ARCHIVE)?
  P3  Is an archived object GET-able synchronously, or does it return
      403/InvalidObjectState until restored?
  P4  Restore call shape: POST ?restore + RestoreRequest XML — accepted?
      What do x-amz-restore / storage-class headers show during/after?
  P5  Lifecycle: does PUT ?lifecycle with a transition-to-archive rule
      parse and read back?

Usage: python3 silo_archive_probe.py <keyfile> [--teardown]
Keyfile: two lines, access then secret. All output is redacted of keys.
"""
import hashlib
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]
                       / "recipes/durable-object-storage/silo"))
from scutl_silo.s3live import EMPTY_SHA256, sign_v4  # noqa: E402

HOST = "sjc1.vultrobjects.com"
BUCKET = "silo-archive-probe-20261105"
BODY = b"silo archive probe payload cst-px98.2\n"
SHA = hashlib.sha256(BODY).hexdigest()


def req(access, secret, method, key="", query="", body=b"", extra=None):
    path = f"/{BUCKET}" + (f"/{key}" if key else "")
    payload_hash = hashlib.sha256(body).hexdigest() if body else EMPTY_SHA256
    headers = sign_v4(method, HOST, path, query, dict(extra or {}),
                      payload_hash, access, secret, "us-east-1",
                      datetime.now(timezone.utc))
    url = f"https://{HOST}{urllib.parse.quote(path, safe='/-_.~')}"
    if query:
        url += f"?{query}"
    r = urllib.request.Request(url, data=body or None, method=method,
                               headers=headers)
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def show(tag, status, headers, body):
    keep = {k: v for k, v in headers.items() if k.lower() in (
        "x-amz-storage-class", "x-amz-restore", "etag", "content-length",
        "x-amz-meta-probe")}
    print(f"{tag}: HTTP {status} {keep} {body[:220]!r}")


def main():
    raw = Path(sys.argv[1]).read_text()
    try:
        import json as _json
        creds = _json.loads(raw)
        access, secret = creds["access"], creds["secret"]
    except (ValueError, KeyError, TypeError):
        access, secret = raw.split()[:2]
    if "--teardown" in sys.argv:
        # Enumerate the bucket rather than assume a fixed key set: later
        # probes (e.g. the day-2 large read-test object) leave keys the
        # hardcoded list misses, so a blind DELETE bucket hits
        # BucketNotEmpty (409). List v2, delete every key, then the bucket.
        st, hd, body = req(access, secret, "GET", query="list-type=2")
        import re
        keys = re.findall(rb"<Key>(.*?)</Key>", body)
        print(f"LIST: HTTP {st}, {len(keys)} object(s): "
              f"{[k.decode() for k in keys]}")
        for kb in keys:
            k = kb.decode()
            show(f"DELETE {k}", *req(access, secret, "DELETE", k))
        show("DELETE bucket", *req(access, secret, "DELETE"))
        return

    show("PUT bucket", *req(access, secret, "PUT"))

    # P1: plain PUT, no storage-class header
    show("P1 PUT plain", *req(access, secret, "PUT", "p1-plain", body=BODY))
    show("P1 HEAD", *req(access, secret, "HEAD", "p1-plain"))
    show("P1 GET", *req(access, secret, "GET", "p1-plain"))

    # P2: explicit storage classes
    show("P2 PUT ARCHIVE", *req(access, secret, "PUT", "p2-explicit-archive",
                                body=BODY,
                                extra={"x-amz-storage-class": "ARCHIVE"}))
    show("P2 HEAD archive", *req(access, secret, "HEAD",
                                 "p2-explicit-archive"))
    show("P2 PUT STANDARD", *req(access, secret, "PUT",
                                 "p2-explicit-standard", body=BODY,
                                 extra={"x-amz-storage-class": "STANDARD"}))
    show("P2 HEAD standard", *req(access, secret, "HEAD",
                                  "p2-explicit-standard"))

    # P3: GET the archived object cold
    show("P3 GET archived", *req(access, secret, "GET",
                                 "p2-explicit-archive"))

    # P4: restore request
    xml = (b'<RestoreRequest xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
           b"<Days>1</Days></RestoreRequest>")
    show("P4 POST restore", *req(access, secret, "POST",
                                 "p2-explicit-archive", query="restore",
                                 body=xml))
    for i in range(6):
        time.sleep(10)
        st, hd, bd = req(access, secret, "HEAD", "p2-explicit-archive")
        show(f"P4 HEAD +{(i + 1) * 10}s", st, hd, bd)
        st2, hd2, bd2 = req(access, secret, "GET", "p2-explicit-archive")
        print(f"P4 GET +{(i + 1) * 10}s: HTTP {st2} "
              f"match={hashlib.sha256(bd2).hexdigest() == SHA}")
        if st2 == 200:
            break

    # P5: lifecycle transition rule
    lc = (b'<LifecycleConfiguration><Rule><ID>to-archive</ID>'
          b"<Filter><Prefix></Prefix></Filter><Status>Enabled</Status>"
          b"<Transition><Days>1</Days>"
          b"<StorageClass>ARCHIVE</StorageClass></Transition>"
          b"</Rule></LifecycleConfiguration>")
    lc_md5 = __import__("base64").b64encode(
        __import__("hashlib").md5(lc).digest()).decode()
    show("P5 PUT lifecycle", *req(access, secret, "PUT", query="lifecycle",
                                  body=lc, extra={"content-md5": lc_md5}))
    show("P5 GET lifecycle", *req(access, secret, "GET", query="lifecycle"))


if __name__ == "__main__":
    main()
