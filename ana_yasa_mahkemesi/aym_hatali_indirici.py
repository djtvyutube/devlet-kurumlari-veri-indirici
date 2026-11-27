"""
Anayasa Mahkemesi Hatalı / Eksik Karar ve Sayfa İndirici

hata.json ve archive_state.db içindeki hatali_kararlar ile hatali_sayfalar
(ve varsa eski ilerleme_durumu.json içindeki hatali_idler) kayıtlarını yeniden sorgular.
İnen kararları Yıl/Ay/Gün (veya tarihsiz/) klasörüne kaydedip idlist.json'a ekler ve
hata kayıtlarından düşer. Yine başarısız olanların hata nedenini, HTTP kodunu ve
deneme sayısını günceller.
"""

import argparse
import os
import sys
import time
import json
import random
import sqlite3
import logging
from typing import Optional, Dict, Any, List
from aym_indirici import AnayasaMahkemesiScraper

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("AYMHatali")


class AnayasaMahkemesiHataliIndirici(AnayasaMahkemesiScraper):
    def __init__(self, root_output_dir: str = "aymjson", delay: float = 0.15, page_size: int = 50):
        super().__init__(root_output_dir=root_output_dir, delay=delay, page_size=page_size)

    def _collect_failed_decisions(self, include_permanent: bool = False) -> List[Dict[str, Any]]:
        records_by_id: Dict[str, Dict[str, Any]] = {}
        skipped_perm = 0

        if os.path.exists(self.db_path):
            with sqlite3.connect(self.db_path) as conn:
                c = conn.cursor()
                c.execute("""
                    SELECT id, karar_tarihi, karar_tipi, arama_kunyesi, http_kodu, kalici_hata, deneme_sayisi
                    FROM hatali_kararlar
                    ORDER BY deneme_sayisi ASC
                """)
                for dec_id, k_tarihi, k_tip, kunye_str, http_kodu, kalici_hata, deneme_sayisi in c.fetchall():
                    is_perm = bool(kalici_hata) or (http_kodu == 404)
                    if is_perm and not include_permanent:
                        skipped_perm += 1
                        continue
                    karar_obj = None
                    if kunye_str:
                        try:
                            karar_obj = json.loads(kunye_str)
                        except Exception:
                            karar_obj = None
                    records_by_id[str(dec_id)] = {
                        "id": str(dec_id),
                        "karar_tarihi": k_tarihi or "",
                        "karar_tipi": k_tip,
                        "arama_kunyesi": karar_obj,
                        "deneme_sayisi": deneme_sayisi or 1
                    }

        hata_data = self._load_hata_json()
        for dec_id, info in hata_data.get("hatali_kararlar", {}).items():
            str_id = str(dec_id)
            if str_id in records_by_id:
                if not records_by_id[str_id].get("arama_kunyesi") and info.get("arama_kunyesi"):
                    records_by_id[str_id]["arama_kunyesi"] = info.get("arama_kunyesi")
                continue
            http_kodu = info.get("http_kodu")
            is_perm = bool(info.get("kalici_hata")) or (http_kodu == 404)
            if is_perm and not include_permanent:
                skipped_perm += 1
                continue
            records_by_id[str_id] = {
                "id": str_id,
                "karar_tarihi": info.get("karar_tarihi") or "",
                "karar_tipi": info.get("karar_tipi"),
                "arama_kunyesi": info.get("arama_kunyesi"),
                "deneme_sayisi": info.get("deneme_sayisi", 1)
            }

        if skipped_perm > 0:
            logger.info(
                f"Kalıcı hata (HTTP 404 veya total:0) olarak işaretlenmiş {skipped_perm} adet karar atlandı "
                f"(Tekrar denemek için --include-permanent parametresini kullanabilirsiniz)."
            )

        return list(records_by_id.values())

    def _collect_failed_pages(self) -> List[Dict[str, Any]]:
        pages_by_key: Dict[str, Dict[str, Any]] = {}

        if os.path.exists(self.db_path):
            with sqlite3.connect(self.db_path) as conn:
                c = conn.cursor()
                c.execute("SELECT tarih, sayfa, deneme_sayisi FROM hatali_sayfalar ORDER BY tarih DESC, sayfa ASC")
                for tarih, sayfa, deneme_sayisi in c.fetchall():
                    key = f"{tarih}_sayfa_{sayfa}"
                    pages_by_key[key] = {"tarih": tarih, "sayfa": int(sayfa), "deneme_sayisi": deneme_sayisi or 1}

        hata_data = self._load_hata_json()
        for key, info in hata_data.get("hatali_sayfalar", {}).items():
            tarih = info.get("tarih")
            sayfa = info.get("sayfa")
            if tarih and sayfa is not None:
                k = f"{tarih}_sayfa_{int(sayfa)}"
                if k not in pages_by_key:
                    pages_by_key[k] = {
                        "tarih": tarih,
                        "sayfa": int(sayfa),
                        "deneme_sayisi": info.get("deneme_sayisi", 1)
                    }

        return list(pages_by_key.values())

    def retry_failed_pages(self, max_karar: Optional[int] = None, current_saved: int = 0) -> int:
        failed_pages = self._collect_failed_pages()
        if not failed_pages:
            logger.info("Hatalı arama sayfası kaydı bulunmuyor.")
            return 0

        logger.info(f"Toplam {len(failed_pages)} adet hatalı arama sayfası yeniden deneniyor...")
        saved_from_pages = 0

        for idx, item in enumerate(failed_pages, start=1):
            tarih_iso = item["tarih"]
            page = item["sayfa"]
            onceki_deneme = item.get("deneme_sayisi", 1)

            logger.info(f"[Sayfa {idx}/{len(failed_pages)}] Sorgulanıyor: {tarih_iso} - Sayfa {page} (Önceki deneme: {onceki_deneme})")
            payload = {
                "kararTarihiStart": tarih_iso,
                "kararTarihiEnd": tarih_iso,
                "page": page,
                "size": self.page_size
            }
            ok, sonuc, page_err = self.api_istek_gonder(payload, max_deneme=5)
            if not ok or not sonuc:
                logger.error(
                    f"[HATA] {tarih_iso} - Sayfa {page} yine alınamadı (Deneme #{onceki_deneme + 1})! "
                    f"Hata: {page_err.get('error_msg')} | HTTP: {page_err.get('status_code')} | "
                    f"Yanıt: {page_err.get('response_text')}"
                )
                self.record_failed_search_page(tarih_iso, page, page_err)
                continue

            self.remove_failed_search_page(tarih_iso, page)
            data_list = sonuc.get("data", [])
            total = sonuc.get("total", 0)
            logger.info(f"  [+] {tarih_iso} - Sayfa {page} alındı ({len(data_list)} karar, Gün toplamı: {total}).")

            if page == 1 and total > self.page_size:
                total_pages = (total + self.page_size - 1) // self.page_size
                existing_keys = {(p["tarih"], p["sayfa"]) for p in failed_pages}
                for next_p in range(2, total_pages + 1):
                    if (tarih_iso, next_p) not in existing_keys:
                        failed_pages.append({"tarih": tarih_iso, "sayfa": next_p, "deneme_sayisi": 0})
                        existing_keys.add((tarih_iso, next_p))

            for karar in data_list:
                k_id = str(karar.get("id") or "")
                if not k_id:
                    continue
                k_tip = karar.get("kararTipi")
                dosya_adi = self.dosya_adi_olustur(k_id)
                clean_id = self.temizle_id_degeri(k_id)

                if k_id in self.downloaded_ids or clean_id in self.downloaded_ids or dosya_adi in self.mevcut_dosyalar:
                    self.remove_failed_decision(k_id)
                    continue

                detay_ok, detay_yanit, detay_err = self.karar_detay_getir(k_id, k_tip)
                if not detay_ok or not detay_yanit:
                    logger.error(
                        f"  [HATA] Karar detayı alınamadı (ID: {k_id}) | "
                        f"Hata: {detay_err.get('error_msg')} | HTTP: {detay_err.get('status_code')} | "
                        f"Yanıt: {detay_err.get('response_text')}"
                    )
                    self.record_failed_decision(k_id, tarih_iso, detay_err, karar_obj=karar, karar_tipi=k_tip)
                    continue

                rel_path, _ = self.save_decision_file(
                    detay_yanit,
                    raw_id=k_id,
                    fallback_date_str=tarih_iso,
                    karar_obj=karar
                )
                saved_from_pages += 1
                logger.info(f"  [+] Kurtarıldı (Sayfadan): ID {k_id} -> {rel_path}")

                if max_karar and (current_saved + saved_from_pages) >= max_karar:
                    logger.info(f"Belirtilen indirme limitine ({max_karar}) ulaşıldı.")
                    return saved_from_pages

                if self.delay > 0:
                    time.sleep(self.delay)

        return saved_from_pages

    def _reconcile_incomplete_days(self):
        hata_data = self._load_hata_json()
        eksik_gunler = dict(hata_data.get("eksik_gunler", {}))
        if not eksik_gunler:
            return

        kalan_sayfa_tarihleri = {
            info.get("tarih") for info in hata_data.get("hatali_sayfalar", {}).values() if isinstance(info, dict)
        }
        kalan_karar_tarihleri = {
            info.get("karar_tarihi") for info in hata_data.get("hatali_kararlar", {}).values() if isinstance(info, dict)
        }

        if os.path.exists(self.db_path):
            with sqlite3.connect(self.db_path) as conn:
                c = conn.cursor()
                c.execute("SELECT DISTINCT tarih FROM hatali_sayfalar")
                kalan_sayfa_tarihleri.update(r[0] for r in c.fetchall() if r[0])
                c.execute("SELECT DISTINCT karar_tarihi FROM hatali_kararlar WHERE kalici_hata = 0")
                kalan_karar_tarihleri.update(r[0] for r in c.fetchall() if r[0])

        for tarih_str, gun_info in eksik_gunler.items():
            if tarih_str not in kalan_sayfa_tarihleri and tarih_str not in kalan_karar_tarihleri:
                beklenen = gun_info.get("beklenen_toplam") or gun_info.get("islenen_karar") or 0
                if beklenen > 0:
                    self.mark_day_completed(tarih_str, beklenen)
                    logger.info(f"  [+] Eksik gün tüm hataları giderildiği için tamamlandı işaretlendi: {tarih_str}")

    def hatali_kararlari_indir(self, max_karar: Optional[int] = None, include_permanent: bool = False):
        logger.info("=" * 70)
        logger.info("Anayasa Mahkemesi Hatalı Karar ve Sayfa İndirici Başlatıldı")
        logger.info(f"Hedef Arşiv: {self.root_output_dir}")
        logger.info(f"Hata Dosyası: {self.hata_json_path} | ID Listesi: {self.idlist_path}")
        logger.info("=" * 70)

        kurtarilan_sayisi = self.retry_failed_pages(max_karar=max_karar, current_saved=0)
        if max_karar and kurtarilan_sayisi >= max_karar:
            return

        aranacak_kayitlar = self._collect_failed_decisions(include_permanent=include_permanent)
        if not aranacak_kayitlar:
            self._reconcile_incomplete_days()
            logger.info("[BİLGİ] Yeniden denenecek hatalı/eksik karar ID'si bulunmuyor.")
            return

        if max_karar:
            kalan = max(0, max_karar - kurtarilan_sayisi)
            aranacak_kayitlar = aranacak_kayitlar[:kalan]

        toplam_hatali = len(aranacak_kayitlar)
        logger.info(f"[*] Toplam {toplam_hatali} adet hatalı/eksik karar ID'si yeniden indiriliyor...")

        hala_hatali_sayisi = 0

        try:
            for sira, item in enumerate(aranacak_kayitlar, start=1):
                k_id = str(item["id"])
                k_tarihi = item.get("karar_tarihi") or ""
                k_tip = item.get("karar_tipi")
                karar_obj = item.get("arama_kunyesi")
                onceki_deneme = item.get("deneme_sayisi", 1)

                dosya_adi = self.dosya_adi_olustur(k_id)
                clean_id = self.temizle_id_degeri(k_id)
                if k_id in self.downloaded_ids or clean_id in self.downloaded_ids or dosya_adi in self.mevcut_dosyalar:
                    self.remove_failed_decision(k_id)
                    logger.info(f"[{sira}/{toplam_hatali}] ID {k_id} zaten arşivde mevcut, hata listesinden silindi.")
                    continue

                logger.info(
                    f"[{sira}/{toplam_hatali}] Sorgulanıyor: ID {k_id} "
                    f"(Tarih: {k_tarihi or 'tarihsiz'}, Önceki deneme: {onceki_deneme})"
                )
                detay_ok, detay_yanit, detay_err = self.karar_detay_getir(k_id, k_tip)

                if not detay_ok or not detay_yanit:
                    hala_hatali_sayisi += 1
                    logger.error(
                        f"  [-] Başarısız (Deneme #{onceki_deneme + 1}): ID {k_id} yine alınamadı! "
                        f"Hata: {detay_err.get('error_msg')} | HTTP: {detay_err.get('status_code')} | "
                        f"Yanıt: {detay_err.get('response_text')}"
                    )
                    self.record_failed_decision(k_id, k_tarihi, detay_err, karar_obj=karar_obj, karar_tipi=k_tip)
                    continue

                rel_path, _ = self.save_decision_file(
                    detay_yanit,
                    raw_id=k_id,
                    fallback_date_str=k_tarihi,
                    karar_obj=karar_obj
                )
                kurtarilan_sayisi += 1
                logger.info(f"  [+] Kurtarıldı: ID {k_id} -> {rel_path}")

                if self.delay > 0:
                    time.sleep(self.delay)
                else:
                    time.sleep(random.uniform(0.1, 0.25))

            self._reconcile_incomplete_days()
            logger.info("=" * 70)
            logger.info(
                f"[BİLGİ] İşlem tamamlandı. Kurtarılan: {kurtarilan_sayisi} | "
                f"Hala Hatalı: {hala_hatali_sayisi}"
            )
            logger.info("=" * 70)
        except KeyboardInterrupt:
            logger.warning("[UYARI] Kullanıcı tarafından durduruldu (Ctrl+C).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Anayasa Mahkemesi Hatalı / Eksik Karar İndirici")
    parser.add_argument("--output", "--dir", dest="output", default="aymjson", help="Çıktı klasörü (varsayılan: aymjson)")
    parser.add_argument("--limit", type=int, default=None, help="Maksimum denenecek hatalı karar sayısı")
    parser.add_argument("--delay", type=float, default=0.15, help="İstekler arası bekleme süresi (saniye, varsayılan: 0.15)")
    parser.add_argument("--page-size", type=int, default=50, help="Sayfa başına kayıt sayısı (varsayılan: 50)")
    parser.add_argument("--include-permanent", action="store_true", help="Kalıcı hata (HTTP 404 veya total:0) almış kararları da yeniden dene")

    args = parser.parse_args()

    kurtarici = AnayasaMahkemesiHataliIndirici(
        root_output_dir=args.output,
        delay=args.delay,
        page_size=args.page_size
    )
    try:
        kurtarici.hatali_kararlari_indir(max_karar=args.limit, include_permanent=args.include_permanent)
    except KeyboardInterrupt:
        sys.exit(0)
