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
import shutil
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
        self.hata_json_path = os.path.join(self.base_dir, "hata.json")
        self.idlist_path = os.path.join(self.base_dir, "idlist.json")
        self._idlist_initialized = os.path.exists(self.idlist_path) and os.path.getsize(self.idlist_path) > 4
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
                    http_kodu INTEGER,
                    yanit_govdesi TEXT,
                    kalici_hata INTEGER DEFAULT 0,
                    deneme_sayisi INTEGER DEFAULT 1,
                    son_deneme TEXT
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS hatali_sayfalar (
                    tarih TEXT,
                    sayfa INTEGER,
                    hata_nedeni TEXT,
                    http_kodu INTEGER,
                    yanit_govdesi TEXT,
                    deneme_sayisi INTEGER DEFAULT 1,
                    son_deneme TEXT,
                    PRIMARY KEY (tarih, sayfa)
                )
            """)
            for col_def in [
                "ALTER TABLE hatali_kararlar ADD COLUMN arama_kunyesi TEXT",
                "ALTER TABLE hatali_kararlar ADD COLUMN http_kodu INTEGER",
                "ALTER TABLE hatali_kararlar ADD COLUMN yanit_govdesi TEXT",
                "ALTER TABLE hatali_kararlar ADD COLUMN kalici_hata INTEGER DEFAULT 0"
            ]:
                try:
                    c.execute(col_def)
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
                    full_path = os.path.join(root, f)
                    try:
                        if os.path.getsize(full_path) < 2:
                            continue
                    except OSError:
                        continue
                    dec_id = f[6:-5]
                    if dec_id and dec_id not in self.downloaded_ids:
                        self.downloaded_ids.add(dec_id)
                        rel_path = os.path.relpath(full_path, self.base_dir).replace("\\", "/")
                        parts = rel_path.split("/")
                        if len(parts) >= 3 and parts[0].isdigit():
                            y, m, d = parts[0], parts[1], parts[2]
                            k_tarihi = f"{y}-{m}-{d}"
                        else:
                            y, m, d, k_tarihi = "tarihsiz", "", "", ""
                        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        disk_new.append((dec_id, k_tarihi, y, m, d, rel_path, now_str))

        if disk_new:
            with sqlite3.connect(self.db_path) as conn:
                c = conn.cursor()
                c.executemany("""
                    INSERT OR IGNORE INTO indirilen_kararlar
                    (id, karar_tarihi, yil, ay, gun, dosya_yolu, eklenme_tarihi)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, disk_new)
                conn.commit()
            for dec_id, _, _, _, _, rel_path, now_str in disk_new:
                self._append_to_idlist(dec_id, rel_path, now_str)
            logger.info(f"Diskteki mevcut dosyalardan {len(disk_new)} adet karar SQLite veritabanına ve idlist.json'a eklendi.")

        if not self._idlist_initialized and self.downloaded_ids:
            self._rebuild_idlist_from_db()

        logger.info(f"Toplam {len(self.downloaded_ids)} adet kayıtlı karar bellekte.")

    def _rebuild_idlist_from_db(self):
        try:
            with sqlite3.connect(self.db_path) as conn:
                c = conn.cursor()
                c.execute("SELECT id, dosya_yolu, eklenme_tarihi FROM indirilen_kararlar")
                rows = c.fetchall()
            if not rows:
                return
            with open(self.idlist_path, "w", encoding="utf-8") as f:
                f.write("{\n")
                for idx, (dec_id, rel_path, ekl_tarih) in enumerate(rows):
                    dt_short = (ekl_tarih or "")[:19].replace("T", " ")
                    path_clean = (rel_path or "").replace("\\", "/")
                    entry = json.dumps({str(dec_id): [dt_short, path_clean]}, ensure_ascii=False, separators=(",", ":"))[1:-1]
                    comma = ",\n" if idx < len(rows) - 1 else "\n"
                    f.write(entry + comma)
                f.write("}\n")
            self._idlist_initialized = True
        except Exception as e:
            logger.error(f"idlist.json yeniden oluşturulurken hata: {e}")

    def _append_to_idlist(self, dec_id: str, rel_path: str, timestamp_str: Optional[str] = None):
        ts = (timestamp_str or datetime.now().strftime("%Y-%m-%d %H:%M:%S"))[:19].replace("T", " ")
        clean_path = rel_path.replace("\\", "/")
        entry_fragment = json.dumps({str(dec_id): [ts, clean_path]}, ensure_ascii=False, separators=(",", ":"))[1:-1]

        try:
            if not os.path.exists(self.idlist_path) or os.path.getsize(self.idlist_path) < 2:
                with open(self.idlist_path, "w", encoding="utf-8") as f:
                    f.write("{\n" + entry_fragment + "\n}\n")
                self._idlist_initialized = True
                return

            with open(self.idlist_path, "rb+") as f:
                f.seek(0, os.SEEK_END)
                pos = f.tell()
                read_len = min(pos, 64)
                f.seek(pos - read_len)
                tail = f.read(read_len)
                last_brace = tail.rfind(b"}")
                if last_brace != -1:
                    cut_pos = (pos - read_len) + last_brace
                    f.seek(0)
                    head = f.read(min(cut_pos, 16)).strip()
                    f.seek(cut_pos)
                    f.truncate()
                    if head == b"{":
                        f.write(("\n" + entry_fragment + "\n}\n").encode("utf-8"))
                    else:
                        f.seek(max(0, cut_pos - 1))
                        prev_char = f.read(1)
                        if prev_char == b"\n":
                            f.seek(cut_pos - 1)
                            f.truncate()
                        f.write((",\n" + entry_fragment + "\n}\n").encode("utf-8"))
                else:
                    f.write((",\n" + entry_fragment + "\n}\n").encode("utf-8"))
        except Exception as e:
            logger.error(f"idlist.json dosyasına yazılırken hata: {e}")

    def _load_hata_json(self) -> Dict[str, Any]:
        default_data: Dict[str, Any] = {
            "hatali_kararlar": {},
            "hatali_sayfalar": {},
            "eksik_gunler": {}
        }
        if os.path.exists(self.hata_json_path):
            try:
                with open(self.hata_json_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    if isinstance(loaded, dict):
                        default_data["hatali_kararlar"] = loaded.get("hatali_kararlar", {})
                        default_data["hatali_sayfalar"] = loaded.get("hatali_sayfalar", {})
                        default_data["eksik_gunler"] = loaded.get("eksik_gunler", {})
            except json.JSONDecodeError as je:
                corrupt_backup = f"{self.hata_json_path}.corrupt.{int(time.time())}"
                logger.error(f"[KRİTİK] hata.json bozuk ({je})! Yedekleniyor: {corrupt_backup}")
                try:
                    shutil.copy2(self.hata_json_path, corrupt_backup)
                except Exception as ce:
                    logger.error(f"Bozuk hata.json yedeklenemedi: {ce}")
            except Exception as e:
                logger.error(f"hata.json okunurken hata oluştu: {e}")
        return default_data

    def _save_hata_json(self, data: Dict[str, Any]):
        try:
            tmp_path = f"{self.hata_json_path}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self.hata_json_path)
        except Exception as e:
            logger.error(f"hata.json kaydedilirken hata oluştu: {e}")

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

        if os.path.exists(self.hata_json_path):
            hata_data = self._load_hata_json()
            if date_str in hata_data.get("eksik_gunler", {}):
                del hata_data["eksik_gunler"][date_str]
                self._save_hata_json(hata_data)

    def record_incomplete_day(self, date_str: str, reason: str, expected_total: int = 0, processed_count: int = 0):
        hata_data = self._load_hata_json()
        existing = hata_data["eksik_gunler"].get(date_str, {})
        hata_data["eksik_gunler"][date_str] = {
            "tarih": date_str,
            "neden": reason,
            "beklenen_toplam": expected_total,
            "islenen_karar": processed_count,
            "deneme_sayisi": existing.get("deneme_sayisi", 0) + 1,
            "son_deneme": datetime.now().isoformat()
        }
        self._save_hata_json(hata_data)

    def record_decision_to_db(self, dec_id: str, date_str: str, y: str, m: str, d: str, file_path: str):
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.downloaded_ids.add(str(dec_id))
        rel_path = os.path.relpath(file_path, self.base_dir).replace("\\", "/")
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("""
                INSERT OR REPLACE INTO indirilen_kararlar
                (id, karar_tarihi, yil, ay, gun, dosya_yolu, eklenme_tarihi)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (str(dec_id), date_str, y, m, d, rel_path, now_str))
            conn.commit()
        self._append_to_idlist(str(dec_id), rel_path, now_str)

    def record_failed_decision(
        self,
        dec_id: str,
        date_str: str,
        error_info: Dict[str, Any],
        karar_obj: Optional[Dict[str, Any]] = None
    ):
        now_iso = datetime.now().isoformat()
        kunye_json = json.dumps(karar_obj, ensure_ascii=False) if karar_obj else None
        reason = error_info.get("error_msg") or "Detay alinamadi"
        status_code = error_info.get("status_code")
        response_text = error_info.get("response_text")
        is_permanent = 1 if (status_code == 404 or error_info.get("kalici_hata")) else 0

        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("""
                INSERT INTO hatali_kararlar (
                    id, karar_tarihi, arama_kunyesi, hata_nedeni, http_kodu, yanit_govdesi, kalici_hata, deneme_sayisi, son_deneme
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(id) DO UPDATE SET
                    deneme_sayisi = deneme_sayisi + 1,
                    karar_tarihi = CASE WHEN excluded.karar_tarihi != '' THEN excluded.karar_tarihi ELSE hatali_kararlar.karar_tarihi END,
                    arama_kunyesi = COALESCE(excluded.arama_kunyesi, hatali_kararlar.arama_kunyesi),
                    hata_nedeni = excluded.hata_nedeni,
                    http_kodu = excluded.http_kodu,
                    yanit_govdesi = excluded.yanit_govdesi,
                    kalici_hata = excluded.kalici_hata,
                    son_deneme = excluded.son_deneme
            """, (str(dec_id), date_str, kunye_json, reason, status_code, response_text, is_permanent, now_iso))
            conn.commit()

        hata_data = self._load_hata_json()
        existing = hata_data["hatali_kararlar"].get(str(dec_id), {})
        deneme_sayisi = existing.get("deneme_sayisi", 0) + 1
        hata_data["hatali_kararlar"][str(dec_id)] = {
            "id": str(dec_id),
            "karar_tarihi": date_str or existing.get("karar_tarihi", ""),
            "arama_kunyesi": karar_obj if karar_obj is not None else existing.get("arama_kunyesi"),
            "hata_nedeni": reason,
            "http_kodu": status_code,
            "kalici_hata": bool(is_permanent),
            "exception": error_info.get("exception"),
            "yanit_govdesi": response_text,
            "deneme_sayisi": deneme_sayisi,
            "son_deneme": now_iso
        }
        self._save_hata_json(hata_data)

    def remove_failed_decision(self, dec_id: str):
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("DELETE FROM hatali_kararlar WHERE id = ?", (str(dec_id),))
            conn.commit()

        if os.path.exists(self.hata_json_path):
            hata_data = self._load_hata_json()
            if str(dec_id) in hata_data["hatali_kararlar"]:
                del hata_data["hatali_kararlar"][str(dec_id)]
                self._save_hata_json(hata_data)

    def record_failed_search_page(self, date_str: str, page: int, error_info: Dict[str, Any]):
        now_iso = datetime.now().isoformat()
        reason = error_info.get("error_msg") or "Arama sayfasi alinamadi"
        status_code = error_info.get("status_code")
        response_text = error_info.get("response_text")

        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("""
                INSERT INTO hatali_sayfalar (
                    tarih, sayfa, hata_nedeni, http_kodu, yanit_govdesi, deneme_sayisi, son_deneme
                )
                VALUES (?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(tarih, sayfa) DO UPDATE SET
                    deneme_sayisi = deneme_sayisi + 1,
                    hata_nedeni = excluded.hata_nedeni,
                    http_kodu = excluded.http_kodu,
                    yanit_govdesi = excluded.yanit_govdesi,
                    son_deneme = excluded.son_deneme
            """, (date_str, page, reason, status_code, response_text, now_iso))
            conn.commit()

        hata_data = self._load_hata_json()
        key = f"{date_str}_sayfa_{page}"
        existing = hata_data["hatali_sayfalar"].get(key, {})
        deneme_sayisi = existing.get("deneme_sayisi", 0) + 1
        hata_data["hatali_sayfalar"][key] = {
            "tarih": date_str,
            "sayfa": page,
            "hata_nedeni": reason,
            "http_kodu": status_code,
            "exception": error_info.get("exception"),
            "yanit_govdesi": response_text,
            "deneme_sayisi": deneme_sayisi,
            "son_deneme": now_iso
        }
        self._save_hata_json(hata_data)

    def remove_failed_search_page(self, date_str: str, page: int):
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("DELETE FROM hatali_sayfalar WHERE tarih = ? AND sayfa = ?", (date_str, page))
            conn.commit()

        if os.path.exists(self.hata_json_path):
            hata_data = self._load_hata_json()
            key = f"{date_str}_sayfa_{page}"
            if key in hata_data["hatali_sayfalar"]:
                del hata_data["hatali_sayfalar"][key]
                self._save_hata_json(hata_data)

    def _gunluk_tarih_utc(self, dt: datetime):
        prev_day = dt - timedelta(days=1)
        start_utc = prev_day.strftime("%Y-%m-%dT21:00:00.000Z")
        end_utc = dt.strftime("%Y-%m-%dT20:59:59.999Z")
        return start_utc, end_utc

    def fetch_search_page(
        self,
        dt: datetime,
        current_page: int,
        max_attempts: int = 5
    ) -> Tuple[bool, Optional[Dict[str, Any]], Dict[str, Any]]:
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
        last_error: Dict[str, Any] = {
            "status_code": None,
            "error_msg": "Bilinmeyen hata",
            "exception": None,
            "response_text": None
        }

        for attempt in range(1, max_attempts + 1):
            try:
                resp = self.session.post(self.SEARCH_URL, json=payload, timeout=30)
                last_error["status_code"] = resp.status_code
                last_error["response_text"] = resp.text
                last_error["exception"] = None

                if self._check_security_block(resp.status_code, resp.text):
                    bekleme = min(60, 8 * attempt)
                    msg = f"Erişim sınırı / Güvenlik engeli [HTTP {resp.status_code}] | Yanıt: {resp.text}"
                    last_error["error_msg"] = msg
                    logger.warning(
                        f"Arama ({date_str} sf {current_page}) - {msg}. "
                        f"Oturum yenileniyor, {bekleme}s bekleniyor... (Deneme {attempt}/{max_attempts})"
                    )
                    self._renew_session()
                    time.sleep(bekleme)
                    continue

                if "json" not in resp.headers.get("Content-Type", "").lower():
                    bekleme = min(60, 10 * attempt)
                    msg = f"JSON olmayan yanıt [HTTP {resp.status_code}, Content-Type: {resp.headers.get('Content-Type')}] | Yanıt: {resp.text}"
                    last_error["error_msg"] = msg
                    logger.warning(
                        f"Arama ({date_str} sf {current_page}) - {msg}. "
                        f"Oturum yenileniyor, {bekleme}s bekleniyor... (Deneme {attempt}/{max_attempts})"
                    )
                    self._renew_session()
                    time.sleep(bekleme)
                    continue

                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, dict) and "data" in data and data.get("data") is not None:
                        return True, data, {}
                    else:
                        msg = f"HTTP 200 ancak 'data' alanı eksik veya boş: {resp.text}"
                        last_error["error_msg"] = msg
                        logger.warning(f"Arama ({date_str} sf {current_page}, deneme {attempt}/{max_attempts}) - {msg}")
                else:
                    msg = f"HTTP {resp.status_code} Hatası | Yanıt: {resp.text}"
                    last_error["error_msg"] = msg
                    logger.warning(f"Arama ({date_str} sf {current_page}, deneme {attempt}/{max_attempts}) - {msg}")

                time.sleep(1 + attempt)
            except Exception as e:
                bekleme = min(40, 3 * attempt)
                exc_str = f"{type(e).__name__}: {e}"
                last_error["exception"] = exc_str
                last_error["error_msg"] = f"Exception: {exc_str}"
                logger.warning(
                    f"Arama hatası ({date_str} sf {current_page}, deneme {attempt}/{max_attempts}): {exc_str}. "
                    f"{bekleme}s bekleniyor..."
                )
                time.sleep(bekleme)

        return False, None, last_error

    def fetch_decision_detail(
        self,
        doc_id: str,
        max_attempts: int = 10
    ) -> Tuple[bool, Optional[Dict[str, Any]], Dict[str, Any]]:
        payload = {"data": {"documentId": str(doc_id)}}
        last_error: Dict[str, Any] = {
            "status_code": None,
            "error_msg": "Bilinmeyen hata",
            "exception": None,
            "response_text": None
        }

        for attempt in range(1, max_attempts + 1):
            try:
                resp = self.session.post(self.DOC_URL, json=payload, timeout=30)
                last_error["status_code"] = resp.status_code
                last_error["response_text"] = resp.text
                last_error["exception"] = None

                if self._check_security_block(resp.status_code, resp.text):
                    bekleme = min(60, 6 * attempt)
                    msg = f"Erişim sınırı / Güvenlik engeli [HTTP {resp.status_code}] | Yanıt: {resp.text}"
                    last_error["error_msg"] = msg
                    logger.warning(
                        f"Detay (ID: {doc_id}) - {msg}. "
                        f"Oturum yenileniyor, {bekleme}s bekleniyor... (Deneme {attempt}/{max_attempts})"
                    )
                    self._renew_session()
                    time.sleep(bekleme)
                    continue

                if "json" not in resp.headers.get("Content-Type", "").lower():
                    bekleme = min(60, 8 * attempt)
                    msg = f"JSON olmayan yanıt [HTTP {resp.status_code}, Content-Type: {resp.headers.get('Content-Type')}] | Yanıt: {resp.text}"
                    last_error["error_msg"] = msg
                    logger.warning(
                        f"Detay (ID: {doc_id}) - {msg}. "
                        f"Oturum yenileniyor, {bekleme}s bekleniyor... (Deneme {attempt}/{max_attempts})"
                    )
                    self._renew_session()
                    time.sleep(bekleme)
                    continue

                if resp.status_code == 200:
                    res = resp.json()
                    if isinstance(res, dict) and "data" in res and res.get("data") is not None:
                        return True, res, {}
                    else:
                        msg = f"HTTP 200 ancak API 'data' alanı boş [Kalıcı Hata]: {resp.text}"
                        last_error["error_msg"] = msg
                        last_error["kalici_hata"] = True
                        logger.warning(f"Detay (ID: {doc_id}, deneme {attempt}/{max_attempts}) - {msg}")
                        if attempt >= 2:
                            return False, None, last_error
                elif resp.status_code == 404:
                    msg = f"Karar detayı bulunamadı [HTTP 404 - Kalıcı Hata] | Yanıt: {resp.text}"
                    last_error["error_msg"] = msg
                    last_error["kalici_hata"] = True
                    logger.warning(f"Detay (ID: {doc_id}) - {msg}")
                    return False, None, last_error
                else:
                    msg = f"HTTP {resp.status_code} Hatası | Yanıt: {resp.text}"
                    last_error["error_msg"] = msg
                    logger.warning(f"Detay (ID: {doc_id}, deneme {attempt}/{max_attempts}) - {msg}")

                time.sleep(1 + attempt)
            except Exception as e:
                bekleme = min(30, 2 * attempt)
                exc_str = f"{type(e).__name__}: {e}"
                last_error["exception"] = exc_str
                last_error["error_msg"] = f"Exception: {exc_str}"
                logger.warning(
                    f"Detay hatası (ID: {doc_id}, deneme {attempt}/{max_attempts}): {exc_str}. "
                    f"{bekleme}s bekleniyor..."
                )
                time.sleep(bekleme)

        return False, None, last_error

    def save_decision_file(
        self,
        karar_obj: Optional[Dict[str, Any]],
        detay_obj: Dict[str, Any],
        date_str: str,
        doc_id_fallback: str = ""
    ) -> Optional[str]:
        doc_id = str((karar_obj or {}).get("documentId") or doc_id_fallback)
        parts = date_str.split("-") if date_str and "-" in date_str else []
        if len(parts) >= 3 and parts[0].isdigit():
            y, m, d = parts[0], parts[1], parts[2]
            folder = os.path.join(self.base_dir, y, m, d)
        else:
            y, m, d = "tarihsiz", "", ""
            folder = os.path.join(self.base_dir, "tarihsiz")

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

        tmp_file_path = f"{file_path}.tmp"
        with open(tmp_file_path, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
        os.replace(tmp_file_path, file_path)

        self.record_decision_to_db(doc_id, date_str, y, m, d, file_path)
        self.remove_failed_decision(doc_id)
        return file_path

    def scrape(self, max_karar: Optional[int] = None):
        logger.info("=" * 70)
        logger.info("Danıştay Karar İndirici Başlatıldı")
        logger.info(f"Tarih Aralığı: {self.end_date.strftime('%Y-%m-%d')} -> {self.start_date.strftime('%Y-%m-%d')}")
        logger.info(f"Hedef Arşiv: {self.base_dir}")
        logger.info(f"Hata Kayıt Dosyası: {self.hata_json_path}")
        logger.info("=" * 70)

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
            day_processed_count = 0
            day_records_count = 0
            total_pages = None
            consecutive_page_failures = 0

            while True:
                if total_pages is not None and page > total_pages:
                    break

                success, search_resp, page_err = self.fetch_search_page(current_dt, page, max_attempts=5)
                if not success or not search_resp:
                    day_success = False
                    consecutive_page_failures += 1
                    logger.error(
                        f"[HATA] {date_str} - Sayfa {page} 5 denemede alınamadı! "
                        f"Hata: {page_err.get('error_msg')} | HTTP: {page_err.get('status_code')} | "
                        f"Yanıt: {page_err.get('response_text')}"
                    )
                    self.record_failed_search_page(date_str, page, page_err)

                    if total_pages is not None and page < total_pages:
                        logger.warning(f"{date_str} - Sayfa {page} atlanıyor, sıradaki sayfaya ({page + 1}/{total_pages}) geçiliyor...")
                        page += 1
                        time.sleep(1)
                        continue
                    elif total_pages is None and consecutive_page_failures < 3:
                        logger.warning(f"{date_str} - Sayfa {page} alınamadı (toplam sayfa bilinmiyor), sayfa {page + 1} deneniyor...")
                        page += 1
                        time.sleep(1)
                        continue
                    else:
                        logger.warning(f"{date_str} günü için kalan sayfalar alınamadı, sonraki güne geçiliyor.")
                        break

                consecutive_page_failures = 0
                self.remove_failed_search_page(date_str, page)

                veri = search_resp.get("data", {})
                karar_listesi = veri.get("emsalKararList", [])
                total_in_day = veri.get("total", 0)

                if total_in_day > 0:
                    day_records_count = total_in_day
                    total_pages = (total_in_day + self.page_size - 1) // self.page_size

                if total_in_day == 0 or not karar_listesi:
                    if page == 1 and total_in_day == 0:
                        logger.info(f"{date_str}: Bu tarihe ait karar bulunamadı (0 karar).")
                    break

                logger.info(
                    f"{date_str} - Sayfa {page}/{total_pages or '?'}: "
                    f"{len(karar_listesi)} karar bulundu (Günün Toplamı: {total_in_day})"
                )

                for karar in karar_listesi:
                    doc_id = str(karar.get("documentId") or "")
                    if not doc_id:
                        continue

                    if doc_id in self.downloaded_ids:
                        day_processed_count += 1
                        continue

                    detay_ok, detay_resp, detay_err = self.fetch_decision_detail(doc_id)
                    if not detay_ok or not detay_resp:
                        logger.error(
                            f"[HATA] Karar detayı alınamadı (ID: {doc_id}) | "
                            f"Hata: {detay_err.get('error_msg')} | "
                            f"HTTP: {detay_err.get('status_code')} | "
                            f"Yanıt: {detay_err.get('response_text')}"
                        )
                        self.record_failed_decision(doc_id, date_str, detay_err, karar_obj=karar)
                        day_success = False
                        continue

                    saved_path = self.save_decision_file(karar, detay_resp, date_str)
                    if saved_path:
                        total_downloaded += 1
                        day_total_saved += 1
                        day_processed_count += 1
                        esas_no = karar.get("esasNo", "")
                        karar_no = karar.get("kararNo", "")
                        logger.info(
                            f"  [{day_processed_count}/{total_in_day}] Karar kaydedildi: "
                            f"ID {doc_id} | {esas_no} | {karar_no}"
                        )

                    if max_karar and total_downloaded >= max_karar:
                        logger.info(f"Belirtilen indirme limitine ({max_karar}) ulaşıldı.")
                        if day_success and day_processed_count >= total_in_day:
                            self.mark_day_completed(date_str, total_in_day)
                        return

                    time.sleep(self.delay)

                if day_processed_count >= total_in_day:
                    break
                if total_pages is not None and page >= total_pages:
                    break
                if len(karar_listesi) < self.page_size and (total_pages is None or page >= total_pages):
                    break

                page += 1
                time.sleep(0.5)

            if day_records_count > 0 and day_processed_count < day_records_count:
                neden = (
                    f"API toplam {day_records_count} karar bildirdi ancak "
                    f"{day_processed_count} karar işlenebildi (Yeni indirilen: {day_total_saved})."
                )
                logger.warning(f"[UYARI] {date_str}: {neden} Gün tamamlandı olarak işaretlenmiyor.")
                self.record_incomplete_day(date_str, neden, day_records_count, day_processed_count)
                day_success = False
            elif not day_success:
                neden = "Gün içinde alınamayan arama sayfası veya indirilemeyen karar detayı mevcut."
                self.record_incomplete_day(date_str, neden, day_records_count, day_processed_count)

            if day_success:
                self.mark_day_completed(date_str, day_records_count)
                logger.info(
                    f"{date_str} tamamlandı. (Yeni indirilen: {day_total_saved}, "
                    f"Gün toplamı: {day_processed_count}/{day_records_count})"
                )
            else:
                logger.warning(
                    f"{date_str} bazı hatalı kararlar veya eksik sayfalar nedeniyle tam tamamlanamadı. "
                    f"Hatalar '{self.hata_json_path}' dosyasına ve veritabanına kaydedildi."
                )

            current_dt -= timedelta(days=1)
            time.sleep(0.2)

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
