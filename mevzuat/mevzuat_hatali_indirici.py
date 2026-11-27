"""
Mevzuat Bilgi Sistemi (mevzuat.gov.tr) Hatalı / Eksik Kayıt İndirici
Bu kod (Alperen Enes Bayar) tarafından yazılmıştır.

mevzuat_indirici.py tarafından oluşturulan hata.json (ve fetch_log.jsonl / index_errors.jsonl)
içindeki:
1) İndeksleme hatalarını (index_hatalari),
2) Dosya indirme hatalarını (fetch_hatalari: error, not_pdf ve istenirse missing/404)
yeniden dener. Başarılı olanları manifest.jsonl ve diske kaydedip hata.json'dan temizler.
"""

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, Any, List, Tuple

from mevzuat_indirici import (
    BASE,
    ensure_ca_bundle,
    session,
    log,
    doc_id,
    list_page,
    fetch_one,
    _load_hata_json,
    record_index_error,
    remove_index_error,
    record_fetch_error,
    remove_fetch_error,
)


def retry_failed_index_pages(ca: str, out: Path, delay: float) -> int:
    hata_data = _load_hata_json(out)
    index_hatalari: Dict[str, Any] = dict(hata_data.get("index_hatalari", {}))
    if not index_hatalari:
        log("Yeniden denenecek hatalı index sayfası bulunmuyor.")
        return 0

    manifest = out / "manifest.jsonl"
    seen = set()
    if manifest.exists():
        with manifest.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    try:
                        seen.add(json.loads(line)["doc_id"])
                    except Exception:
                        pass

    try:
        session(ca).get(BASE + "/", timeout=60)
    except Exception:
        pass

    log(f"Toplam {len(index_hatalari)} adet hatalı index sayfası yeniden deneniyor...")
    recovered_docs = 0

    with manifest.open("a", encoding="utf-8") as f:
        for idx, (key, info) in enumerate(index_hatalari.items(), start=1):
            tur_name = info.get("tur_name")
            start = int(info.get("start", 0))
            length = int(info.get("length", 100))
            onceki_deneme = info.get("deneme_sayisi", 1)

            log(f"[Index {idx}/{len(index_hatalari)}] Sorgulanıyor: {tur_name} start={start} (Önceki deneme: {onceki_deneme})")
            d, http_code, err_msg, raw_text = list_page(ca, tur_name, start, length)
            if d is None:
                log(f"  [HATA] {tur_name} start={start} yine alınamadı (Deneme #{onceki_deneme + 1}): {err_msg}")
                record_index_error(
                    out=out,
                    tur_name=tur_name,
                    start=start,
                    length=length,
                    error_msg=err_msg or "Liste sayfası alınamadı",
                    http_code=http_code,
                    response_text=raw_text,
                )
                continue

            remove_index_error(out, tur_name, start)
            rows = d.get("data") or []
            added = 0
            for row in rows:
                did = doc_id(row)
                if did in seen:
                    continue
                seen.add(did)
                added += 1
                recovered_docs += 1
                row["doc_id"] = did
                row["list_type"] = tur_name
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
            log(f"  [+] Kurtarıldı: {tur_name} start={start} ({len(rows)} kayıt geldi, {added} yeni eklendi).")
            time.sleep(delay)

    return recovered_docs


def _collect_failed_fetch_jobs(out: Path, include_missing: bool = False, only: str = None) -> List[Tuple[str, str, str, str]]:
    jobs_by_rel: Dict[str, Tuple[str, str, str, str]] = {}
    skipped_missing = 0

    hata_data = _load_hata_json(out)
    for rel, info in hata_data.get("fetch_hatalari", {}).items():
        if (out / rel).exists():
            remove_fetch_error(out, rel)
            continue
        st = info.get("status")
        is_missing = (st == "missing") or (info.get("http_kodu") == 404) or bool(info.get("kalici_hata"))
        if is_missing and not include_missing:
            skipped_missing += 1
            continue
        kind = info.get("kind") or ("pdf" if rel.endswith(".pdf") else "html")
        if only and kind != only:
            continue
        did = info.get("doc_id") or Path(rel).stem
        url = info.get("url") or ""
        if url:
            jobs_by_rel[rel] = (did, kind, url, rel)

    status_log = out / "fetch_log.jsonl"
    if status_log.exists():
        latest_entries: Dict[str, Dict[str, Any]] = {}
        with status_log.open("r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    e = json.loads(line)
                    rel = e.get("rel")
                    if rel:
                        latest_entries[rel] = e
                except Exception:
                    continue

        for rel, e in latest_entries.items():
            if rel in jobs_by_rel or (out / rel).exists():
                continue
            st = e.get("status")
            if st == "ok":
                continue
            if st == "missing" and not include_missing:
                continue
            kind = e.get("kind") or ("pdf" if rel.endswith(".pdf") else "html")
            if only and kind != only:
                continue
            did = e.get("doc_id") or Path(rel).stem
            url = e.get("url") or ""
            if url:
                jobs_by_rel[rel] = (did, kind, url, rel)

    if skipped_missing > 0:
        log(f"Kalıcı 404 (missing) olarak işaretlenmiş {skipped_missing} adet dosya atlandı (--include-missing ile zorlanabilir).")

    return list(jobs_by_rel.values())


def main():
    p = argparse.ArgumentParser(description="Mevzuat Hatalı / Eksik İndirmeleri Yeniden Deneme Aracı")
    p.add_argument("--out", required=True, help="mevzuat_indirici çıktı klasörü")
    p.add_argument("--workers", type=int, default=2, help="Paralel indirme iş parçacığı sayısı (varsayılan: 2)")
    p.add_argument("--delay", type=float, default=1.0, help="İstekler arası bekleme süresi (varsayılan: 1.0)")
    p.add_argument("--limit", type=int, default=0, help="Maksimum denenecek dosya sayısı")
    p.add_argument("--only", choices=["html", "pdf"], help="Sadece html veya sadece pdf hatalarını dene")
    p.add_argument("--include-missing", action="store_true", help="HTTP 404 (missing) dönmüş kayıtları da yeniden dene")
    args = p.parse_args()

    out = Path(args.out).expanduser()
    if not out.exists():
        log(f"[HATA] Çıktı klasörü bulunamadı: {out}")
        return 1

    ca = ensure_ca_bundle(out)

    retry_failed_index_pages(ca, out, args.delay)

    jobs = _collect_failed_fetch_jobs(out, include_missing=args.include_missing, only=args.only)
    if args.limit and args.limit > 0:
        jobs = jobs[: args.limit]

    if not jobs:
        log("Yeniden denenecek hatalı dosya (fetch) kaydı bulunmuyor.")
        return 0

    log(f"Toplam {len(jobs)} adet hatalı dosya yeniden indiriliyor (workers={args.workers}, delay={args.delay}s)...")
    status_log = out / "fetch_log.jsonl"
    lock = threading.Lock()
    stats = {"ok": 0, "missing": 0, "not_pdf": 0, "error": 0, "bytes": 0}

    with status_log.open("a", encoding="utf-8") as logf, ThreadPoolExecutor(args.workers) as ex:
        futs = {ex.submit(fetch_one, ca, out, k, u, r, args.delay): (d, k, u, r) for d, k, u, r in jobs}
        for i, fut in enumerate(as_completed(futs), 1):
            did, kind, url, rel = futs[fut]
            try:
                status, size, error_reason, http_code = fut.result()
            except Exception as e:
                status, size, error_reason, http_code = "error", 0, f"{type(e).__name__}: {e}", None

            if status == "ok":
                remove_fetch_error(out, rel)
                log(f"  [+] [{i}/{len(jobs)}] Kurtarıldı: {rel} ({size} byte)")
            else:
                log(f"  [-] [{i}/{len(jobs)}] Başarısız ({status}): {rel} | Hata: {error_reason}")
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

    log(f"Hatalı kayıtları yeniden deneme tamamlandı: {stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
