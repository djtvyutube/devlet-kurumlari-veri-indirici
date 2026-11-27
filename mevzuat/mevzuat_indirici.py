"""mevzuat.gov.tr indirici.
Bu kod (Alperen Enes Bayar) tarafından yazılmıştır.

Kullanım:
    python mevzuat_indirici.py index --out ~/data/mevzuat
    python mevzuat_indirici.py fetch --out ~/data/mevzuat --workers 2 --delay 1.0
    python mevzuat_indirici.py status --out ~/data/mevzuat
"""

import argparse
from datetime import datetime, timezone
import json
import os
import random
import shutil
import ssl
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

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
_hata_lock = threading.Lock()


def log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_hata_json(out: Path) -> Dict[str, Any]:
    hata_path = out / "hata.json"
    default_data: Dict[str, Any] = {
        "index_hatalari": {},
        "fetch_hatalari": {},
    }
    if hata_path.exists():
        try:
            with hata_path.open("r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    default_data["index_hatalari"] = loaded.get("index_hatalari", {})
                    default_data["fetch_hatalari"] = loaded.get("fetch_hatalari", {})
        except json.JSONDecodeError as je:
            backup = out / f"hata.json.corrupt.{int(time.time())}"
            log(f"[KRİTİK] hata.json bozuk ({je}), yedekleniyor: {backup}")
            try:
                shutil.copy2(hata_path, backup)
            except Exception:
                pass
        except Exception as e:
            log(f"[HATA] hata.json okunamadı: {e}")
    return default_data


def _save_hata_json(out: Path, data: Dict[str, Any]):
    hata_path = out / "hata.json"
    tmp_path = out / "hata.json.tmp"
    try:
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp_path.replace(hata_path)
    except Exception as e:
        log(f"[HATA] hata.json kaydedilemedi: {e}")


def record_index_error(
    out: Path,
    tur_name: str,
    start: int,
    length: int,
    error_msg: str,
    http_code: Optional[int] = None,
    response_text: Optional[str] = None,
):
    now_iso = _now_utc_iso()
    err_entry = {
        "tur_name": tur_name,
        "start": start,
        "length": length,
        "error": error_msg,
        "http_code": http_code,
        "response_snippet": (response_text or "")[:300] if response_text else None,
        "timestamp_utc": now_iso,
        "t": time.time(),
    }
    try:
        with (out / "index_errors.jsonl").open("a", encoding="utf-8") as ef:
            ef.write(json.dumps(err_entry, ensure_ascii=False) + "\n")
    except Exception as e:
        log(f"[HATA] index_errors.jsonl yazılamadı: {e}")

    with _hata_lock:
        data = _load_hata_json(out)
        key = f"{tur_name}_start_{start}"
        existing = data["index_hatalari"].get(key, {})
        data["index_hatalari"][key] = {
            "tur_name": tur_name,
            "start": start,
            "length": length,
            "hata_nedeni": error_msg,
            "http_kodu": http_code,
            "yanit_ozeti": (response_text or "")[:300] if response_text else None,
            "deneme_sayisi": existing.get("deneme_sayisi", 0) + 1,
            "son_deneme_utc": now_iso,
        }
        _save_hata_json(out, data)


def remove_index_error(out: Path, tur_name: str, start: int):
    hata_path = out / "hata.json"
    if not hata_path.exists():
        return
    with _hata_lock:
        data = _load_hata_json(out)
        key = f"{tur_name}_start_{start}"
        if key in data["index_hatalari"]:
            del data["index_hatalari"][key]
            _save_hata_json(out, data)


def record_fetch_error(
    out: Path,
    did: str,
    kind: str,
    rel: str,
    url: str,
    status: str,
    error_msg: str,
    http_code: Optional[int] = None,
):
    now_iso = _now_utc_iso()
    with _hata_lock:
        data = _load_hata_json(out)
        existing = data["fetch_hatalari"].get(rel, {})
        data["fetch_hatalari"][rel] = {
            "doc_id": did,
            "kind": kind,
            "rel": rel,
            "url": url,
            "status": status,
            "hata_nedeni": error_msg,
            "http_kodu": http_code,
            "kalici_hata": (status == "missing" or http_code == 404),
            "deneme_sayisi": existing.get("deneme_sayisi", 0) + 1,
            "son_deneme_utc": now_iso,
        }
        _save_hata_json(out, data)


def remove_fetch_error(out: Path, rel: str):
    hata_path = out / "hata.json"
    if not hata_path.exists():
        return
    with _hata_lock:
        data = _load_hata_json(out)
        if rel in data["fetch_hatalari"]:
            del data["fetch_hatalari"][rel]
            _save_hata_json(out, data)


def _get_system_ca_pem() -> str:
    candidates = [
        SYSTEM_CA,
        "/etc/pki/tls/certs/ca-bundle.crt",
        "/etc/ssl/cert.pem",
    ]
    default_verify = ssl.get_default_verify_paths()
    if default_verify.cafile:
        candidates.append(default_verify.cafile)
    try:
        req_ca = getattr(requests.utils, "DEFAULT_CA_BUNDLE_PATH", None)
        if req_ca:
            candidates.append(str(req_ca))
    except Exception:
        pass
    try:
        import certifi
        candidates.append(certifi.where())
    except Exception:
        pass

    for c_path in candidates:
        if c_path and Path(c_path).is_file():
            return Path(c_path).read_text(encoding="utf-8", errors="replace")

    raise FileNotFoundError("Sistem CA sertifika paketi bulunamadı (certifi paketini yükleyin).")


def ensure_ca_bundle(out: Path) -> str:
    bundle = out / "ca_bundle.pem"
    if bundle.exists() and bundle.stat().st_size > 0:
        return str(bundle)
    der = requests.get(INTERMEDIATE_URL, timeout=30).content
    pem = ssl.DER_cert_to_PEM_cert(der)
    base_ca_pem = _get_system_ca_pem()
    tmp_bundle = out / "ca_bundle.pem.tmp"
    tmp_bundle.write_text(base_ca_pem + "\n" + pem, encoding="utf-8")
    tmp_bundle.replace(bundle)
    return str(bundle)


def session(ca: str) -> requests.Session:
    s = getattr(_tls, "s", None)
    if s is None:
        s = requests.Session()
        s.headers["User-Agent"] = UA
        s.verify = ca
        _tls.s = s
    return s


def request(ca, method, url, retries=5, **kw) -> Tuple[Optional[requests.Response], Optional[int], Optional[str]]:
    last_code: Optional[int] = None
    last_err: str = "Bilinmeyen bağlantı hatası"

    for attempt in range(retries):
        try:
            r = session(ca).request(method, url, timeout=60, **kw)
            last_code = r.status_code
            if r.status_code == 404 or "ErrorPage?code=404" in r.url:
                return None, 404, f"HTTP 404 Not Found ({r.url})"
            if r.status_code in (429, 500, 502, 503, 504):
                last_err = f"HTTP {r.status_code}"
                raise requests.HTTPError(last_err, response=r)
            r.raise_for_status()
            return r, r.status_code, None
        except (requests.RequestException, OSError) as e:
            if isinstance(e, requests.HTTPError) and getattr(e, "response", None) is not None:
                last_code = e.response.status_code
            last_err = f"{type(e).__name__}: {e}"
            wait = min(300, 5 * 2**attempt) + random.random() * 3
            log(f"  retry {attempt + 1}/{retries} {url} ({last_err}); {wait:.0f}s")
            time.sleep(wait)

    raise RuntimeError(f"{retries} deneme sonunda başarısız ({url}) | Son hata: {last_err} (HTTP {last_code})")


def doc_id(row) -> str:
    tur, tertip, no = row["mevzuatTur"], row["mevzuatTertip"], row["mevzuatNo"]
    if row.get("fileType") == 2:
        stem = row["url"].rsplit("/", 1)[-1].rsplit(".", 1)[0]
        return f"{tur}.{stem}"
    return f"{tur}.{tertip}.{no}"


def list_page(ca, tur_name, start, length) -> Tuple[Optional[Dict[str, Any]], Optional[int], Optional[str], Optional[str]]:
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
    last_code: Optional[int] = None
    last_err: str = "Bilinmeyen hata"
    last_text: Optional[str] = None

    for attempt in range(6):
        try:
            r, http_code, req_err = request(ca, "POST", f"{BASE}/anasayfa/MevzuatDatatable", json=payload)
            last_code = http_code
        except Exception as e:
            last_err = str(e)
            wait = 30 * 2**attempt
            log(f"  İstek hatası ({tur_name} start={start}): {last_err}; {wait}s bekle")
            time.sleep(wait)
            continue

        if r is None:
            last_err = req_err or "HTTP 404 / Yanıt alınamadı"
            wait = 30 * 2**attempt
            log(f"  Yanıt alınamadı / 404 ({tur_name} start={start}); {wait}s bekle")
            time.sleep(wait)
            continue

        last_text = r.text
        try:
            data = r.json()
            if isinstance(data, dict) and "data" in data:
                return data, r.status_code, None, None
            last_err = f"Beklenmeyen JSON yapısı (keys: {list(data.keys()) if isinstance(data, dict) else type(data).__name__})"
        except ValueError as ve:
            last_err = f"JSONDecodeError ({ve}) - Content-Type: {r.headers.get('content-type')}"

        wait = 30 * 2**attempt
        log(f"  Geçerli JSON değil ({tur_name} start={start}, {last_err}, "
            f"{(last_text or '')[:120]!r}); {wait}s bekle")
        time.sleep(wait)

    return None, last_code, f"Liste API'si 6 denemede geçerli JSON döndürmedi ({tur_name} start={start}): {last_err}", last_text


def cmd_index(args):
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    ca = ensure_ca_bundle(out)
    manifest = out / "manifest.jsonl"
    seen = set()
    if manifest.exists():
        with manifest.open("r", encoding="utf-8") as f:
            seen = {json.loads(l)["doc_id"] for l in f if l.strip()}
    log(f"index: {len(seen)} kayıt zaten manifest'te")

    try:
        session(ca).get(BASE + "/", timeout=60)
    except Exception as e:
        log(f"[UYARI] Başlangıç oturum çerezi alınamadı ({e}), devam ediliyor...")

    total_errors = 0
    with manifest.open("a", encoding="utf-8") as f:
        for tur_name in TYPES:
            start, new, total = 0, 0, None
            consecutive_failures = 0
            while total is None or start < total:
                d, http_code, err_msg, raw_text = list_page(ca, tur_name, start, args.page_size)
                if d is None:
                    total_errors += 1
                    consecutive_failures += 1
                    log(f"  [HATA] {err_msg} -> hata.json ve index_errors.jsonl'e kaydedildi.")
                    record_index_error(
                        out=out,
                        tur_name=tur_name,
                        start=start,
                        length=args.page_size,
                        error_msg=err_msg or "Liste sayfası alınamadı",
                        http_code=http_code,
                        response_text=raw_text,
                    )
                    if total is not None and (start + args.page_size) < total and consecutive_failures < 3:
                        log(f"  [UYARI] {tur_name} start={start} atlanıyor, sonraki sayfaya ({start + args.page_size}) geçiliyor...")
                        start += args.page_size
                        time.sleep(args.delay)
                        continue
                    else:
                        log(f"  [UYARI] {tur_name} türü için kalan sayfalar alınamadı, sonraki mevzuat türüne geçiliyor.")
                        break

                consecutive_failures = 0
                remove_index_error(out, tur_name, start)

                total = d.get("recordsTotal", 0)
                rows = d.get("data") or []
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
            log(f"index: {tur_name:45s} toplam={total if total is not None else '?':>6} yeni={new}")
    log(f"index bitti: {len(seen)} tekil belge -> {manifest} (Hatalı sayfa: {total_errors})")


def targets(row):
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
    head = body[:600].lstrip()
    return head.startswith(b"<!DOCTYPE") and b"<title>" in head


def fetch_one(ca, out: Path, kind, url, rel, delay) -> Tuple[str, int, Optional[str], Optional[int]]:
    path = out / rel
    last_http_code: Optional[int] = None

    for attempt in range(7):
        try:
            r, http_code, req_err = request(ca, "GET", url)
            last_http_code = http_code
        except Exception as e:
            return "error", 0, str(e), last_http_code

        time.sleep(delay)
        if r is None:
            return "missing", 0, req_err or "HTTP 404 Not Found", 404

        body = r.content
        if not is_block_page(body):
            break
        wait = 30 * 2**attempt
        log(f"  engel sayfası ({rel}); {wait}s bekle")
        time.sleep(wait)
    else:
        return "error", 0, "7 denemede hız sınırı / engel sayfası (block page) aşılamadı", last_http_code

    if kind == "pdf" and not body.startswith(b"%PDF"):
        snippet = body[:120].decode("utf-8", errors="replace").strip()
        return (
            "not_pdf",
            len(body),
            f"İndirilen içerik %PDF imzası taşımıyor (Content-Type: {r.headers.get('Content-Type')}, İlk baytlar: {snippet!r})",
            last_http_code,
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_bytes(body)
    tmp.replace(path)
    return "ok", len(body), None, last_http_code


def load_done(status_log: Path, retry_missing: bool = False, retry_not_pdf: bool = True):
    latest_status: Dict[str, str] = {}
    if status_log.exists():
        with status_log.open("r", encoding="utf-8") as f:
            for l in f:
                if not l.strip():
                    continue
                try:
                    e = json.loads(l)
                    rel = e.get("rel")
                    st = e.get("status")
                    if rel and st:
                        latest_status[rel] = st
                except Exception:
                    continue

    accepted_statuses = {"ok"}
    if not retry_missing:
        accepted_statuses.add("missing")
    if not retry_not_pdf:
        accepted_statuses.add("not_pdf")

    return {rel for rel, st in latest_status.items() if st in accepted_statuses}


def cmd_fetch(args):
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    ca = ensure_ca_bundle(out)
    manifest_path = out / "manifest.jsonl"
    if not manifest_path.exists():
        log(f"[HATA] {manifest_path} bulunamadı. Önce 'index' komutunu çalıştırın.")
        return

    with manifest_path.open("r", encoding="utf-8") as f:
        rows = [json.loads(l) for l in f if l.strip()]
    status_log = out / "fetch_log.jsonl"
    done = load_done(
        status_log,
        retry_missing=getattr(args, "retry_missing", False),
        retry_not_pdf=not getattr(args, "skip_not_pdf", False),
    )

    jobs = []
    for row in rows:
        for kind, url, rel in targets(row):
            if (out / rel).exists() or rel in done:
                continue
            if args.only and kind != args.only:
                continue
            jobs.append((row["doc_id"], kind, url, rel))
    if args.limit:
        jobs = jobs[: args.limit]
    log(f"fetch: {len(rows)} belge, {len(done)} dosya tamam/atlandı, {len(jobs)} iş kaldı "
        f"(workers={args.workers}, delay={args.delay}s)")
    if not jobs:
        return

    lock = threading.Lock()
    stats = {"ok": 0, "missing": 0, "not_pdf": 0, "error": 0, "bytes": 0}
    t0 = time.time()
    with status_log.open("a", encoding="utf-8") as logf, ThreadPoolExecutor(args.workers) as ex:
        futs = {ex.submit(fetch_one, ca, out, k, u, r, args.delay): (d, k, u, r) for d, k, u, r in jobs}
        for i, fut in enumerate(as_completed(futs), 1):
            did, kind, url, rel = futs[fut]
            try:
                status, size, error_reason, http_code = fut.result()
            except Exception as e:
                status, size, error_reason, http_code = "error", 0, f"{type(e).__name__}: {e}", None
                log(f"  HATA {did} {kind}: {error_reason}")

            if status == "ok":
                remove_fetch_error(out, rel)
            else:
                if status in ("error", "not_pdf"):
                    log(f"  [{status.upper()}] {did} ({kind}): {error_reason}")
                record_fetch_error(
                    out=out,
                    did=did,
                    kind=kind,
                    rel=rel,
                    url=url,
                    status=status,
                    error_msg=error_reason or status,
                    http_code=http_code,
                )

            with lock:
                stats[status] += 1
                stats["bytes"] += size
                log_record: Dict[str, Any] = {
                    "doc_id": did,
                    "kind": kind,
                    "rel": rel,
                    "url": url,
                    "status": status,
                    "http_code": http_code,
                    "bytes": size,
                    "t": time.time(),
                }
                if error_reason:
                    log_record["error"] = error_reason
                logf.write(json.dumps(log_record, ensure_ascii=False) + "\n")
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
    manifest_path = out / "manifest.jsonl"
    rows = []
    if manifest_path.exists():
        with manifest_path.open("r", encoding="utf-8") as f:
            rows = [json.loads(l) for l in f if l.strip()]
    by_type = {}
    for r in rows:
        by_type[r["mevzuatTurEnumString"]] = by_type.get(r["mevzuatTurEnumString"], 0) + 1
    print(f"manifest: {len(rows)} belge")
    for k, v in sorted(by_type.items(), key=lambda x: -x[1]):
        print(f"  {k:45s} {v:>6}")
    latest = {}
    log_path = out / "fetch_log.jsonl"
    if log_path.exists():
        with log_path.open("r", encoding="utf-8") as f:
            for l in f:
                if not l.strip():
                    continue
                e = json.loads(l)
                latest[e["rel"]] = e
    counts = {}
    for e in latest.values():
        key = (e["kind"], e["status"])
        counts[key] = counts.get(key, 0) + 1
    print("fetch:")
    for (kind, st), n in sorted(counts.items()):
        print(f"  {kind:5s} {st:8s} {n:>6}")
    total = sum(e["bytes"] for e in latest.values() if e.get("status") == "ok")
    print(f"  toplam {total / 1e9:.2f} GB")

    hata_path = out / "hata.json"
    if hata_path.exists():
        hata_data = _load_hata_json(out)
        idx_errs = len(hata_data.get("index_hatalari", {}))
        fetch_errs = len(hata_data.get("fetch_hatalari", {}))
        print(f"hata.json: index_hatalari={idx_errs}, fetch_hatalari={fetch_errs}")


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
    pf.add_argument("--retry-missing", action="store_true", help="Daha önce 404 (missing) dönen dosyaları da yeniden dene")
    pf.add_argument("--skip-not-pdf", action="store_true", help="Daha önce not_pdf dönen dosyaları yeniden deneme")
    ps = sub.add_parser("status")
    ps.add_argument("--out", required=True)
    args = p.parse_args()
    {"index": cmd_index, "fetch": cmd_fetch, "status": cmd_status}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
