"""
Anayasa Mahkemesi Hatalı / Eksik Karar İndirici

ilerleme_durumu.json içindeki hatali_idler listesinde kalan kararları
yeniden sorgular ve inenleri ilgili Yıl/Ay/Gün klasörüne ham JSON olarak kaydeder.
"""

import argparse
import os
import sys
import time
import json
import random
import re
import base64
from datetime import datetime, timezone
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class AnayasaMahkemesiHataliIndirici:
    def __init__(self, root_output_dir="aymjson", delay=0.15):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        if not os.path.isabs(root_output_dir):
            self.root_output_dir = os.path.abspath(os.path.join(script_dir, root_output_dir))
        else:
            self.root_output_dir = os.path.abspath(root_output_dir)

        self.delay = delay
        self.ilerleme_dosyasi = os.path.join(self.root_output_dir, "ilerleme_durumu.json")
        self.api_url = "https://kararlarbilgibankasi.anayasa.gov.tr/api/core/public/search"

        self.session = self.olustur_oturum()
        self.mevcut_dosyalar = set()
        self.ilerleme_verisi = {}
        self.basarili_idler = []
        self._basarili_set = set()
        self.hatali_idler = []
        self._hatali_set = set()

        self.dosyalar_onbellekle()

    def olustur_oturum(self):
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
            total=5,
            backoff_factor=1.5,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["POST", "GET"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=20, pool_maxsize=20)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    def log_yaz(self, mesaj):
        zaman = time.strftime("%H:%M:%S")
        print(f"[{zaman}] {mesaj}")

    def dosyalar_onbellekle(self):
        if os.path.exists(self.root_output_dir):
            for root, dirs, files in os.walk(self.root_output_dir):
                for file in files:
                    if file.endswith(".json") and file != "ilerleme_durumu.json":
                        self.mevcut_dosyalar.add(file)

    def ilerleme_oku(self):
        if not os.path.exists(self.ilerleme_dosyasi):
            self.log_yaz(f"[HATA] İlerleme dosyası bulunamadı: {self.ilerleme_dosyasi}")
            return False

        try:
            with open(self.ilerleme_dosyasi, "r", encoding="utf-8") as f:
                self.ilerleme_verisi = json.load(f)

            kayitli_basarililar = self.ilerleme_verisi.get("basarili_idler", [])
            if isinstance(kayitli_basarililar, list):
                self.basarili_idler = kayitli_basarililar
                self._basarili_set = set(kayitli_basarililar)

            kayitli_hatalar = self.ilerleme_verisi.get("hatali_idler", [])
            if isinstance(kayitli_hatalar, list):
                self.hatali_idler = list(kayitli_hatalar)
                self._hatali_set = set(kayitli_hatalar)

            return True
        except Exception as e:
            self.log_yaz(f"[HATA] İlerleme dosyası okunamadı: {e}")
            return False

    def ilerleme_yukle(self):
        return self.ilerleme_oku()

    def ilerleme_kaydet(self):
        try:
            son_tarih = self.ilerleme_verisi.get("son_tarih")
            guncel_veri = {
                "son_tarih": son_tarih,
                "toplam_onbellek_dosya": len(self.mevcut_dosyalar),
                "basarili_id_sayisi": len(self.basarili_idler),
                "basarili_idler": self.basarili_idler,
                "hatali_id_sayisi": len(self.hatali_idler),
                "hatali_idler": self.hatali_idler,
                "guncelleme_zamani_utc": datetime.now(timezone.utc).isoformat()
            }
            gecici_dosya = f"{self.ilerleme_dosyasi}.tmp"
            with open(gecici_dosya, "w", encoding="utf-8") as f:
                json.dump(guncel_veri, f, ensure_ascii=False, indent=4)
            os.replace(gecici_dosya, self.ilerleme_dosyasi)
        except Exception as e:
            self.log_yaz(f"[HATA] İlerleme dosyası güncellenemedi: {e}")

    def temizle_id_degeri(self, raw_id):
        if not raw_id:
            return ""
        str_id = str(raw_id).strip()

        if str_id.startswith("a2Ji") or str_id.startswith("kbb:"):
            try:
                padding = 4 - (len(str_id) % 4)
                if padding < 4:
                    str_id_padded = str_id + ("=" * padding)
                else:
                    str_id_padded = str_id

                decoded = base64.b64decode(str_id_padded).decode("utf-8", errors="ignore")
                if ":" in decoded:
                    parts = decoded.split(":")
                    if len(parts) > 1 and parts[1]:
                        str_id = parts[1]
                else:
                    str_id = decoded
            except Exception:
                pass

        temiz = re.sub(r'[\\/*?:"<>| ]', "_", str_id)
        return temiz

    def dosya_adi_olustur(self, kimlik_degeri):
        temiz = self.temizle_id_degeri(kimlik_degeri)
        return f"karar_{temiz}.json"

    def tarih_ayristir(self, item, raw_id):
        olasi_alanlar = ["kararTarihi", "kararTarih", "basvuruTarihi", "resmiGazeteTarihi", "tarih"]
        tarih_degeri = None

        if isinstance(item, dict):
            for alan in olasi_alanlar:
                deger = item.get(alan)
                if deger and isinstance(deger, str):
                    tarih_degeri = deger.strip()
                    break

        if tarih_degeri:
            iso_eslesme = re.match(r"^(\d{4})-(\d{2})-(\d{2})", tarih_degeri)
            if iso_eslesme:
                return iso_eslesme.group(1), iso_eslesme.group(2), iso_eslesme.group(3)

            tr_eslesme = re.match(r"^(\d{2})[./](\d{2})[./](\d{4})", tarih_degeri)
            if tr_eslesme:
                return tr_eslesme.group(3), tr_eslesme.group(2), tr_eslesme.group(1)

        return "kurtarilan_kararlar", "tarihsiz", self.temizle_id_degeri(raw_id)[:2] or "00"

    def api_istek_gonder(self, payload, max_deneme=5):
        deneme = 0
        while deneme < max_deneme:
            deneme += 1
            try:
                response = self.session.post(self.api_url, json=payload, timeout=20)
                if response.status_code == 200:
                    return response.json()
                elif response.status_code == 429:
                    bekleme = 15 * deneme
                    self.log_yaz(f"[UYARI] İstek sınırı (429) aşıldı. {bekleme} saniye bekleniyor... (Deneme {deneme}/{max_deneme})")
                    time.sleep(bekleme)
                else:
                    self.log_yaz(f"[UYARI] Sunucu {response.status_code} yanıtı döndü. 5 saniye bekleniyor...")
                    time.sleep(5)
            except requests.exceptions.RequestException as e:
                self.log_yaz(f"[HATA] Bağlantı hatası: {e}. {3 * deneme} saniye sonra tekrar denenecek.")
                time.sleep(3 * deneme)

        return None

    def karar_detay_getir(self, doc_id):
        clean_id = self.temizle_id_degeri(doc_id) if doc_id else doc_id
        payload = {
            "id": clean_id,
            "size": 1
        }
        yanit = self.api_istek_gonder(payload)
        if yanit and isinstance(yanit, dict) and yanit.get("data"):
            return yanit

        if doc_id != clean_id:
            payload["id"] = doc_id
            yanit = self.api_istek_gonder(payload)
            if yanit and isinstance(yanit, dict) and yanit.get("data"):
                return yanit

        return None

    def hatali_kararlari_indir(self, max_karar=None):
        if not self.ilerleme_oku():
            return

        if not self.hatali_idler:
            self.log_yaz("[BİLGİ] 'ilerleme_durumu.json' içinde hatalı/eksik ID bulunmuyor.")
            return

        aranacak_idler = list(self.hatali_idler)
        if max_karar:
            aranacak_idler = aranacak_idler[:max_karar]
        toplam_hatali = len(aranacak_idler)
        self.log_yaz(f"[*] Toplam {toplam_hatali} adet hatalı/eksik karar ID'si yeniden indiriliyor...")

        kurtarilan_sayisi = 0
        hala_hatali_sayisi = 0

        try:
            for sira, k_id in enumerate(aranacak_idler, start=1):
                self.log_yaz(f"[{sira}/{toplam_hatali}] Sorgulanıyor: ID {k_id}")
                detay_yanit = self.karar_detay_getir(k_id)

                if not detay_yanit or not isinstance(detay_yanit, dict) or not detay_yanit.get("data"):
                    self.log_yaz(f"  [-] Başarısız: ID {k_id} yine alınamadı.")
                    hala_hatali_sayisi += 1
                    continue

                detay_data = detay_yanit.get("data")
                ilk_kayit = detay_data[0] if isinstance(detay_data, list) and len(detay_data) > 0 else {}

                gercek_id = (ilk_kayit.get("id") or k_id) if isinstance(ilk_kayit, dict) else k_id

                yil, ay, gun = self.tarih_ayristir(ilk_kayit, gercek_id)
                dosya_adi = self.dosya_adi_olustur(gercek_id)
                hedef_klasor = os.path.join(self.root_output_dir, yil, ay, gun)
                hedef_dosya_yolu = os.path.join(hedef_klasor, dosya_adi)

                os.makedirs(hedef_klasor, exist_ok=True)
                with open(hedef_dosya_yolu, "w", encoding="utf-8") as f:
                    json.dump(detay_yanit, f, ensure_ascii=False, indent=4)

                self.mevcut_dosyalar.add(dosya_adi)
                if k_id in self._hatali_set:
                    self._hatali_set.discard(k_id)
                    self.hatali_idler.remove(k_id)
                if k_id not in self._basarili_set:
                    self._basarili_set.add(k_id)
                    self.basarili_idler.append(k_id)

                kurtarilan_sayisi += 1
                self.ilerleme_kaydet()

                print(f"  [+] Kurtarıldı: ID: {gercek_id} -> {yil}/{ay}/{gun}/{dosya_adi}")
                if self.delay > 0:
                    time.sleep(self.delay)
                else:
                    time.sleep(random.uniform(0.1, 0.25))

            self.log_yaz(
                f"[BİLGİ] İşlem tamamlandı. Kurtarılan: {kurtarilan_sayisi} | "
                f"Hala Hatalı: {hala_hatali_sayisi} | Kalan Hatalı ID: {len(self.hatali_idler)}"
            )
        except KeyboardInterrupt:
            self.log_yaz("[UYARI] Kullanıcı tarafından durduruldu (Ctrl+C). İlerleme durumu kaydediliyor...")
            self.ilerleme_kaydet()
            self.log_yaz(f"[*] İlerleme '{self.ilerleme_dosyasi}' dosyasına kaydedildi.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Anayasa Mahkemesi Hatalı / Eksik Karar İndirici")
    parser.add_argument("--output", "--dir", dest="output", default="aymjson", help="Çıktı klasörü (varsayılan: aymjson)")
    parser.add_argument("--limit", type=int, default=None, help="Maksimum denenecek hatalı karar sayısı")
    parser.add_argument("--delay", type=float, default=0.15, help="İstekler arası bekleme süresi (saniye, varsayılan: 0.15)")

    args = parser.parse_args()

    kurtarici = AnayasaMahkemesiHataliIndirici(root_output_dir=args.output, delay=args.delay)
    try:
        kurtarici.hatali_kararlari_indir(max_karar=args.limit)
    except KeyboardInterrupt:
        kurtarici.ilerleme_kaydet()
        sys.exit(0)
