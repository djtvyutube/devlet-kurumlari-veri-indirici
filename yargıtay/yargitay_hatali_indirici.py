"""
Yargıtay Hatalı / Eksik Karar ve Sayfa İndirici

hata.json ve archive_state.db içindeki hatali_kararlar ile hatali_sayfalar
kayıtlarını yeniden sorgular; başarılı olanları Yıl/Ay/Gün (veya tarihsiz/)
klasörüne kaydedip idlist.json'a ekler ve hata kayıtlarından düşer.
Kalıcı hatalar (HTTP 404) varsayılan olarak atlanır (--include-404 ile zorlanabilir).
"""

import os
import sys
import time
import json
import sqlite3
import logging
import argparse
from typing import Optional, Dict, Any, List
from yargitay_indirici import YargitayScraper2

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("YargitayHatali")


class YargitayHataliIndirici(YargitayScraper2):
    def __init__(self, base_dir: str = "yargitay_arsiv", delay: float = 0.25, page_size: int = 100):
        super().__init__(base_dir=base_dir, delay=delay, page_size=page_size)

    def _collect_failed_decisions(self, include_404: bool = False) -> List[Dict[str, Any]]:
        records_by_id: Dict[str, Dict[str, Any]] = {}
        skipped_404 = 0

        if os.path.exists(self.db_path):
            with sqlite3.connect(self.db_path) as conn:
                c = conn.cursor()
                c.execute("""
                    SELECT id, karar_tarihi, arama_kunyesi, http_kodu, kalici_hata, deneme_sayisi
                    FROM hatali_kararlar
                    ORDER BY deneme_sayisi ASC
                """)
                for dec_id, k_tarihi, kunye_str, http_kodu, kalici_hata, deneme_sayisi in c.fetchall():
                    is_perm = bool(kalici_hata) or (http_kodu == 404)
                    if is_perm and not include_404:
                        skipped_404 += 1
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
                        "arama_kunyesi": karar_obj,
                        "http_kodu": http_kodu,
                        "kalici_hata": is_perm,
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
            if is_perm and not include_404:
                skipped_404 += 1
                continue
            records_by_id[str_id] = {
                "id": str_id,
                "karar_tarihi": info.get("karar_tarihi") or "",
                "arama_kunyesi": info.get("arama_kunyesi"),
                "http_kodu": http_kodu,
                "kalici_hata": is_perm,
                "deneme_sayisi": info.get("deneme_sayisi", 1)
            }

        if skipped_404 > 0:
            logger.info(
                f"Kalıcı hata (HTTP 404) olarak işaretlenmiş {skipped_404} adet karar atlandı "
                f"(Tekrar denemek için --include-404 parametresini kullanabilirsiniz)."
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
            date_str = item["tarih"]
            page = item["sayfa"]
            onceki_deneme = item.get("deneme_sayisi", 1)

            logger.info(f"[Sayfa {idx}/{len(failed_pages)}] Yeniden sorgulanıyor: {date_str} - Sayfa {page} (Önceki deneme: {onceki_deneme})")
            ok, page_data, page_err = self.fetch_search_page(date_str, page)
            if not ok or not page_data or "content" not in page_data:
                logger.error(
                    f"[HATA] {date_str} - Sayfa {page} yine alınamadı (Deneme #{onceki_deneme + 1})! "
                    f"Hata: {page_err.get('error_msg')} | HTTP: {page_err.get('status_code')} | "
                    f"Yanıt: {page_err.get('response_text')}"
                )
                self.record_failed_search_page(date_str, page, page_err)
                continue

            self.remove_failed_search_page(date_str, page)
            items = page_data.get("content", [])
            total_pages = page_data.get("totalPages", 0)
            logger.info(f"  [+] {date_str} - Sayfa {page} alındı ({len(items)} karar, Toplam sayfa: {total_pages}).")

            if page == 0 and total_pages > 1:
                existing_keys = {(p["tarih"], p["sayfa"]) for p in failed_pages}
                for next_p in range(1, total_pages):
                    if (date_str, next_p) not in existing_keys:
                        failed_pages.append({"tarih": date_str, "sayfa": next_p, "deneme_sayisi": 0})
                        existing_keys.add((date_str, next_p))

            for karar in items:
                dec_id = str(karar.get("id") or "")
                if not dec_id:
                    continue
                if dec_id in self.downloaded_ids:
                    self.remove_failed_decision(dec_id)
                    continue

                detay_ok, detail, detay_err = self.fetch_decision_detail(dec_id)
                if not detay_ok or detail is None:
                    logger.error(
                        f"  [HATA] Karar detayı çekilemedi (ID: {dec_id}) | "
                        f"Hata: {detay_err.get('error_msg')} | HTTP: {detay_err.get('status_code')} | "
                        f"Yanıt: {detay_err.get('response_text')}"
                    )
                    self.record_failed_decision(dec_id, date_str, detay_err, karar_obj=karar)
                    continue

                save_res = self.save_decision(detail, date_str, fallback_id=dec_id)
                if save_res:
                    saved_path, karar_tarihi, y, m, d = save_res
                    self.downloaded_ids.add(dec_id)
                    self._record_decision_to_db(dec_id, karar_tarihi, y, m, d, saved_path)
                    self.remove_failed_decision(dec_id)
                    saved_from_pages += 1
                    logger.info(f"  [+] Kurtarıldı (Sayfadan): ID {dec_id} -> {saved_path}")

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

    def retry_failed_decisions(self, max_karar: Optional[int] = None, include_404: bool = False):
        logger.info("=" * 60)
        logger.info("Yargıtay Hatalı Karar ve Sayfa İndirici Başlatıldı")
        logger.info(f"Hedef Arşiv: {self.base_dir}")
        logger.info(f"Hata Dosyası: {self.hata_json_path} | ID Listesi: {self.idlist_path}")
        logger.info("=" * 60)

        total_recovered = self.retry_failed_pages(max_karar=max_karar, current_saved=0)
        if max_karar and total_recovered >= max_karar:
            return

        failed_decisions = self._collect_failed_decisions(include_404=include_404)
        if not failed_decisions:
            self._reconcile_incomplete_days()
            logger.info("Yeniden denenecek hatalı karar ID kaydı bulunmuyor.")
            return

        if max_karar:
            remaining = max(0, max_karar - total_recovered)
            failed_decisions = failed_decisions[:remaining]

        total_failed = len(failed_decisions)
        logger.info(f"Toplam {total_failed} adet hatalı karar ID'si yeniden deneniyor...")

        still_failed = 0
        for idx, item in enumerate(failed_decisions, start=1):
            dec_id = str(item["id"])
            date_str = item.get("karar_tarihi") or ""
            karar_obj = item.get("arama_kunyesi")
            onceki_deneme = item.get("deneme_sayisi", 1)

            if dec_id in self.downloaded_ids:
                self.remove_failed_decision(dec_id)
                logger.info(f"[{idx}/{total_failed}] ID {dec_id} zaten arşivde mevcut, hata listesinden silindi.")
                continue

            logger.info(f"[{idx}/{total_failed}] Sorgulanıyor: ID {dec_id} (Tarih: {date_str or 'tarihsiz'}, Önceki deneme: {onceki_deneme})")
            ok, detail, err_info = self.fetch_decision_detail(dec_id)
            if not ok or detail is None:
                still_failed += 1
                logger.error(
                    f"  [HATA] ID {dec_id} yine alınamadı (Deneme #{onceki_deneme + 1})! "
                    f"Hata: {err_info.get('error_msg')} | HTTP: {err_info.get('status_code')} | "
                    f"Yanıt: {err_info.get('response_text')}"
                )
                self.record_failed_decision(dec_id, date_str, err_info, karar_obj=karar_obj)
                continue

            save_res = self.save_decision(detail, date_str, fallback_id=dec_id)
            if save_res:
                saved_path, karar_tarihi, y, m, d = save_res
                self.downloaded_ids.add(dec_id)
                self._record_decision_to_db(dec_id, karar_tarihi, y, m, d, saved_path)
                self.remove_failed_decision(dec_id)
                total_recovered += 1
                logger.info(f"  [+] Kurtarıldı: ID {dec_id} -> {saved_path}")

            if self.delay > 0:
                time.sleep(self.delay)

        self._reconcile_incomplete_days()
        logger.info("=" * 60)
        logger.info(
            f"İşlem tamamlandı. Kurtarılan Karar: {total_recovered} | "
            f"Hala Hatalı Karar: {still_failed}"
        )
        logger.info("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Yargıtay Hatalı / Eksik Karar İndirici")
    parser.add_argument("--output", "--dir", dest="output", default="yargitay_arsiv", help="Çıktı klasörü (varsayılan: yargitay_arsiv)")
    parser.add_argument("--limit", type=int, default=None, help="Maksimum denenecek/indirilecek hatalı karar sayısı")
    parser.add_argument("--delay", type=float, default=0.25, help="İstekler arası gecikme (saniye, varsayılan: 0.25)")
    parser.add_argument("--page-size", type=int, default=100, help="Sayfa başına kayıt (varsayılan: 100)")
    parser.add_argument("--include-404", action="store_true", help="HTTP 404 (kalıcı hata) almış kararları da yeniden dene")

    args = parser.parse_args()

    retry_scraper = YargitayHataliIndirici(
        base_dir=args.output,
        delay=args.delay,
        page_size=args.page_size
    )
    try:
        retry_scraper.retry_failed_decisions(max_karar=args.limit, include_404=args.include_404)
    except KeyboardInterrupt:
        logger.info("\nKullanıcı tarafından durduruldu.")


if __name__ == "__main__":
    main()
