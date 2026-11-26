"""mevzuat.gov.tr scraper — P2 (e-devlet/mevzuat korpusu).
Bu kod (Alperen Enes Bayar) tarafından yazılmıştır.

Üç aşama:
    index    liste API'sinden tüm belgeleri manifest.jsonl'e yazar (artımlı)
    fetch    her belge için HTML metni + PDF indirir (kaldığı yerden devam eder)
    status   indirme durumunu özetler

Kullanım:
    python mevzuat.py index --out ~/data/mevzuat
    python mevzuat.py fetch --out ~/data/mevzuat --workers 2 --delay 1.0
    python mevzuat.py status --out ~/data/mevzuat

Not: www.mevzuat.gov.tr ara sertifikayı (GeoTrust TLS RSA CA G1) göndermiyor;
ensure_ca_bundle() onu DigiCert'ten indirip sistem CA'larıyla birleştirir.
Ara sertifika kök sertifikaya karşı doğrulandığı için bu güvenli.
"""

import argparse
import json
import os
import random
import ssl
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

BASE = "https://www.mevzuat.gov.tr"
UA = "Mozilla/5.0 (X11; Linux x86_64) RIP-tokenizer-research"
INTERMEDIATE_URL = "http://cacerts.digicert.com/GeoTrustTLSRSACAG1.crt"
SYSTEM_CA = "/etc/ssl/certs/ca-certificates.crt"

TYPES = [
    "Kanun",
    "MulgaKanun",
    "KHK",
    "Tuzuk",
    "CumhurbaskaniKararnameleri",
    "CumhurbaskanligiVeBakanlarKuruluYonetmelik",
    "KurumVeKurulusYonetmeligi",
    "Yonetmelik",
    "TumYonetmelik",
    "Teblig",
    "CumhurbaskaniKararlari",
    "CumhurbaskanligiGenelgeleri",
]

YONETMELIK_DIR_TURS = {7, 9}

_tls = threading.local()


def log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def ensure_ca_bundle(out: Path) -> str:
    bundle = out / "ca_bundle.pem"
    if bundle.exists():
        return str(bundle)
    der = requests.get(INTERMEDIATE_URL, timeout=30).content
    pem = ssl.DER_cert_to_PEM_cert(der)
    bundle.write_text(Path(SYSTEM_CA).read_text() + "\n" + pem)
    return str(bundle)


def session(ca: str) -> requests.Session:
    s = getattr(_tls, "s", None)
    if s is None:
        s = requests.Session()
        s.headers["User-Agent"] = UA
        s.verify = ca
        _tls.s = s
    return s


def request(ca, method, url, retries=5, **kw):
    """Geçici hatalarda üstel geri çekilmeyle tekrar dener. 404 → None."""
    for attempt in range(retries):
        try:
            r = session(ca).request(method, url, timeout=60, **kw)
            if r.status_code == 404 or "ErrorPage?code=404" in r.url:
                return None
            if r.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"HTTP {r.status_code}")
            r.raise_for_status()
            return r
        except (requests.RequestException, OSError) as e:
            wait = min(300, 5 * 2**attempt) + random.random() * 3
            log(f"  retry {attempt + 1}/{retries} {url} ({e}); {wait:.0f}s")
            time.sleep(wait)
    raise RuntimeError(f"failed after {retries} retries: {url}")


def doc_id(row) -> str:
    tur, tertip, no = row["mevzuatTur"], row["mevzuatTertip"], row["mevzuatNo"]
    if row.get("fileType") == 2:
        stem = row["url"].rsplit("/", 1)[-1].rsplit(".", 1)[0]
        return f"{tur}.{stem}"
    return f"{tur}.{tertip}.{no}"


def list_page(ca, tur_name, start, length):
    payload = {
        "draw": 1,
        "start": start,
        "length": length,
        "parameters": {
            "AranacakIfade": "",
            "AranacakYer": "Baslik",
            "TamCumle": False,
            "MevzuatTur": tur_name,
            "GenelArama": False,
        },
    }
    for attempt in range(6):
        r = request(ca, "POST", f"{BASE}/anasayfa/MevzuatDatatable", json=payload)
        try:
            return r.json()
        except ValueError:
            wait = 30 * 2**attempt
            log(f"  JSON değil ({tur_name} start={start}, {r.headers.get('content-type')}, "
                f"{r.text[:120]!r}); {wait}s bekle")
            time.sleep(wait)
    raise RuntimeError(f"liste API'si JSON döndürmüyor: {tur_name} start={start}")


def cmd_index(args):
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    ca = ensure_ca_bundle(out)
    manifest = out / "manifest.jsonl"
    seen = set()
    if manifest.exists():
        with manifest.open() as f:
            seen = {json.loads(l)["doc_id"] for l in f}
    log(f"index: {len(seen)} kayıt zaten manifest'te")

    session(ca).get(BASE + "/", timeout=60)
    with manifest.open("a") as f:
        for tur_name in TYPES:
            start, new, total = 0, 0, None
            while total is None or start < total:
                d = list_page(ca, tur_name, start, args.page_size)
                total = d["recordsTotal"]
                rows = d["data"]
                if not rows:
                    break
                for row in rows:
                    did = doc_id(row)
                    if did in seen:
                        continue
                    seen.add(did)
                    new += 1
                    row["doc_id"] = did
                    row["list_type"] = tur_name
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
                f.flush()
                start += len(rows)
                time.sleep(args.delay)
            log(f"index: {tur_name:45s} toplam={total:>6} yeni={new}")
    log(f"index bitti: {len(seen)} tekil belge -> {manifest}")


def targets(row):
    """(tür, url, yerel_yol) listesi."""
    tur, tertip, no = row["mevzuatTur"], row["mevzuatTertip"], row["mevzuatNo"]
    did = row["doc_id"]
    if row.get("fileType") == 2:
        url = row["url"] if row["url"].startswith("http") else f"{BASE}/{row['url']}"
        return [("pdf", url, f"pdf/{tur}/{did}.pdf")]
    sub = "yonetmelik/" if int(tur) in YONETMELIK_DIR_TURS else ""
    return [
        (
            "html",
            f"{BASE}/anasayfa/MevzuatFihristDetayIframe?MevzuatTur={tur}&MevzuatNo={no}&MevzuatTertip={tertip}",
            f"html/{tur}/{did}.html",
        ),
        ("pdf", f"{BASE}/MevzuatMetin/{sub}{tur}.{tertip}.{no}.pdf", f"pdf/{tur}/{did}.pdf"),
    ]


def is_block_page(body: bytes) -> bool:
    """Hız sınırında dönen 200'lü site sayfası. Gerçek iframe metni DOCTYPE/title içermez."""
    head = body[:600].lstrip()
    return head.startswith(b"<!DOCTYPE") and b"<title>" in head


def fetch_one(ca, out: Path, kind, url, rel, delay):
    path = out / rel
    for attempt in range(7):
        r = request(ca, "GET", url)
        time.sleep(delay)
        if r is None:
            return "missing", 0
        body = r.content
        if not is_block_page(body):
            break
        wait = 30 * 2**attempt
        log(f"  engel sayfası ({rel}); {wait}s bekle")
        time.sleep(wait)
    else:
        return "error", 0
    if kind == "pdf" and not body.startswith(b"%PDF"):
        return "not_pdf", len(body)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_bytes(body)
    tmp.replace(path)
    return "ok", len(body)


def load_done(status_log: Path):
    done = set()
    if status_log.exists():
        with status_log.open() as f:
            for l in f:
                e = json.loads(l)
                if e["status"] in ("ok", "missing", "not_pdf"):
                    done.add(e["rel"])
    return done


def cmd_fetch(args):
    out = Path(args.out).expanduser()
    ca = ensure_ca_bundle(out)
    with (out / "manifest.jsonl").open() as f:
        rows = [json.loads(l) for l in f]
    status_log = out / "fetch_log.jsonl"
    done = load_done(status_log)

    jobs = []
    for row in rows:
        for kind, url, rel in targets(row):
            if rel in done or (out / rel).exists():
                continue
            if args.only and kind != args.only:
                continue
            jobs.append((row["doc_id"], kind, url, rel))
    if args.limit:
        jobs = jobs[: args.limit]
    log(f"fetch: {len(rows)} belge, {len(done)} dosya tamam, {len(jobs)} iş kaldı "
        f"(workers={args.workers}, delay={args.delay}s)")
    if not jobs:
        return

    lock = threading.Lock()
    stats = {"ok": 0, "missing": 0, "not_pdf": 0, "error": 0, "bytes": 0}
    t0 = time.time()
    with status_log.open("a") as logf, ThreadPoolExecutor(args.workers) as ex:
        futs = {ex.submit(fetch_one, ca, out, k, u, r, args.delay): (d, k, u, r) for d, k, u, r in jobs}
        for i, fut in enumerate(as_completed(futs), 1):
            did, kind, url, rel = futs[fut]
            try:
                status, size = fut.result()
            except Exception as e:
                status, size = "error", 0
                log(f"  HATA {did} {kind}: {e}")
            with lock:
                stats[status] += 1
                stats["bytes"] += size
                logf.write(json.dumps({"doc_id": did, "kind": kind, "rel": rel, "url": url,
                                       "status": status, "bytes": size, "t": time.time()}) + "\n")
                logf.flush()
            if i % 50 == 0 or i == len(jobs):
                el = time.time() - t0
                eta = el / i * (len(jobs) - i)
                log(f"{i:>6}/{len(jobs)} ({100 * i / len(jobs):5.1f}%)  ok={stats['ok']} "
                    f"missing={stats['missing']} not_pdf={stats['not_pdf']} error={stats['error']}  "
                    f"{stats['bytes'] / 1e9:.2f} GB  ETA {eta / 3600:.1f} sa")
    log(f"fetch bitti: {stats}")


def cmd_status(args):
    out = Path(args.out).expanduser()
    with (out / "manifest.jsonl").open() as f:
        rows = [json.loads(l) for l in f]
    by_type = {}
    for r in rows:
        by_type[r["mevzuatTurEnumString"]] = by_type.get(r["mevzuatTurEnumString"], 0) + 1
    print(f"manifest: {len(rows)} belge")
    for k, v in sorted(by_type.items(), key=lambda x: -x[1]):
        print(f"  {k:45s} {v:>6}")
    latest = {}
    log_path = out / "fetch_log.jsonl"
    if log_path.exists():
        with log_path.open() as f:
            for l in f:
                e = json.loads(l)
                latest[e["rel"]] = e
    counts = {}
    for e in latest.values():
        key = (e["kind"], e["status"])
        counts[key] = counts.get(key, 0) + 1
    print("fetch:")
    for (kind, st), n in sorted(counts.items()):
        print(f"  {kind:5s} {st:8s} {n:>6}")
    total = sum(e["bytes"] for e in latest.values())
    print(f"  toplam {total / 1e9:.2f} GB")


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    pi = sub.add_parser("index")
    pi.add_argument("--out", required=True)
    pi.add_argument("--page-size", type=int, default=100)
    pi.add_argument("--delay", type=float, default=1.0)
    pf = sub.add_parser("fetch")
    pf.add_argument("--out", required=True)
    pf.add_argument("--workers", type=int, default=2)
    pf.add_argument("--delay", type=float, default=1.0, help="her istekten sonra worker başına bekleme")
    pf.add_argument("--limit", type=int, default=0)
    pf.add_argument("--only", choices=["html", "pdf"])
    ps = sub.add_parser("status")
    ps.add_argument("--out", required=True)
    args = p.parse_args()
    {"index": cmd_index, "fetch": cmd_fetch, "status": cmd_status}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
