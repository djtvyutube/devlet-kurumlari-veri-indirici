"""
Yargıtay Karar İndirici

karararama-services.yargitay.gov.tr REST API üzerinden kararları gün gün tarar
ve detay yanıtlarını (/api/kararlar/karar/{id}) ham JSON olarak kaydeder.
"""

import os
import sys
import time
import json
import shutil
import sqlite3
import logging
import argparse
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Tuple
import requests

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("Yargitay")


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
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": "https://karararama.yargitay.gov.tr",
            "Referer": "https://karararama.yargitay.gov.tr/",
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

    def record_failed_decision(
        self,
        dec_id: str,
        date_str: str,
        error_info: Dict[str, Any],
        karar_obj: Optional[Dict[str, Any]] = None
    ):
        now_iso = datetime.now().isoformat()
        kunye_json = json.dumps(karar_obj, ensure_ascii=False) if karar_obj else None
        reason = error_info.get("error_msg") or "Detay çekilemedi"
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
        reason = error_info.get("error_msg") or "Arama sayfası alınamadı"
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

    def fetch_search_page(
        self,
        date_str: str,
        page: int,
        max_attempts: int = 10
    ) -> Tuple[bool, Optional[Dict[str, Any]], Dict[str, Any]]:
        params = {
            "page": page,
            "size": self.page_size,
            "sortOrder": "tarih_desc",
            "kararTarihiMin": date_str,
            "kararTarihiMax": date_str
        }
        last_error: Dict[str, Any] = {
            "status_code": None,
            "error_msg": "Bilinmeyen hata",
            "exception": None,
            "response_text": None
        }

        for attempt in range(1, max_attempts + 1):
            try:
                resp = self.session.get(self.SEARCH_ENDPOINT, params=params, timeout=25)
                last_error["status_code"] = resp.status_code
                last_error["response_text"] = resp.text
                last_error["exception"] = None

                if resp.status_code == 200:
                    try:
                        return True, resp.json(), {}
                    except json.JSONDecodeError as je:
                        msg = f"JSONDecodeError: {je} | Yanıt: {resp.text}"
                        last_error["error_msg"] = msg
                        last_error["exception"] = f"JSONDecodeError: {je}"
                        logger.warning(f"Arama ({date_str}, sayfa {page}) yanıtı JSON değil: {msg}. (Deneme {attempt}/{max_attempts})")
                        self._renew_session()
                        time.sleep(15)
                        continue

                if self._check_security_block(resp.status_code, resp.text):
                    wait_sec = min(10 * attempt, 60)
                    msg = f"Erişim sınırı / Güvenlik engeli [HTTP {resp.status_code}] | Yanıt: {resp.text}"
                    last_error["error_msg"] = msg
                    logger.warning(
                        f"Arama ({date_str}, sayfa {page}): {msg}. "
                        f"Oturum yenileniyor, {wait_sec}s bekleniyor... (Deneme {attempt}/{max_attempts})"
                    )
                    self._renew_session()
                    time.sleep(wait_sec)
                    continue
                else:
                    msg = f"HTTP {resp.status_code} Hatası | Yanıt: {resp.text}"
                    last_error["error_msg"] = msg
                    logger.warning(f"Arama isteği başarısız ({date_str}, sayfa {page}, deneme {attempt}/{max_attempts}): {msg}")
                    time.sleep(3 * attempt)
            except Exception as e:
                exc_str = f"{type(e).__name__}: {e}"
                last_error["exception"] = exc_str
                last_error["error_msg"] = f"Exception: {exc_str}"
                logger.error(f"Arama isteği hatası ({date_str}, sayfa {page}, deneme {attempt}/{max_attempts}): {exc_str}")
                self._renew_session()
                time.sleep(3 * attempt)

        return False, None, last_error

    def fetch_decision_detail(
        self,
        decision_id: str,
        max_attempts: int = 10
    ) -> Tuple[bool, Optional[Dict[str, Any]], Dict[str, Any]]:
        url = f"{self.DETAIL_ENDPOINT}/{decision_id}"
        last_error: Dict[str, Any] = {
            "status_code": None,
            "error_msg": "Bilinmeyen hata",
            "exception": None,
            "response_text": None,
            "kalici_hata": False
        }

        for attempt in range(1, max_attempts + 1):
            try:
                resp = self.session.get(url, timeout=25)
                last_error["status_code"] = resp.status_code
                last_error["response_text"] = resp.text
                last_error["exception"] = None

                if resp.status_code == 200:
                    try:
                        return True, resp.json(), {}
                    except json.JSONDecodeError as je:
                        msg = f"JSONDecodeError: {je} | Yanıt: {resp.text}"
                        last_error["error_msg"] = msg
                        last_error["exception"] = f"JSONDecodeError: {je}"
                        logger.warning(f"Detay ({decision_id}) yanıtı JSON değil: {msg}. (Deneme {attempt}/{max_attempts})")
                        self._renew_session()
                        time.sleep(15)
                        continue

                if self._check_security_block(resp.status_code, resp.text):
                    wait_sec = min(8 * attempt, 60)
                    msg = f"Erişim engeli [HTTP {resp.status_code}] | Yanıt: {resp.text}"
                    last_error["error_msg"] = msg
                    logger.warning(
                        f"Detay ({decision_id}): {msg}. "
                        f"Oturum yenileniyor, {wait_sec}s bekleniyor... (Deneme {attempt}/{max_attempts})"
                    )
                    self._renew_session()
                    time.sleep(wait_sec)
                    continue
                elif resp.status_code == 404:
                    msg = f"Karar detayı bulunamadı [HTTP 404 - Kalıcı Hata] | Yanıt: {resp.text}"
                    last_error["error_msg"] = msg
                    last_error["kalici_hata"] = True
                    logger.warning(f"Detay ({decision_id}): {msg}")
                    return False, None, last_error
                else:
                    msg = f"HTTP {resp.status_code} Hatası | Yanıt: {resp.text}"
                    last_error["error_msg"] = msg
                    logger.warning(f"Detay isteği başarısız ({decision_id}, deneme {attempt}/{max_attempts}): {msg}")
                    time.sleep(2 * attempt)
            except Exception as e:
                exc_str = f"{type(e).__name__}: {e}"
                last_error["exception"] = exc_str
                last_error["error_msg"] = f"Exception: {exc_str}"
                logger.error(f"Detay çekme hatası ({decision_id}, deneme {attempt}/{max_attempts}): {exc_str}")
                self._renew_session()
                time.sleep(3 * attempt)

        return False, None, last_error

    def save_decision(
        self,
        raw_data: Dict[str, Any],
        date_str: str,
        fallback_id: str = ""
    ) -> Optional[Tuple[str, str, str, str, str]]:
        decision_id = str(raw_data.get("id") or fallback_id)
        if not decision_id:
            return None

        karar_tarihi = raw_data.get("kararTarihi") or date_str or ""
        year_str, month_str, day_str = "", "", ""
        resolved_date_str = ""

        if karar_tarihi and len(karar_tarihi) >= 10:
            try:
                dt = datetime.strptime(karar_tarihi[:10], "%Y-%m-%d")
                year_str = f"{dt.year:04d}"
                month_str = f"{dt.month:02d}"
                day_str = f"{dt.day:02d}"
                resolved_date_str = f"{year_str}-{month_str}-{day_str}"
            except Exception:
                pass

        if not year_str and date_str and len(date_str) >= 10 and "-" in date_str:
            parts = date_str[:10].split("-")
            if len(parts) >= 3 and parts[0].isdigit():
                year_str, month_str, day_str = parts[0], parts[1], parts[2]
                resolved_date_str = f"{year_str}-{month_str}-{day_str}"

        if year_str:
            target_dir = os.path.join(self.base_dir, year_str, month_str, day_str)
        else:
            year_str, month_str, day_str = "tarihsiz", "", ""
            target_dir = os.path.join(self.base_dir, "tarihsiz")

        os.makedirs(target_dir, exist_ok=True)

        file_name = f"karar_{decision_id}.json"
        file_path = os.path.join(target_dir, file_name)
        tmp_file_path = f"{file_path}.tmp"

        with open(tmp_file_path, "w", encoding="utf-8") as f:
            json.dump(raw_data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_file_path, file_path)

        rel_path = os.path.relpath(file_path, self.base_dir)
        return rel_path, resolved_date_str, year_str, month_str, day_str

    def _record_decision_to_db(self, dec_id: str, karar_tarihi: str, y: str, m: str, d: str, saved_path: str):
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        clean_path = saved_path.replace("\\", "/")
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("""
                INSERT OR REPLACE INTO indirilen_kararlar
                (id, karar_tarihi, yil, ay, gun, dosya_yolu, eklenme_tarihi)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (str(dec_id), karar_tarihi, y, m, d, clean_path, now_str))
            conn.commit()
        self._append_to_idlist(str(dec_id), clean_path, now_str)

    def process_day(self, date_str: str, max_karar: Optional[int] = None, current_total: int = 0) -> Tuple[int, bool]:
        ok_first, first_page, first_err = self.fetch_search_page(date_str, page=0)
        if not ok_first or not first_page:
            logger.error(
                f"[HATA] Günün ilk sayfası alınamadı ({date_str}, sayfa 0) | "
                f"Hata: {first_err.get('error_msg')} | HTTP: {first_err.get('status_code')} | "
                f"Yanıt: {first_err.get('response_text')}"
            )
            self.record_failed_search_page(date_str, 0, first_err)
            self.record_incomplete_day(date_str, "Günün ilk arama sayfası (sayfa 0) alınamadı.", 0, 0)
            return 0, False

        self.remove_failed_search_page(date_str, 0)

        total_elements = first_page.get("totalElements", 0)
        total_pages = first_page.get("totalPages", 0)

        if total_elements == 0:
            logger.info(f"{date_str}: Karar bulunamadı.")
            self.mark_day_completed(date_str, 0)
            return 0, False

        logger.info(f"{date_str}: Toplam {total_elements} karar ({total_pages} sayfa) bulundu. İndiriliyor...")

        saved_count = 0
        current_page = 0
        overall_idx = 0
        has_any_failure = False

        while current_page < total_pages:
            if current_page == 0:
                page_data = first_page
            else:
                ok_page, page_data, page_err = self.fetch_search_page(date_str, current_page)
                if not ok_page or not page_data or "content" not in page_data:
                    logger.error(
                        f"[HATA] {date_str} - Sayfa {current_page} alınamadı, hata tablosuna kaydedilip sonraki sayfaya geçiliyor! "
                        f"Hata: {page_err.get('error_msg')} | HTTP: {page_err.get('status_code')} | "
                        f"Yanıt: {page_err.get('response_text')}"
                    )
                    self.record_failed_search_page(date_str, current_page, page_err)
                    has_any_failure = True
                    current_page += 1
                    continue

                self.remove_failed_search_page(date_str, current_page)

            items = page_data.get("content", [])

            for item in items:
                overall_idx += 1
                dec_id = str(item.get("id") or "")
                if not dec_id:
                    continue

                if dec_id in self.downloaded_ids:
                    continue

                detay_ok, detail, detay_err = self.fetch_decision_detail(dec_id)
                if not detay_ok or detail is None:
                    logger.error(
                        f"[HATA] Karar detayı çekilemedi (ID: {dec_id}) | "
                        f"Hata: {detay_err.get('error_msg')} | HTTP: {detay_err.get('status_code')} | "
                        f"Yanıt: {detay_err.get('response_text')}"
                    )
                    self.record_failed_decision(dec_id, date_str, detay_err, karar_obj=item)
                    has_any_failure = True
                    continue

                save_res = self.save_decision(detail, date_str, fallback_id=dec_id)
                if save_res:
                    saved_path, karar_tarihi, y, m, d = save_res
                    self.downloaded_ids.add(dec_id)
                    self._record_decision_to_db(dec_id, karar_tarihi, y, m, d, saved_path)
                    self.remove_failed_decision(dec_id)
                    saved_count += 1
                    logger.info(f"  [{overall_idx}/{total_elements}] Karar kaydedildi: {dec_id}")
                else:
                    save_err = {
                        "status_code": 200,
                        "error_msg": "Karar dosyaya kaydedilemedi (save_decision None döndü)",
                        "exception": None,
                        "response_text": json.dumps(detail, ensure_ascii=False)
                    }
                    logger.error(f"[HATA] Karar diske kaydedilemedi (ID: {dec_id})")
                    self.record_failed_decision(dec_id, date_str, save_err, karar_obj=item)
                    has_any_failure = True

                if max_karar and (current_total + saved_count) >= max_karar:
                    logger.info(f"Belirtilen indirme limitine ({max_karar}) ulaşıldı.")
                    if not has_any_failure and overall_idx >= total_elements:
                        self.mark_day_completed(date_str, total_elements)
                    return saved_count, True

                if self.delay > 0:
                    time.sleep(self.delay)

            current_page += 1

        if overall_idx < total_elements:
            has_any_failure = True

        if not has_any_failure:
            self.mark_day_completed(date_str, total_elements)
            logger.info(f"{date_str} tamamlandı. ({saved_count} yeni karar | Gün toplamı: {total_elements} | Toplam arşiv: {len(self.downloaded_ids)})")
        else:
            self.record_incomplete_day(
                date_str,
                "Bazı arama sayfaları veya karar detayları indirilemedi.",
                expected_total=total_elements,
                processed_count=overall_idx
            )
            logger.warning(
                f"{date_str} bazı hatalarla bitti. Hatalar '{self.hata_json_path}' dosyasına ve veritabanına kaydedildi."
            )

        return saved_count, False

    def run(self, max_karar: Optional[int] = None):
        logger.info("=" * 60)
        logger.info("Yargıtay Karar İndirici Başlatılıyor")
        logger.info(f"Tarih Aralığı: {self.end_date.strftime('%Y-%m-%d')} -> {self.start_date.strftime('%Y-%m-%d')}")
        logger.info(f"Kayıt Dizini: {self.base_dir}")
        logger.info(f"Hata Kayıt Dosyası: {self.hata_json_path}")
        logger.info("=" * 60)

        current_dt = self.end_date
        total_days = (self.end_date - self.start_date).days + 1
        day_idx = 0
        total_downloaded = 0

        while current_dt >= self.start_date:
            day_idx += 1
            date_str = current_dt.strftime("%Y-%m-%d")

            if self.is_day_completed(date_str):
                logger.info(f"[{day_idx}/{total_days}] {date_str} daha önce tamamlanmış, atlanıyor.")
                current_dt -= timedelta(days=1)
                continue

            logger.info(f"\n--- [{day_idx}/{total_days}] Gün İşleniyor: {date_str} ---")
            try:
                saved_in_day, limit_reached = self.process_day(date_str, max_karar=max_karar, current_total=total_downloaded)
                total_downloaded += saved_in_day
                if limit_reached:
                    break
            except KeyboardInterrupt:
                logger.info("\nKullanıcı tarafından durduruldu.")
                sys.exit(0)
            except Exception as e:
                logger.error(f"{date_str} işlenirken hata: {e}", exc_info=True)
                time.sleep(3)

            current_dt -= timedelta(days=1)

        logger.info("=" * 60)
        logger.info("Tarama tamamlandı.")
        logger.info(f"Toplam kaydedilen karar: {len(self.downloaded_ids)}")
        logger.info("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Yargıtay Karar İndirici")
    parser.add_argument("--output", "--dir", dest="output", default="yargitay_arsiv", help="Çıktı klasörü (varsayılan: yargitay_arsiv)")
    parser.add_argument("--start", default=None, help="En eski tarih (YYYY-MM-DD, varsayılan: 1970-01-01)")
    parser.add_argument("--end", default=None, help="Başlangıç/en yeni tarih (YYYY-MM-DD, varsayılan: Bugün)")
    parser.add_argument("--limit", type=int, default=None, help="Maksimum indirilecek karar sayısı")
    parser.add_argument("--delay", type=float, default=0.25, help="İstekler arası gecikme (saniye, varsayılan: 0.25)")
    parser.add_argument("--page-size", type=int, default=100, help="Sayfa başına kayıt (En fazla 100, varsayılan: 100)")

    args = parser.parse_args()

    scraper = YargitayScraper2(
        base_dir=args.output,
        start_date=args.start,
        end_date=args.end,
        delay=args.delay,
        page_size=args.page_size
    )
    scraper.run(max_karar=args.limit)
