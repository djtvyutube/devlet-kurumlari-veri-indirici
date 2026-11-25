#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Yargıtay Karar Arama 2.0 Otomasyonu ve Arşivleyici

Yargıtay'ın yenilenen REST API altyapısını (karararama-services.yargitay.gov.tr) kullanarak:
- Günümüzden geçmişe doğru gün gün kararları tarar.
- Hiçbir kararı atlamadan pagination (sayfalama) ile tüm kayıtları alır.
- Kararın detay içeriğini (/api/kararlar/karar/{id}) apiden geldiği gibi eksiksiz çeker.
- Her kararı yıl/ay/gün hiyerarşik klasör yapısında (Örn: yargitay_arsiv/2026/11/05/karar_{id}.json)
  ve API'den gelen orijinal JSON yapısını bozmadan, uydurma alan/kategori eklemeden saklar.
- SQLite ile durum takibi (checkpointing) yapar, durdurulup tekrar başlatıldığında kaldığı yerden devam eder.
- Kararlar indirildiği an anında SQLite'a işlenir.
- İndirilemeyen/hata alan kararları 'hatali_kararlar' tablosuna kaydeder ve periyodik olarak tekrar dener.
- Başlangıçta diskteki mevcut kararları otomatik tarayıp SQLite'a senkronize eder.
- Captcha, Cloudflare/WAF, 429 Rate Limit ve erişim sınırı blokajlarına karşı dinamik oturum yenileme ve bekleme koruması içerir.
"""

import os
import sys
import time
import json
import sqlite3
import logging
import argparse
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Tuple, List
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import urllib3

# SSL uyarılarını bastır
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Loglama ayarları
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("Yargitay2.0")


class YargitayScraper2:
    BASE_URL = "https://karararama-services.yargitay.gov.tr"
    SEARCH_ENDPOINT = f"{BASE_URL}/api/kararlar"
    DETAIL_ENDPOINT = f"{BASE_URL}/api/kararlar/karar"

    def __init__(
        self,
        base_dir: str = "yargitay_arsiv",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        delay: float = 0.25,
        page_size: int = 100,
    ):
        # Script dizinine göre mutlak yol çözümü
        script_dir = os.path.dirname(os.path.abspath(__file__))
        if not os.path.isabs(base_dir):
            self.base_dir = os.path.abspath(os.path.join(script_dir, base_dir))
        else:
            self.base_dir = os.path.abspath(base_dir)

        os.makedirs(self.base_dir, exist_ok=True)

        self.delay = delay
        self.page_size = page_size

        # Varsayılan bitiş: Bugün
        today = datetime.now()
        if end_date:
            self.end_date = datetime.strptime(end_date, "%Y-%m-%d")
        else:
            self.end_date = today

        # Varsayılan başlangıç: En eski arşivlere kadar (1970)
        if start_date:
            self.start_date = datetime.strptime(start_date, "%Y-%m-%d")
        else:
            self.start_date = datetime(1970, 1, 1)

        self.db_path = os.path.join(self.base_dir, "archive_state.db")
        self.init_db()

        # Oturum yapılandırması
        self.session = self._create_session()

        # Bellek içi indirilenler önbelleği
        self.downloaded_ids = set()
        self._load_existing_ids()
        self._sync_disk_files_to_db()

    def _create_session(self) -> requests.Session:
        s = requests.Session()
        s.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": "https://karararama.yargitay.gov.tr",
            "Referer": "https://karararama.yargitay.gov.tr/",
        })
        return s

    def _renew_session(self):
        """Rate limit, çerez blokajı veya güvenlik duvarında oturumu sıfırlar."""
        try:
            self.session.close()
        except Exception:
            pass
        self.session = self._create_session()

    def _check_security_block(self, status_code: int, text: str) -> bool:
        """
        Gelen yanıtın Captcha, WAF blokajı, IP engeli veya erişim sınırı olup olmadığını tespit eder.
        """
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
        """SQLite veritabanını ilklendirir."""
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
                    hata_nedeni TEXT,
                    deneme_sayisi INTEGER DEFAULT 1,
                    son_deneme TEXT
                )
            """)
            conn.commit()

    def _load_existing_ids(self):
        """Daha önce indirilmiş karar kimliklerini belleğe alır."""
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("SELECT id FROM indirilen_kararlar")
            for row in c.fetchall():
                self.downloaded_ids.add(row[0])

    def _sync_disk_files_to_db(self):
        """Diskte var olup SQLite'a yazılmamış dosyaları tespit edip veritabanına ekler."""
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
            logger.info(f"Diskteki mevcut dosyalardan {len(disk_new)} adet karar SQLite veritabanına indekslendi.")

        logger.info(f"Toplam {len(self.downloaded_ids)} adet benzersiz karar belleğe hazır.")

    def is_day_completed(self, date_str: str) -> bool:
        """Günün tamamen taranıp tamamlanmadığını kontrol eder."""
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("SELECT 1 FROM tamamlanan_gunler WHERE tarih = ?", (date_str,))
            return c.fetchone() is not None

    def mark_day_completed(self, date_str: str, count: int):
        """Günü tamamlandı olarak kaydeder."""
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute(
                "INSERT OR REPLACE INTO tamamlanan_gunler (tarih, toplam_karar, tamamlanma_zamani) VALUES (?, ?, ?)",
                (date_str, count, datetime.now().isoformat())
            )
            conn.commit()

    def record_failed_decision(self, dec_id: str, date_str: str, reason: str):
        """İndirilemeyen kararı hata tablosuna kaydeder/günceller."""
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("""
                INSERT INTO hatali_kararlar (id, karar_tarihi, hata_nedeni, deneme_sayisi, son_deneme)
                VALUES (?, ?, ?, 1, ?)
                ON CONFLICT(id) DO UPDATE SET
                    deneme_sayisi = deneme_sayisi + 1,
                    hata_nedeni = excluded.hata_nedeni,
                    son_deneme = excluded.son_deneme
            """, (dec_id, date_str, reason, datetime.now().isoformat()))
            conn.commit()

    def remove_failed_decision(self, dec_id: str):
        """Başarıyla indirilen kararı hata tablosundan siler."""
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("DELETE FROM hatali_kararlar WHERE id = ?", (dec_id,))
            conn.commit()

    def fetch_search_page(self, date_str: str, page: int) -> Optional[Dict[str, Any]]:
        """Belirtilen gün ve sayfa için arama sonuçlarını çeker."""
        params = {
            "page": page,
            "size": self.page_size,
            "sortOrder": "tarih_desc",
            "kararTarihiMin": date_str,
            "kararTarihiMax": date_str
        }

        for attempt in range(1, 10):
            try:
                resp = self.session.get(self.SEARCH_ENDPOINT, params=params, timeout=25, verify=False)
                if resp.status_code == 200:
                    try:
                        return resp.json()
                    except json.JSONDecodeError:
                        logger.warning(f"Arama yanıtı JSON değil (HTML döndü). Yeniden deneniyor...")
                        self._renew_session()
                        time.sleep(15)
                        continue

                if self._check_security_block(resp.status_code, resp.text):
                    wait_sec = min(10 * attempt, 60)
                    logger.warning(f"Arama: Güvenlik/Erişim sınırı algılandı [HTTP {resp.status_code}]. Oturum sıfırlanıyor, {wait_sec}s bekleniyor... (Deneme {attempt}/10)")
                    self._renew_session()
                    time.sleep(wait_sec)
                    continue
                else:
                    logger.warning(f"Arama isteği başarısız [HTTP {resp.status_code}]: {resp.text[:150]}")
                    time.sleep(3 * attempt)
            except Exception as e:
                logger.error(f"Arama isteği hatası ({date_str}, sayfa {page}): {e}")
                self._renew_session()
                time.sleep(3 * attempt)

        return None

    def fetch_decision_detail(self, decision_id: str) -> Optional[Dict[str, Any]]:
        """Tek bir kararın tüm detayını çeker."""
        url = f"{self.DETAIL_ENDPOINT}/{decision_id}"
        for attempt in range(1, 10):
            try:
                resp = self.session.get(url, timeout=25, verify=False)
                if resp.status_code == 200:
                    try:
                        return resp.json()
                    except json.JSONDecodeError:
                        logger.warning(f"Detay yanıtı JSON değil. Yeniden deneniyor...")
                        self._renew_session()
                        time.sleep(15)
                        continue

                if self._check_security_block(resp.status_code, resp.text):
                    wait_sec = min(8 * attempt, 60)
                    logger.warning(f"Detay: Güvenlik/Erişim engeli algılandı ({decision_id}) [HTTP {resp.status_code}]. Oturum sıfırlanıyor, {wait_sec}s bekleniyor... (Deneme {attempt}/10)")
                    self._renew_session()
                    time.sleep(wait_sec)
                    continue
                elif resp.status_code == 404:
                    logger.warning(f"Karar detayı bulunamadı [404]: {decision_id}")
                    return None
                else:
                    logger.warning(f"Detay isteği başarısız [HTTP {resp.status_code}]: {decision_id}")
                    time.sleep(2 * attempt)
            except Exception as e:
                logger.error(f"Detay çekme hatası ({decision_id}): {e}")
                self._renew_session()
                time.sleep(3 * attempt)

        return None

    def save_decision(self, raw_data: Dict[str, Any], date_str: str) -> Optional[str]:
        """
        API'den gelen veriyi hiçbir format/kategori uydurmadan geldiği gibi
        yıl/ay/gün yapısındaki klasöre kaydeder.
        """
        decision_id = raw_data.get("id")
        if not decision_id:
            return None

        karar_tarihi = raw_data.get("kararTarihi") or date_str
        try:
            dt = datetime.strptime(karar_tarihi[:10], "%Y-%m-%d")
            year_str = f"{dt.year:04d}"
            month_str = f"{dt.month:02d}"
            day_str = f"{dt.day:02d}"
        except Exception:
            year_str = date_str[:4]
            month_str = date_str[5:7]
            day_str = date_str[8:10]

        target_dir = os.path.join(self.base_dir, year_str, month_str, day_str)
        os.makedirs(target_dir, exist_ok=True)

        file_name = f"karar_{decision_id}.json"
        file_path = os.path.join(target_dir, file_name)

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(raw_data, f, ensure_ascii=False, indent=2)

        rel_path = os.path.relpath(file_path, self.base_dir)
        return rel_path

    def _record_decision_to_db(self, dec_id: str, karar_tarihi: str, y: str, m: str, d: str, saved_path: str):
        """Tek bir kararı anında SQLite veritabanına işler."""
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("""
                INSERT OR IGNORE INTO indirilen_kararlar
                (id, karar_tarihi, yil, ay, gun, dosya_yolu, eklenme_tarihi)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (dec_id, karar_tarihi, y, m, d, saved_path, datetime.now().isoformat()))
            conn.commit()

    def retry_failed_decisions(self):
        """Önceki turlarda hata almış kararları tekrar indirmeyi dener."""
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("SELECT id, karar_tarihi FROM hatali_kararlar")
            failed_rows = c.fetchall()

        if not failed_rows:
            return

        logger.info(f"Hata tablosundaki {len(failed_rows)} adet başarısız karar yeniden deneniyor...")
        for dec_id, date_str in failed_rows:
            if dec_id in self.downloaded_ids:
                self.remove_failed_decision(dec_id)
                continue

            detail = self.fetch_decision_detail(dec_id)
            if detail:
                saved_path = self.save_decision(detail, date_str)
                if saved_path:
                    self.downloaded_ids.add(dec_id)
                    karar_tarihi = detail.get("kararTarihi") or date_str
                    parts = karar_tarihi[:10].split("-") if "-" in karar_tarihi[:10] else date_str.split("-")
                    y, m, d = (parts[0], parts[1], parts[2]) if len(parts) >= 3 else ("", "", "")
                    self._record_decision_to_db(dec_id, karar_tarihi, y, m, d, saved_path)
                    self.remove_failed_decision(dec_id)
                    logger.info(f"  [KURTARILDI] Hatalı karar başarıyla indirildi: {dec_id}")
            time.sleep(self.delay)

    def process_day(self, date_str: str) -> int:
        """Belirtilen bir güne ait tüm kararları sayfa sayfa eksiksiz çeker."""
        first_page = self.fetch_search_page(date_str, page=0)
        if not first_page:
            logger.error(f"Günün ilk sayfası alınamadı: {date_str}")
            return 0

        total_elements = first_page.get("totalElements", 0)
        total_pages = first_page.get("totalPages", 0)

        if total_elements == 0:
            logger.info(f"{date_str}: Karar bulunamadı.")
            self.mark_day_completed(date_str, 0)
            return 0

        logger.info(f"{date_str}: Toplam {total_elements} karar ({total_pages} sayfa) bulundu. İndiriliyor...")

        saved_count = 0
        current_page = 0
        overall_idx = 0
        has_any_failure = False

        while current_page < total_pages:
            page_data = first_page if current_page == 0 else self.fetch_search_page(date_str, current_page)
            if not page_data or "content" not in page_data:
                logger.warning(f"Sayfa {current_page} verisi alınamadı ({date_str}), yeniden deneniyor...")
                time.sleep(3)
                page_data = self.fetch_search_page(date_str, current_page)
                if not page_data or "content" not in page_data:
                    logger.error(f"Sayfa {current_page} atlanmak zorunda kalındı!")
                    has_any_failure = True
                    current_page += 1
                    continue

            items = page_data["content"]

            for item in items:
                overall_idx += 1
                dec_id = item.get("id")
                if not dec_id:
                    continue

                if dec_id in self.downloaded_ids:
                    continue

                # Karar detayını çek (Tam API yanıtı)
                detail = self.fetch_decision_detail(dec_id)
                if detail is None:
                    # Detay çekilemediyse yarım/özet veriyi kaydetme; hatali_kararlar tablosuna yaz ki sonraki turda tam detayı indirilsin
                    logger.warning(f"Karar detayı çekilemedi, hata tablosuna yazılıyor: {dec_id}")
                    self.record_failed_decision(dec_id, date_str, "Detay çekilemedi (HTTP Hata / Timeout)")
                    has_any_failure = True
                    continue

                self.remove_failed_decision(dec_id)
                saved_path = self.save_decision(detail, date_str)
                if saved_path:
                    self.downloaded_ids.add(dec_id)
                    karar_tarihi = detail.get("kararTarihi") or date_str
                    y, m, d = date_str.split("-")
                    self._record_decision_to_db(dec_id, karar_tarihi, y, m, d, saved_path)
                    saved_count += 1
                    logger.info(f"  [{overall_idx}/{total_elements}] Karar kaydedildi: {dec_id}")

                if self.delay > 0:
                    time.sleep(self.delay)

            current_page += 1

        # Eğer o günde hiçbir hata/eksik kalmadıysa günü tamamlandı olarak işaretle
        if not has_any_failure:
            self.mark_day_completed(date_str, saved_count)
            logger.info(f"{date_str} eksiksiz tamamlandı. ({saved_count} yeni karar kaydedildi | Toplam önbellek: {len(self.downloaded_ids)})")
        else:
            logger.warning(f"{date_str} bazı hatalarla bitti. Gün 'tamamlandı' işaretlenmedi (tekrar çalıştırıldığında eksikler tamamlanacak).")

        return saved_count

    def run(self):
        """Günümüzden geçmişe doğru tüm günleri tarar."""
        logger.info("=" * 60)
        logger.info("Yargıtay Karar Arama 2.0 İndirici Başlatılıyor")
        logger.info(f"Tarih Aralığı: {self.end_date.strftime('%Y-%m-%d')} -> {self.start_date.strftime('%Y-%m-%d')} (Günümüzden Geçmişe)")
        logger.info(f"Kayıt Dizini: {self.base_dir}")
        logger.info("=" * 60)

        # Başlangıçta varsa önceki hatalıları dene
        self.retry_failed_decisions()

        current_dt = self.end_date
        total_days = (self.end_date - self.start_date).days + 1
        day_idx = 0

        while current_dt >= self.start_date:
            day_idx += 1
            date_str = current_dt.strftime("%Y-%m-%d")

            if self.is_day_completed(date_str):
                logger.info(f"[{day_idx}/{total_days}] {date_str} daha önce tamamlanmış, atlanıyor.")
                current_dt -= timedelta(days=1)
                continue

            logger.info(f"\n--- [{day_idx}/{total_days}] Gün İşleniyor: {date_str} ---")
            try:
                self.process_day(date_str)
            except KeyboardInterrupt:
                logger.info("\nKullanıcı tarafından durduruldu. Veriler güvenle kaydedildi.")
                sys.exit(0)
            except Exception as e:
                logger.error(f"{date_str} işlenirken hata: {e}", exc_info=True)
                time.sleep(3)

            current_dt -= timedelta(days=1)

        # Tarama bittiğinde varsa kalan hatalıları son kez dene
        self.retry_failed_decisions()

        logger.info("=" * 60)
        logger.info("Tüm hedeflenen aralık başarıyla tarandı.")
        logger.info(f"Toplam kaydedilen benzersiz karar: {len(self.downloaded_ids)}")
        logger.info("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Yargıtay 2.0 Karar Arama ve Arşivleme")
    parser.add_argument("--output", default="yargitay_arsiv", help="Hedef çıktı klasörü (varsayılan: yargitay_arsiv)")
    parser.add_argument("--start", default=None, help="Başlangıç tarihi (YYYY-MM-DD, varsayılan: 1970-01-01)")
    parser.add_argument("--end", default=None, help="Bitiş tarihi (YYYY-MM-DD, varsayılan: Bugün)")
    parser.add_argument("--delay", type=float, default=0.25, help="İstekler arası gecikme (saniye)")
    parser.add_argument("--page-size", type=int, default=100, help="Sayfa başına kayıt (En fazla 100)")

    args = parser.parse_args()

    scraper = YargitayScraper2(
        base_dir=args.output,
        start_date=args.start,
        end_date=args.end,
        delay=args.delay,
        page_size=args.page_size
    )
    scraper.run()
