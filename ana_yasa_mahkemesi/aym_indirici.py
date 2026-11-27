"""
Anayasa Mahkemesi Karar İndirici

Kararlar Bilgi Bankası API'sini gün bazında tarar ve dönen yanıtları
değiştirmeden ham JSON olarak Yıl/Ay/Gün (veya tarihsiz/) klasör yapısında diske kaydeder.
SQLite (archive_state.db), hata.json ve kompakt idlist.json ile durum takibi yapar.
"""

import argparse
import os
import sys
import time
import json
import random
import re
import base64
import hashlib
import shutil
import sqlite3
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, Tuple, List
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("AYM")


class AnayasaMahkemesiScraper:
    def __init__(self, root_output_dir: str = "aymjson", delay: float = 0.1, page_size: int = 50):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        if not os.path.isabs(root_output_dir):
            self.root_output_dir = os.path.abspath(os.path.join(script_dir, root_output_dir))
        else:
            self.root_output_dir = os.path.abspath(root_output_dir)

        if not os.path.exists(self.root_output_dir):
            os.makedirs(self.root_output_dir, exist_ok=True)
            logger.info(f"Çıktı klasörü oluşturuldu: {self.root_output_dir}")

        self.delay = delay
        self.page_size = page_size
        self.api_url = "https://kararlarbilgibankasi.anayasa.gov.tr/api/core/public/search"

        self.db_path = os.path.join(self.root_output_dir, "archive_state.db")
        self.hata_json_path = os.path.join(self.root_output_dir, "hata.json")
        self.idlist_path = os.path.join(self.root_output_dir, "idlist.json")
        self.ilerleme_dosyasi = os.path.join(self.root_output_dir, "ilerleme_durumu.json")

        self._idlist_initialized = os.path.exists(self.idlist_path) and os.path.getsize(self.idlist_path) > 4
        self.init_db()

        self.session = self.olustur_oturum()
        self.downloaded_ids = set()
        self.mevcut_dosyalar = set()
        self.aktif_tarih = None

        self._load_existing_ids()
        self._sync_disk_files_to_db()
        self._migrate_legacy_ilerleme()

    def olustur_oturum(self) -> requests.Session:
        session = requests.Session()
        session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": "https://kararlarbilgibankasi.anayasa.gov.tr",
            "Referer": "https://kararlarbilgibankasi.anayasa.gov.tr/kbb/pages/search/Tumu"
        })

        retry_strategy = Retry(
            total=2,
            backoff_factor=1.0,
            status_forcelist=[502, 503, 504],
            allowed_methods=["POST", "GET"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=20, pool_maxsize=20)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    def init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("""
                CREATE TABLE IF NOT EXISTS indirilen_kararlar (
                    id TEXT PRIMARY KEY,
                    dosya_adi TEXT,
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
                    karar_tipi TEXT,
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
                "ALTER TABLE indirilen_kararlar ADD COLUMN dosya_adi TEXT",
                "ALTER TABLE hatali_kararlar ADD COLUMN karar_tipi TEXT",
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
            c.execute("SELECT id, dosya_adi FROM indirilen_kararlar")
            for dec_id, dosya_adi in c.fetchall():
                if dec_id:
                    str_id = str(dec_id)
                    self.downloaded_ids.add(str_id)
                    self.downloaded_ids.add(self.temizle_id_degeri(str_id))
                if dosya_adi:
                    str_fname = str(dosya_adi)
                    self.mevcut_dosyalar.add(str_fname)
                    if str_fname.startswith("karar_") and str_fname.endswith(".json"):
                        self.downloaded_ids.add(str_fname[6:-5])

    def _sync_disk_files_to_db(self):
        ignored_files = {"ilerleme_durumu.json", "hata.json", "idlist.json"}
        disk_new = []
        for root, _, files in os.walk(self.root_output_dir):
            for f in files:
                if f.endswith(".json") and f not in ignored_files and f.startswith("karar_"):
                    full_path = os.path.join(root, f)
                    try:
                        if os.path.getsize(full_path) < 2:
                            continue
                    except OSError:
                        continue
                    dec_id = f[6:-5]
                    zaten_kayitli = (f in self.mevcut_dosyalar) or (dec_id in self.downloaded_ids)
                    self.mevcut_dosyalar.add(f)
                    if dec_id and not zaten_kayitli:
                        self.downloaded_ids.add(dec_id)
                        rel_path = os.path.relpath(full_path, self.root_output_dir).replace("\\", "/")
                        parts = rel_path.split("/")
                        if len(parts) >= 3 and parts[0].isdigit():
                            y, m, d = parts[0], parts[1], parts[2]
                            k_tarihi = f"{y}-{m}-{d}"
                        else:
                            y, m, d, k_tarihi = "tarihsiz", "", "", ""
                        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        disk_new.append((dec_id, f, k_tarihi, y, m, d, rel_path, now_str))

        if disk_new:
            with sqlite3.connect(self.db_path) as conn:
                c = conn.cursor()
                c.executemany("""
                    INSERT OR IGNORE INTO indirilen_kararlar
                    (id, dosya_adi, karar_tarihi, yil, ay, gun, dosya_yolu, eklenme_tarihi)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, disk_new)
                conn.commit()
            for dec_id, _, _, _, _, _, rel_path, now_str in disk_new:
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

    def _migrate_legacy_ilerleme(self):
        if not os.path.exists(self.ilerleme_dosyasi):
            return
        try:
            with open(self.ilerleme_dosyasi, "r", encoding="utf-8") as f:
                veri = json.load(f)
            if not isinstance(veri, dict):
                return
            hatali_idler = veri.get("hatali_idler", [])
            if isinstance(hatali_idler, list) and hatali_idler:
                eklenen = 0
                for raw_id in hatali_idler:
                    str_id = str(raw_id)
                    if str_id not in self.downloaded_ids and self.dosya_adi_olustur(str_id) not in self.mevcut_dosyalar:
                        self.record_failed_decision(
                            dec_id=str_id,
                            date_str="",
                            error_info={
                                "status_code": None,
                                "error_msg": "Eski ilerleme_durumu.json dosyasından aktarıldı",
                                "exception": None,
                                "response_text": None,
                                "kalici_hata": False
                            }
                        )
                        eklenen += 1
                if eklenen > 0:
                    logger.info(f"Eski ilerleme_durumu.json içinden {eklenen} adet hatalı karar ID'si hata.json ve veritabanına aktarıldı.")
        except json.JSONDecodeError as je:
            corrupt_backup = f"{self.ilerleme_dosyasi}.corrupt.{int(time.time())}"
            logger.error(f"[KRİTİK] ilerleme_durumu.json bozuk ({je})! Veri kaybını önlemek için yedekleniyor: {corrupt_backup}")
            try:
                shutil.copy2(self.ilerleme_dosyasi, corrupt_backup)
            except Exception as copy_err:
                logger.error(f"Bozuk ilerleme_durumu.json yedeklenemedi: {copy_err}")
        except Exception as e:
            logger.error(f"ilerleme_durumu.json okunurken hata: {e}")

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
                logger.error(f"[KRİTİK] hata.json bozuk ({je})! Üzerine yazılmadan önce yedekleniyor: {corrupt_backup}")
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

    def ilerleme_ozeti_kaydet(self, tarih_str: str):
        try:
            gecici_dosya = f"{self.ilerleme_dosyasi}.tmp"
            with open(gecici_dosya, "w", encoding="utf-8") as f:
                json.dump({
                    "son_islenen_tarih": tarih_str,
                    "toplam_indirilen_karar": len(self.downloaded_ids),
                    "idlist_dosyasi": "idlist.json",
                    "hata_dosyasi": "hata.json",
                    "veritabani_dosyasi": "archive_state.db",
                    "guncelleme_zamani_utc": datetime.now(timezone.utc).isoformat()
                }, f, ensure_ascii=False, indent=2)
            os.replace(gecici_dosya, self.ilerleme_dosyasi)
        except Exception as e:
            logger.error(f"ilerleme_durumu.json yazılırken hata oluştu: {e}")

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

    def record_decision_to_db(self, dec_id: str, dosya_adi: str, date_str: str, y: str, m: str, d: str, rel_path: str):
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        clean_rel_path = rel_path.replace("\\", "/")
        self.downloaded_ids.add(str(dec_id))
        self.mevcut_dosyalar.add(dosya_adi)

        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("""
                INSERT OR REPLACE INTO indirilen_kararlar
                (id, dosya_adi, karar_tarihi, yil, ay, gun, dosya_yolu, eklenme_tarihi)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (str(dec_id), dosya_adi, date_str, y, m, d, clean_rel_path, now_str))
            conn.commit()

        self._append_to_idlist(str(dec_id), clean_rel_path, now_str)

    def record_failed_decision(
        self,
        dec_id: str,
        date_str: str,
        error_info: Dict[str, Any],
        karar_obj: Optional[Dict[str, Any]] = None,
        karar_tipi: Optional[str] = None
    ):
        now_iso = datetime.now().isoformat()
        kunye_json = json.dumps(karar_obj, ensure_ascii=False) if karar_obj else None
        k_tip = karar_tipi or (karar_obj.get("kararTipi") if isinstance(karar_obj, dict) else None)
        reason = error_info.get("error_msg") or "Detay içeriği alınamadı"
        status_code = error_info.get("status_code")
        response_text = error_info.get("response_text")
        is_permanent = 1 if (status_code == 404 or error_info.get("kalici_hata")) else 0

        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("""
                INSERT INTO hatali_kararlar (
                    id, karar_tarihi, karar_tipi, arama_kunyesi, hata_nedeni, http_kodu, yanit_govdesi, kalici_hata, deneme_sayisi, son_deneme
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(id) DO UPDATE SET
                    deneme_sayisi = deneme_sayisi + 1,
                    karar_tarihi = CASE WHEN excluded.karar_tarihi != '' THEN excluded.karar_tarihi ELSE hatali_kararlar.karar_tarihi END,
                    karar_tipi = COALESCE(excluded.karar_tipi, hatali_kararlar.karar_tipi),
                    arama_kunyesi = COALESCE(excluded.arama_kunyesi, hatali_kararlar.arama_kunyesi),
                    hata_nedeni = excluded.hata_nedeni,
                    http_kodu = excluded.http_kodu,
                    yanit_govdesi = excluded.yanit_govdesi,
                    kalici_hata = excluded.kalici_hata,
                    son_deneme = excluded.son_deneme
            """, (str(dec_id), date_str, k_tip, kunye_json, reason, status_code, response_text, is_permanent, now_iso))
            conn.commit()

        hata_data = self._load_hata_json()
        existing = hata_data["hatali_kararlar"].get(str(dec_id), {})
        deneme_sayisi = existing.get("deneme_sayisi", 0) + 1
        hata_data["hatali_kararlar"][str(dec_id)] = {
            "id": str(dec_id),
            "karar_tarihi": date_str or existing.get("karar_tarihi", ""),
            "karar_tipi": k_tip or existing.get("karar_tipi"),
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

    def temizle_id_degeri(self, raw_id: Any) -> str:
        if not raw_id:
            return "unknown"
        orig_str = str(raw_id).strip()
        candidate = orig_str
        needs_hash_suffix = False

        if orig_str.startswith("a2Ji") or orig_str.startswith("kbb:"):
            if orig_str.startswith("kbb:"):
                parts = orig_str.split(":", 1)
                if len(parts) > 1 and parts[1]:
                    candidate = parts[1]
            else:
                try:
                    padding = (4 - (len(orig_str) % 4)) % 4
                    padded = orig_str + ("=" * padding)
                    decoded_bytes = base64.b64decode(padded, validate=False)
                    decoded_str = decoded_bytes.decode("utf-8", errors="strict")
                    if ":" in decoded_str:
                        parts = decoded_str.split(":", 1)
                        if len(parts) > 1 and parts[1].strip():
                            candidate = parts[1].strip()
                        else:
                            candidate = decoded_str.strip()
                    elif decoded_str.strip():
                        candidate = decoded_str.strip()
                    else:
                        needs_hash_suffix = True
                except Exception:
                    needs_hash_suffix = True

        temiz = re.sub(r'[\\/*?:"<>|\s]+', "_", candidate).strip("_")
        if not temiz:
            short_hash = hashlib.sha256(orig_str.encode("utf-8", errors="replace")).hexdigest()[:12]
            return f"id_{short_hash}"

        if needs_hash_suffix or (temiz != candidate and not re.match(r'^[A-Za-z0-9_.-]+$', candidate)):
            short_hash = hashlib.sha256(orig_str.encode("utf-8", errors="replace")).hexdigest()[:8]
            temiz = f"{temiz}_{short_hash}"

        return temiz

    def dosya_adi_olustur(self, kimlik_degeri: Any) -> str:
        temiz = self.temizle_id_degeri(kimlik_degeri)
        return f"karar_{temiz}.json"

    def api_istek_gonder(self, payload: Dict[str, Any], max_deneme: int = 5) -> Tuple[bool, Optional[Dict[str, Any]], Dict[str, Any]]:
        last_error: Dict[str, Any] = {
            "status_code": None,
            "error_msg": "Bilinmeyen hata",
            "exception": None,
            "response_text": None,
            "kalici_hata": False
        }

        for deneme in range(1, max_deneme + 1):
            try:
                response = self.session.post(self.api_url, json=payload, timeout=25)
                last_error["status_code"] = response.status_code
                last_error["response_text"] = response.text
                last_error["exception"] = None

                if response.status_code == 200:
                    try:
                        data = response.json()
                        if isinstance(data, dict):
                            return True, data, {}
                        else:
                            msg = f"HTTP 200 ancak JSON dict değil | Yanıt: {response.text}"
                            last_error["error_msg"] = msg
                            logger.warning(f"[UYARI] {msg} (Deneme {deneme}/{max_deneme})")
                    except ValueError as je:
                        msg = f"JSONDecodeError: {je} | Yanıt: {response.text}"
                        last_error["error_msg"] = msg
                        last_error["exception"] = f"JSONDecodeError: {je}"
                        logger.warning(f"[UYARI] {msg} (Deneme {deneme}/{max_deneme})")
                elif response.status_code == 429:
                    bekleme = 15 * deneme
                    msg = f"İstek sınırı (HTTP 429) aşıldı | Yanıt: {response.text}"
                    last_error["error_msg"] = msg
                    logger.warning(f"[UYARI] {msg}. {bekleme} saniye bekleniyor... (Deneme {deneme}/{max_deneme})")
                    time.sleep(bekleme)
                    continue
                elif response.status_code == 404:
                    msg = f"Kaynak bulunamadı [HTTP 404 - Kalıcı Hata] | Yanıt: {response.text}"
                    last_error["error_msg"] = msg
                    last_error["kalici_hata"] = True
                    logger.warning(f"[UYARI] {msg}")
                    return False, None, last_error
                else:
                    msg = f"Sunucu HTTP {response.status_code} yanıtı döndü | Yanıt: {response.text}"
                    last_error["error_msg"] = msg
                    logger.warning(f"[UYARI] {msg}. 5 saniye bekleniyor... (Deneme {deneme}/{max_deneme})")
                    time.sleep(5)
            except requests.exceptions.RequestException as e:
                exc_str = f"{type(e).__name__}: {e}"
                last_error["exception"] = exc_str
                last_error["error_msg"] = f"RequestException: {exc_str}"
                logger.error(f"[HATA] Bağlantı hatası (Deneme {deneme}/{max_deneme}): {exc_str}. {3 * deneme} saniye sonra tekrar denenecek.")
                time.sleep(3 * deneme)

        return False, None, last_error

    def gunun_kararlarini_getir(self, tarih_iso: str) -> Tuple[List[Dict[str, Any]], bool, int]:
        tum_kararlar: List[Dict[str, Any]] = []
        page = 1
        page_size = self.page_size
        has_page_failure = False
        expected_total = 0
        total_pages: Optional[int] = None
        consecutive_failures = 0

        while True:
            if total_pages is not None and page > total_pages:
                break

            payload = {
                "kararTarihiStart": tarih_iso,
                "kararTarihiEnd": tarih_iso,
                "page": page,
                "size": page_size
            }
            ok, sonuc, page_err = self.api_istek_gonder(payload, max_deneme=5)
            if not ok or not sonuc:
                has_page_failure = True
                consecutive_failures += 1
                logger.error(
                    f"[HATA] {tarih_iso} - Sayfa {page} 5 denemede alınamadı! "
                    f"Hata: {page_err.get('error_msg')} | HTTP: {page_err.get('status_code')} | "
                    f"Yanıt: {page_err.get('response_text')}"
                )
                self.record_failed_search_page(tarih_iso, page, page_err)

                if total_pages is not None and page < total_pages:
                    logger.warning(f"{tarih_iso} - Sayfa {page} atlanıyor, sayfa {page + 1}/{total_pages} deneniyor...")
                    page += 1
                    time.sleep(1)
                    continue
                elif total_pages is None and consecutive_failures < 3:
                    logger.warning(f"{tarih_iso} - Sayfa {page} alınamadı, sayfa {page + 1} deneniyor...")
                    page += 1
                    time.sleep(1)
                    continue
                else:
                    break

            consecutive_failures = 0
            self.remove_failed_search_page(tarih_iso, page)

            total = sonuc.get("total", 0)
            data = sonuc.get("data", [])

            if total > 0:
                expected_total = total
                total_pages = (total + page_size - 1) // page_size

            if not data:
                break

            tum_kararlar.extend(data)

            if len(tum_kararlar) >= expected_total and expected_total > 0:
                break
            if total_pages is not None and page >= total_pages:
                break
            if len(data) < page_size and (total_pages is None or page >= total_pages):
                break

            page += 1
            time.sleep(0.15)

        return tum_kararlar, has_page_failure, expected_total

    def karar_detay_getir(self, doc_id: str, karar_tipi: Optional[str] = None) -> Tuple[bool, Optional[Dict[str, Any]], Dict[str, Any]]:
        clean_id = self.temizle_id_degeri(doc_id) if doc_id else str(doc_id)
        payload: Dict[str, Any] = {
            "id": clean_id,
            "size": 1
        }
        if karar_tipi is not None:
            payload["kararTipi"] = karar_tipi

        ok, yanit, err_info = self.api_istek_gonder(payload, max_deneme=5)
        if ok and yanit:
            data_list = yanit.get("data")
            total = yanit.get("total", 0)
            if isinstance(data_list, list) and len(data_list) > 0:
                return True, yanit, {}

            if str(doc_id) != clean_id:
                payload["id"] = str(doc_id)
                ok2, yanit2, err_info2 = self.api_istek_gonder(payload, max_deneme=3)
                if ok2 and yanit2 and isinstance(yanit2.get("data"), list) and len(yanit2.get("data")) > 0:
                    return True, yanit2, {}

            err_info = {
                "status_code": 200,
                "error_msg": f"HTTP 200 ancak karar detayı boş (total: {total}, data: []) [Kalıcı Hata]",
                "exception": None,
                "response_text": json.dumps(yanit, ensure_ascii=False),
                "kalici_hata": (total == 0)
            }
            return False, None, err_info

        return False, None, err_info

    def tarih_ayristir(self, item: Optional[Dict[str, Any]], fallback_date_str: str = "") -> Tuple[str, str, str, str]:
        olasi_alanlar = ["kararTarihi", "kararTarih", "basvuruTarihi", "resmiGazeteTarihi", "tarih"]
        tarih_degeri = None

        if isinstance(item, dict):
            for alan in olasi_alanlar:
                deger = item.get(alan)
                if deger and isinstance(deger, str):
                    tarih_degeri = deger.strip()
                    break

        for aday in (tarih_degeri, fallback_date_str):
            if not aday:
                continue
            iso_eslesme = re.match(r"^(\d{4})-(\d{2})-(\d{2})", aday)
            if iso_eslesme:
                y, m, d = iso_eslesme.group(1), iso_eslesme.group(2), iso_eslesme.group(3)
                return y, m, d, f"{y}-{m}-{d}"

            tr_eslesme = re.match(r"^(\d{2})[./](\d{2})[./](\d{4})", aday)
            if tr_eslesme:
                y, m, d = tr_eslesme.group(3), tr_eslesme.group(2), tr_eslesme.group(1)
                return y, m, d, f"{y}-{m}-{d}"

        return "tarihsiz", "", "", ""

    def save_decision_file(
        self,
        detay_yanit: Dict[str, Any],
        raw_id: str,
        fallback_date_str: str = "",
        karar_obj: Optional[Dict[str, Any]] = None
    ) -> Tuple[str, str]:
        detay_data = detay_yanit.get("data")
        ilk_kayit = detay_data[0] if isinstance(detay_data, list) and len(detay_data) > 0 else (karar_obj or {})
        gercek_id = str((ilk_kayit.get("id") if isinstance(ilk_kayit, dict) else None) or raw_id)

        yil, ay, gun, resolved_date = self.tarih_ayristir(ilk_kayit, fallback_date_str)
        dosya_adi = self.dosya_adi_olustur(gercek_id)

        if yil != "tarihsiz" and ay and gun:
            hedef_klasor = os.path.join(self.root_output_dir, yil, ay, gun)
        else:
            hedef_klasor = os.path.join(self.root_output_dir, "tarihsiz")

        os.makedirs(hedef_klasor, exist_ok=True)
        hedef_dosya_yolu = os.path.join(hedef_klasor, dosya_adi)
        gecici_dosya_yolu = f"{hedef_dosya_yolu}.tmp"

        with open(gecici_dosya_yolu, "w", encoding="utf-8") as f:
            json.dump(detay_yanit, f, ensure_ascii=False, indent=4)
        os.replace(gecici_dosya_yolu, hedef_dosya_yolu)

        rel_path = os.path.relpath(hedef_dosya_yolu, self.root_output_dir).replace("\\", "/")
        self.record_decision_to_db(gercek_id, dosya_adi, resolved_date, yil, ay, gun, rel_path)
        if str(raw_id) != gercek_id:
            self.downloaded_ids.add(str(raw_id))
            self.remove_failed_decision(str(raw_id))
        self.remove_failed_decision(gercek_id)

        return rel_path, dosya_adi

    def arsivle(self, baslangic_tarihi_str: Optional[str] = None, bitis_tarihi_str: Optional[str] = None, max_karar: Optional[int] = None):
        if not baslangic_tarihi_str:
            baslangic_tarihi_str = datetime.now().strftime("%Y-%m-%d")

        if not bitis_tarihi_str:
            bitis_tarihi_str = "1962-01-01"

        current_date = datetime.strptime(baslangic_tarihi_str, "%Y-%m-%d")
        end_date = datetime.strptime(bitis_tarihi_str, "%Y-%m-%d")

        if current_date < end_date:
            current_date, end_date = end_date, current_date

        logger.info("=" * 70)
        logger.info("Anayasa Mahkemesi Karar İndirici Başlatıldı")
        logger.info(f"Tarih Aralığı: {current_date.strftime('%Y-%m-%d')} -> {end_date.strftime('%Y-%m-%d')}")
        logger.info(f"Hedef Arşiv: {self.root_output_dir}")
        logger.info(f"Hata Dosyası: {self.hata_json_path} | ID Listesi: {self.idlist_path}")
        logger.info("=" * 70)

        toplam_indirilen = 0
        total_days = (current_date - end_date).days + 1
        day_index = 0
        tarih_iso = current_date.strftime("%Y-%m-%d")
        self.aktif_tarih = tarih_iso

        try:
            while current_date >= end_date:
                day_index += 1
                tarih_iso = current_date.strftime("%Y-%m-%d")
                self.aktif_tarih = tarih_iso

                if self.is_day_completed(tarih_iso):
                    logger.info(f"[{day_index}/{total_days}] {tarih_iso} daha önce tamamlanmış, atlanıyor.")
                    current_date -= timedelta(days=1)
                    continue

                karar_listesi, sayfa_hatasi_oldu, expected_total = self.gunun_kararlarini_getir(tarih_iso)
                karar_sayisi = len(karar_listesi)
                day_success = not sayfa_hatasi_oldu
                gunluk_indirilen = 0
                gunluk_islenen = 0

                if karar_sayisi == 0 and not sayfa_hatasi_oldu and expected_total == 0:
                    self.mark_day_completed(tarih_iso, 0)
                    current_date -= timedelta(days=1)
                    continue

                if karar_sayisi > 0:
                    logger.info(f"[{day_index}/{total_days}] Tarih: {tarih_iso} | Bulunan Karar: {karar_sayisi} (API Toplamı: {expected_total})")

                    for karar in karar_listesi:
                        k_id = str(karar.get("id") or "")
                        if not k_id:
                            continue
                        k_tip = karar.get("kararTipi")
                        dosya_adi = self.dosya_adi_olustur(k_id)
                        clean_id = self.temizle_id_degeri(k_id)

                        if k_id in self.downloaded_ids or clean_id in self.downloaded_ids or dosya_adi in self.mevcut_dosyalar:
                            gunluk_islenen += 1
                            self.remove_failed_decision(k_id)
                            continue

                        detay_ok, detay_yanit, detay_err = self.karar_detay_getir(k_id, k_tip)
                        if not detay_ok or not detay_yanit:
                            logger.error(
                                f"[HATA] Karar detayı alınamadı (ID: {k_id}, Tarih: {tarih_iso}) | "
                                f"Hata: {detay_err.get('error_msg')} | HTTP: {detay_err.get('status_code')} | "
                                f"Yanıt: {detay_err.get('response_text')}"
                            )
                            self.record_failed_decision(k_id, tarih_iso, detay_err, karar_obj=karar, karar_tipi=k_tip)
                            day_success = False
                            continue

                        rel_path, kaydedilen_dosya = self.save_decision_file(
                            detay_yanit,
                            raw_id=k_id,
                            fallback_date_str=tarih_iso,
                            karar_obj=karar
                        )
                        toplam_indirilen += 1
                        gunluk_indirilen += 1
                        gunluk_islenen += 1

                        logger.info(f"  [+] Kaydedildi ({gunluk_islenen}/{expected_total or karar_sayisi}): {tarih_iso} | ID: {k_id} -> {rel_path}")

                        if max_karar and toplam_indirilen >= max_karar:
                            logger.info(f"[BİLGİ] Belirtilen indirme limitine ({max_karar}) ulaşıldı.")
                            if day_success and expected_total > 0 and gunluk_islenen >= expected_total:
                                self.mark_day_completed(tarih_iso, expected_total)
                            self.ilerleme_ozeti_kaydet(tarih_iso)
                            return

                        if self.delay > 0:
                            time.sleep(self.delay)
                        else:
                            time.sleep(random.uniform(0.05, 0.15))

                if expected_total > 0 and gunluk_islenen < expected_total:
                    day_success = False
                    neden = f"API toplam {expected_total} karar bildirdi ancak {gunluk_islenen} karar işlenebildi."
                    logger.warning(f"[UYARI] {tarih_iso}: {neden}")
                    self.record_incomplete_day(tarih_iso, neden, expected_total, gunluk_islenen)
                elif sayfa_hatasi_oldu:
                    neden = "Arama sayfalarından en az biri alınamadı."
                    logger.warning(f"[UYARI] {tarih_iso}: {neden}")
                    self.record_incomplete_day(tarih_iso, neden, expected_total, gunluk_islenen)
                elif not day_success:
                    neden = "Bazı kararların detayı indirilemedi."
                    logger.warning(f"[UYARI] {tarih_iso}: {neden}")
                    self.record_incomplete_day(tarih_iso, neden, expected_total, gunluk_islenen)

                if day_success:
                    self.mark_day_completed(tarih_iso, expected_total or gunluk_islenen)
                    if gunluk_indirilen > 0:
                        logger.info(f"Tamamlandı: {tarih_iso} tarihinden {gunluk_indirilen} yeni karar kaydedildi.")

                self.ilerleme_ozeti_kaydet(tarih_iso)
                current_date -= timedelta(days=1)
                time.sleep(0.05)

            logger.info("=" * 70)
            logger.info(f"[BİLGİ] Arşivleme tamamlandı. Toplam yeni indirilen karar: {toplam_indirilen}")
            logger.info("=" * 70)
        except KeyboardInterrupt:
            logger.warning("[UYARI] Kullanıcı tarafından durduruldu (Ctrl+C). İlerleme durumu kaydediliyor...")
            self.ilerleme_ozeti_kaydet(self.aktif_tarih or tarih_iso)
            logger.info(f"[*] İlerleme '{self.ilerleme_dosyasi}' dosyasına kaydedildi.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Anayasa Mahkemesi Karar İndirici")
    parser.add_argument("--output", "--dir", dest="output", default="aymjson", help="Çıktı klasörü (varsayılan: aymjson)")
    parser.add_argument("--start", default=None, help="En eski tarih (YYYY-MM-DD, varsayılan: 1962-01-01)")
    parser.add_argument("--end", default=None, help="Başlangıç/en yeni tarih (YYYY-MM-DD, varsayılan: Bugün)")
    parser.add_argument("--limit", type=int, default=None, help="Maksimum indirilecek karar sayısı")
    parser.add_argument("--delay", type=float, default=0.1, help="İstekler arası bekleme süresi (saniye, varsayılan: 0.1)")
    parser.add_argument("--page-size", type=int, default=50, help="Sayfa başına kayıt sayısı (varsayılan: 50)")

    args = parser.parse_args()

    bot = AnayasaMahkemesiScraper(
        root_output_dir=args.output,
        delay=args.delay,
        page_size=args.page_size
    )
    try:
        bot.arsivle(
            baslangic_tarihi_str=args.end,
            bitis_tarihi_str=args.start,
            max_karar=args.limit
        )
    except KeyboardInterrupt:
        bot.ilerleme_ozeti_kaydet(bot.aktif_tarih or datetime.now().strftime("%Y-%m-%d"))
        sys.exit(0)
