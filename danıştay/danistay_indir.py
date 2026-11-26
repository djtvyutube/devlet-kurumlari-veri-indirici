#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Danıştay Karar İndirici (UYAP / Bedesten API)

karararama.danistay.gov.tr (bedesten.adalet.gov.tr) üzerinden gün gün kararları
tarar; arama künyesini ve getDocContent yanıtını ham JSON olarak kaydeder.
"""

import os
import sys
import time
import json
import base64
import sqlite3
import logging
import argparse
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Tuple
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("Danistay")


class DanistayScraper2:
    SEARCH_URL = "https://bedesten.adalet.gov.tr/emsal-karar/getEmsalKarar"
    DOC_URL = "https://bedesten.adalet.gov.tr/emsal-karar/getDocContent"

    def __init__(
        self,
        base_dir: str = "danistay_arsiv",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        delay: float = 0.3,
        page_size: int = 100,
    ):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        if not os.path.isabs(base_dir):
            self.base_dir = os.path.abspath(os.path.join(script_dir, base_dir))
        else:
            self.base_dir = os.path.abspath(base_dir)

        os.makedirs(self.base_dir, exist_ok=True)
        self.delay = delay
        self.page_size = page_size

        today = datetime.now()
        if end_date:
            self.end_date = datetime.strptime(end_date, "%Y-%m-%d")
        else:
            self.end_date = today

        if start_date:
            self.start_date = datetime.strptime(start_date, "%Y-%m-%d")
        else:
            self.start_date = datetime(1970, 1, 1)

        self.db_path = os.path.join(self.base_dir, "archive_state.db")
        self.init_db()

        self.session = self._create_session()
        self.downloaded_ids = set()
        self._load_existing_ids()
        self._sync_disk_files_to_db()

    def _create_session(self) -> requests.Session:
        s = requests.Session()
        s.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Origin": "https://karararama.danistay.gov.tr",
            "Referer": "https://karararama.danistay.gov.tr/",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            )
        })
        return s

    def _renew_session(self):
        try:
            self.session.close()
        except Exception:
            pass
        self.session = self._create_session()

    def _check_security_block(self, status_code: int, text: str) -> bool:
        if status_code in (403, 429):
            return True
        low = text.lower()
        guvenlik_kelimeleri = [
            "captcha", "recaptcha", "hcaptcha", "turnstile", "cf-ray",
            "erişim sınırı", "güvenlik politikaları", "sınıra ulaştın",
            "too many requests", "rate_limit_exceeded", "forbidden", "access denied",
            "waf", "güvenlik kontrolü", "bot detection", "robot"
        ]
        return any(k in low for k in guvenlik_kelimeleri)

    def init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("""
                CREATE TABLE IF NOT EXISTS indirilen_kararlar (
                    id TEXT PRIMARY KEY,
                    karar_tarihi TEXT,
                    yil TEXT,
                    ay TEXT,
                    gun TEXT,
                    dosya_yolu TEXT,
                    eklenme_tarihi TEXT
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS tamamlanan_gunler (
                    tarih TEXT PRIMARY KEY,
                    toplam_karar INTEGER,
                    tamamlanma_zamani TEXT
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS hatali_kararlar (
                    id TEXT PRIMARY KEY,
                    karar_tarihi TEXT,
                    arama_kunyesi TEXT,
                    hata_nedeni TEXT,
                    deneme_sayisi INTEGER DEFAULT 1,
                    son_deneme TEXT
                )
            """)
            try:
                c.execute("ALTER TABLE hatali_kararlar ADD COLUMN arama_kunyesi TEXT")
            except sqlite3.OperationalError:
                pass
            conn.commit()

    def _load_existing_ids(self):
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("SELECT id FROM indirilen_kararlar")
            for row in c.fetchall():
                self.downloaded_ids.add(str(row[0]))

    def _sync_disk_files_to_db(self):
        disk_new = []
        for root, _, files in os.walk(self.base_dir):
            for f in files:
                if f.startswith("karar_") and f.endswith(".json"):
                    dec_id = f[6:-5]
                    if dec_id and dec_id not in self.downloaded_ids:
                        self.downloaded_ids.add(dec_id)
                        rel_path = os.path.relpath(os.path.join(root, f), self.base_dir)
                        parts = rel_path.split(os.sep)
                        if len(parts) >= 3 and parts[0].isdigit():
                            y, m, d = parts[0], parts[1], parts[2]
                            k_tarihi = f"{y}-{m}-{d}"
                        else:
                            y, m, d, k_tarihi = "", "", "", ""
                        disk_new.append((dec_id, k_tarihi, y, m, d, rel_path, datetime.now().isoformat()))

        if disk_new:
            with sqlite3.connect(self.db_path) as conn:
                c = conn.cursor()
                c.executemany("""
                    INSERT OR IGNORE INTO indirilen_kararlar
                    (id, karar_tarihi, yil, ay, gun, dosya_yolu, eklenme_tarihi)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, disk_new)
                conn.commit()
            logger.info(f"Diskteki mevcut dosyalardan {len(disk_new)} adet karar SQLite veritabanına eklendi.")

        logger.info(f"Toplam {len(self.downloaded_ids)} adet kayıtlı karar bellekte.")

    def is_day_completed(self, date_str: str) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("SELECT 1 FROM tamamlanan_gunler WHERE tarih = ?", (date_str,))
            return c.fetchone() is not None

    def mark_day_completed(self, date_str: str, count: int):
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute(
                "INSERT OR REPLACE INTO tamamlanan_gunler (tarih, toplam_karar, tamamlanma_zamani) VALUES (?, ?, ?)",
                (date_str, count, datetime.now().isoformat())
            )
            conn.commit()

    def record_decision_to_db(self, dec_id: str, date_str: str, y: str, m: str, d: str, file_path: str):
        self.downloaded_ids.add(str(dec_id))
        rel_path = os.path.relpath(file_path, self.base_dir)
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("""
                INSERT OR IGNORE INTO indirilen_kararlar
                (id, karar_tarihi, yil, ay, gun, dosya_yolu, eklenme_tarihi)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (str(dec_id), date_str, y, m, d, rel_path, datetime.now().isoformat()))
            conn.commit()

    def record_failed_decision(self, dec_id: str, date_str: str, reason: str, karar_obj: Optional[Dict[str, Any]] = None):
        kunye_json = json.dumps(karar_obj, ensure_ascii=False) if karar_obj else None
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("""
                INSERT INTO hatali_kararlar (id, karar_tarihi, arama_kunyesi, hata_nedeni, deneme_sayisi, son_deneme)
                VALUES (?, ?, ?, ?, 1, ?)
                ON CONFLICT(id) DO UPDATE SET
                    deneme_sayisi = deneme_sayisi + 1,
                    arama_kunyesi = COALESCE(excluded.arama_kunyesi, hatali_kararlar.arama_kunyesi),
                    hata_nedeni = excluded.hata_nedeni,
                    son_deneme = excluded.son_deneme
            """, (str(dec_id), date_str, kunye_json, reason, datetime.now().isoformat()))
            conn.commit()

    def remove_failed_decision(self, dec_id: str):
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("DELETE FROM hatali_kararlar WHERE id = ?", (str(dec_id),))
            conn.commit()

    def _gunluk_tarih_utc(self, dt: datetime):
        """Türkiye saatine (UTC+3) göre günün UTC başlangıç ve bitiş zamanlarını üretir."""
        prev_day = dt - timedelta(days=1)
        start_utc = prev_day.strftime("%Y-%m-%dT21:00:00.000Z")
        end_utc = dt.strftime("%Y-%m-%dT20:59:59.999Z")
        return start_utc, end_utc

    def fetch_search_page(self, dt: datetime, current_page: int) -> Tuple[bool, Optional[Dict[str, Any]]]:
        start_utc, end_utc = self._gunluk_tarih_utc(dt)
        payload = {
            "data": {
                "itemTypeList": ["DANISTAYKARAR"],
                "birimAdiList": [],
                "esasNoYil": None,
                "esasNoSiraStart": None,
                "esasNoSiraEnd": None,
                "kararNoYil": None,
                "kararNoSiraStart": None,
                "kararNoSiraEnd": None,
                "kararTarihiStart": start_utc,
                "kararTarihiEnd": end_utc,
                "sortFieldKod": 0,
                "sortDirection": "DESC",
                "pageSize": self.page_size,
                "pageNumber": current_page,
                "andKelimeler": [],
                "orKelimeler": [],
                "notKelimeler": []
            }
        }

        date_str = dt.strftime("%Y-%m-%d")

        for attempt in range(1, 11):
            try:
                resp = self.session.post(self.SEARCH_URL, json=payload, timeout=30)
                if self._check_security_block(resp.status_code, resp.text):
                    bekleme = min(60, 8 * attempt)
                    logger.warning(
                        f"Arama: Erişim sınırı [HTTP {resp.status_code}] ({date_str} sf {current_page}). "
                        f"Oturum yenileniyor, {bekleme}s bekleniyor... (Deneme {attempt}/10)"
                    )
                    self._renew_session()
                    time.sleep(bekleme)
                    continue

                if "json" not in resp.headers.get("Content-Type", ""):
                    bekleme = min(60, 10 * attempt)
                    logger.warning(
                        f"Arama: JSON olmayan yanıt ({date_str} sf {current_page}). "
                        f"Oturum yenileniyor, {bekleme}s bekleniyor... (Deneme {attempt}/10)"
                    )
                    self._renew_session()
                    time.sleep(bekleme)
                    continue

                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, dict) and "data" in data:
                        return True, data
                    else:
                        logger.warning(f"Arama: data alanı eksik: {data}")

                time.sleep(1 + attempt)
            except Exception as e:
                bekleme = min(40, 3 * attempt)
                logger.warning(f"Arama hatası ({date_str} sf {current_page}, deneme {attempt}/10): {e}. {bekleme}s bekleniyor...")
                time.sleep(bekleme)

        return False, None

    def fetch_decision_detail(self, doc_id: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
        payload = {"data": {"documentId": str(doc_id)}}

        for attempt in range(1, 11):
            try:
                resp = self.session.post(self.DOC_URL, json=payload, timeout=30)
                if self._check_security_block(resp.status_code, resp.text):
                    bekleme = min(60, 6 * attempt)
                    logger.warning(
                        f"Detay: Erişim sınırı [HTTP {resp.status_code}] (ID: {doc_id}). "
                        f"Oturum yenileniyor, {bekleme}s bekleniyor... (Deneme {attempt}/10)"
                    )
                    self._renew_session()
                    time.sleep(bekleme)
                    continue

                if "json" not in resp.headers.get("Content-Type", ""):
                    bekleme = min(60, 8 * attempt)
                    logger.warning(
                        f"Detay: JSON olmayan yanıt (ID: {doc_id}). "
                        f"Oturum yenileniyor, {bekleme}s bekleniyor... (Deneme {attempt}/10)"
                    )
                    self._renew_session()
                    time.sleep(bekleme)
                    continue

                if resp.status_code == 200:
                    res = resp.json()
                    if isinstance(res, dict) and "data" in res and res.get("data") is not None:
                        return True, res
                    else:
                        logger.warning(f"Detay: API verisi boş (ID: {doc_id}): {res}")

                time.sleep(1 + attempt)
            except Exception as e:
                bekleme = min(30, 2 * attempt)
                logger.warning(f"Detay hatası (ID: {doc_id}, deneme {attempt}/10): {e}. {bekleme}s bekleniyor...")
                time.sleep(bekleme)

        return False, None

    def save_decision_file(self, karar_obj: Optional[Dict[str, Any]], detay_obj: Dict[str, Any], date_str: str, doc_id_fallback: str = "") -> Optional[str]:
        doc_id = str((karar_obj or {}).get("documentId") or doc_id_fallback)
        parts = date_str.split("-") if date_str and "-" in date_str else []
        if len(parts) >= 3 and parts[0].isdigit():
            y, m, d = parts[0], parts[1], parts[2]
        else:
            y, m, d = "kurtarilan", "tarihsiz", "00"

        folder = os.path.join(self.base_dir, y, m, d)
        os.makedirs(folder, exist_ok=True)
        file_path = os.path.join(folder, f"karar_{doc_id}.json")

        if isinstance(detay_obj, dict):
            data_sec = detay_obj.get("data")
            if isinstance(data_sec, dict) and "content" in data_sec:
                b64_str = data_sec.get("content") or ""
                decoded_html = ""
                if b64_str:
                    try:
                        decoded_html = base64.b64decode(b64_str).decode("utf-8", errors="ignore")
                    except Exception:
                        decoded_html = ""
                data_sec["content_html"] = decoded_html

        record = {
            "arama_kunyesi": karar_obj,
            "detay": detay_obj
        }

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)

        self.record_decision_to_db(doc_id, date_str, y, m, d, file_path)
        self.remove_failed_decision(doc_id)
        return file_path

    def retry_failed_decisions(self):
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("SELECT id, karar_tarihi, arama_kunyesi FROM hatali_kararlar ORDER BY deneme_sayisi ASC")
            failed = c.fetchall()

        if not failed:
            return

        logger.info(f"Daha önce hata alan {len(failed)} karar tekrar deneniyor...")
        for dec_id, k_tarihi, kunye_str in failed:
            if str(dec_id) in self.downloaded_ids:
                self.remove_failed_decision(dec_id)
                continue

            karar_obj = None
            if kunye_str:
                try:
                    karar_obj = json.loads(kunye_str)
                except Exception:
                    karar_obj = None

            success, detail = self.fetch_decision_detail(dec_id)
            if success and detail:
                self.save_decision_file(karar_obj, detail, k_tarihi or "", doc_id_fallback=str(dec_id))
                logger.info(f"Hatalı karar kurtarıldı: {dec_id}")
            time.sleep(self.delay)

    def scrape(self, max_karar: Optional[int] = None):
        logger.info("=" * 70)
        logger.info("Danıştay Karar İndirici Başlatıldı")
        logger.info(f"Tarih Aralığı: {self.end_date.strftime('%Y-%m-%d')} -> {self.start_date.strftime('%Y-%m-%d')}")
        logger.info(f"Hedef Arşiv: {self.base_dir}")
        logger.info("=" * 70)

        self.retry_failed_decisions()

        current_dt = self.end_date
        total_days = (self.end_date - self.start_date).days + 1
        day_index = 0
        total_downloaded = 0

        while current_dt >= self.start_date:
            day_index += 1
            date_str = current_dt.strftime("%Y-%m-%d")

            if self.is_day_completed(date_str):
                logger.info(f"[{day_index}/{total_days}] {date_str} daha önce tamamlanmış, atlanıyor.")
                current_dt -= timedelta(days=1)
                continue

            logger.info(f"\n--- [{day_index}/{total_days}] Gün İşleniyor: {date_str} ---")

            day_success = True
            page = 1
            day_total_saved = 0
            day_records_count = 0

            while True:
                success, search_resp = self.fetch_search_page(current_dt, page)
                if not success:
                    logger.error(f"[DURDURMA] {date_str} sayfası {page} çekilemedi. İşlem durduruldu.")
                    return

                veri = search_resp.get("data", {})
                karar_listesi = veri.get("emsalKararList", [])
                total_in_day = veri.get("total", 0)

                if total_in_day == 0 or not karar_listesi:
                    if page == 1:
                        logger.info(f"{date_str}: Bu tarihe ait karar bulunamadı (0 karar).")
                    break

                day_records_count = total_in_day
                logger.info(f"{date_str} - Sayfa {page}: {len(karar_listesi)} karar bulundu (Günün Toplamı: {total_in_day})")

                for idx, karar in enumerate(karar_listesi, start=1):
                    doc_id = str(karar.get("documentId"))
                    if not doc_id:
                        continue

                    if doc_id in self.downloaded_ids:
                        continue

                    detay_ok, detay_resp = self.fetch_decision_detail(doc_id)
                    if not detay_ok or not detay_resp:
                        logger.error(f"Karar detayı alınamadı, hata tablosuna kaydediliyor: {doc_id}")
                        self.record_failed_decision(doc_id, date_str, "Detay alinamadi", karar_obj=karar)
                        day_success = False
                        continue

                    saved_path = self.save_decision_file(karar, detay_resp, date_str)
                    if saved_path:
                        total_downloaded += 1
                        day_total_saved += 1
                        esas_no = karar.get("esasNo", "")
                        karar_no = karar.get("kararNo", "")
                        logger.info(f"  [{day_total_saved}/{total_in_day}] Karar kaydedildi: ID {doc_id} | {esas_no} | {karar_no}")

                    if max_karar and total_downloaded >= max_karar:
                        logger.info(f"Belirtilen indirme limitine ({max_karar}) ulaşıldı.")
                        if day_success and day_total_saved >= total_in_day:
                            self.mark_day_completed(date_str, total_in_day)
                        return

                    time.sleep(self.delay)

                if len(karar_listesi) < self.page_size:
                    break

                page += 1
                time.sleep(0.5)

            if day_success:
                self.mark_day_completed(date_str, day_records_count)
                logger.info(f"{date_str} tamamlandı. ({day_total_saved} yeni karar kaydedildi)")
            else:
                logger.warning(f"{date_str} bazı hatalı kararlar nedeniyle tam tamamlanamadı. Sonraki çalışmada tekrar denenecek.")

            current_dt -= timedelta(days=1)
            time.sleep(0.2)

        self.retry_failed_decisions()
        logger.info("\n" + "=" * 70)
        logger.info(f"Arşivleme tamamlandı. İndirilen toplam yeni karar: {total_downloaded}")
        logger.info("=" * 70)


def main():
    parser = argparse.ArgumentParser(description="Danıştay Karar İndirici")
    parser.add_argument("--dir", "--output", dest="dir", type=str, default="danistay_arsiv", help="Kayıt klasörü (varsayılan: danistay_arsiv)")
    parser.add_argument("--start", type=str, default=None, help="En eski tarih (YYYY-MM-DD, varsayılan: 1970-01-01)")
    parser.add_argument("--end", type=str, default=None, help="Başlangıç/en yeni tarih (YYYY-MM-DD, varsayılan: Bugün)")
    parser.add_argument("--limit", type=int, default=None, help="Maksimum indirilecek karar sayısı")
    parser.add_argument("--delay", type=float, default=0.25, help="İstekler arası bekleme süresi (sn, varsayılan: 0.25)")
    parser.add_argument("--page-size", type=int, default=100, help="Sayfa başına kayıt sayısı (varsayılan: 100)")

    args = parser.parse_args()

    scraper = DanistayScraper2(
        base_dir=args.dir,
        start_date=args.start,
        end_date=args.end,
        delay=args.delay,
        page_size=args.page_size
    )

    try:
        scraper.scrape(max_karar=args.limit)
    except KeyboardInterrupt:
        logger.info("\nKullanıcı tarafından durduruldu.")


if __name__ == "__main__":
    main()
