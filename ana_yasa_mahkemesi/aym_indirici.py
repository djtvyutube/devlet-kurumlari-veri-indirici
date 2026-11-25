"""
Anayasa Mahkemesi Karar İndirici

Kararlar Bilgi Bankası API'sini gün bazında tarar ve dönen yanıtları
değiştirmeden ham JSON olarak Yıl/Ay/Gün klasör yapısında diske kaydeder.
"""

import os
import sys
import time
import json
import random
import re
import base64
from datetime import datetime, timezone, timedelta
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class AnayasaMahkemesiScraper:
    def __init__(self, root_output_dir="aymjson"):
        self.root_output_dir = os.path.abspath(root_output_dir)
        if not os.path.exists(self.root_output_dir):
            os.makedirs(self.root_output_dir, exist_ok=True)
            print(f"[*] Çıktı klasörü oluşturuldu: {self.root_output_dir}")

        self.ilerleme_dosyasi = os.path.join(self.root_output_dir, "ilerleme_durumu.json")
        self.api_url = "https://kararlarbilgibankasi.anayasa.gov.tr/api/core/public/search"

        self.session = self.olustur_oturum()
        self.mevcut_dosyalar = set()
        self.basarili_idler = []
        self._basarili_set = set()
        self.hatali_idler = []
        self._hatali_set = set()
        self.aktif_tarih = None
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

    def dosyalar_onbellekle(self):
        if os.path.exists(self.root_output_dir):
            print("[*] Mevcut dosya arşivi taranıyor...")
            dosya_sayisi = 0
            for root, dirs, files in os.walk(self.root_output_dir):
                for file in files:
                    if file.endswith('.json') and file != "ilerleme_durumu.json":
                        self.mevcut_dosyalar.add(file)
                        dosya_sayisi += 1
            print(f"[*] Önbelleğe alınan mevcut dosya sayısı: {dosya_sayisi}")

    def ilerleme_oku(self):
        if os.path.exists(self.ilerleme_dosyasi):
            try:
                with open(self.ilerleme_dosyasi, "r", encoding="utf-8") as f:
                    veri = json.load(f)
                    kayitli_basarililar = veri.get("basarili_idler", [])
                    if isinstance(kayitli_basarililar, list):
                        self.basarili_idler = kayitli_basarililar
                        self._basarili_set = set(kayitli_basarililar)

                    kayitli_hatalar = veri.get("hatali_idler", [])
                    if isinstance(kayitli_hatalar, list):
                        self.hatali_idler = kayitli_hatalar
                        self._hatali_set = set(kayitli_hatalar)

                    son_t = veri.get("son_tarih")
                    if son_t:
                        if "." in son_t:
                            parts = son_t.split(".")
                            if len(parts) == 3:
                                return f"{parts[2]}-{parts[1]}-{parts[0]}"
                        return son_t
            except Exception:
                pass
        return None

    def ilerleme_kaydet(self, tarih_str, indirilen_ekstra=0):
        try:
            gecici_dosya = f"{self.ilerleme_dosyasi}.tmp"
            with open(gecici_dosya, "w", encoding="utf-8") as f:
                json.dump({
                    "son_tarih": tarih_str,
                    "toplam_onbellek_dosya": len(self.mevcut_dosyalar),
                    "basarili_id_sayisi": len(self.basarili_idler),
                    "basarili_idler": self.basarili_idler,
                    "hatali_id_sayisi": len(self.hatali_idler),
                    "hatali_idler": self.hatali_idler,
                    "guncelleme_zamani_utc": datetime.now(timezone.utc).isoformat()
                }, f, ensure_ascii=False, indent=4)
            os.replace(gecici_dosya, self.ilerleme_dosyasi)
        except Exception:
            pass

    def log_yaz(self, mesaj):
        zaman = time.strftime("%H:%M:%S")
        print(f"[{zaman}] {mesaj}")

    def temizle_id_degeri(self, raw_id):
        """Base64 formatlı (kbb:...) ID değerlerini çözer ve dosya adına uygun hale getirir."""
        if not raw_id:
            return ""
        str_id = str(raw_id).strip()

        if str_id.startswith("a2Ji") or str_id.startswith("kbb:"):
            try:
                padding = 4 - (len(str_id) % 4)
                if padding < 4:
                    str_id_padded = str_id + ('=' * padding)
                else:
                    str_id_padded = str_id

                decoded = base64.b64decode(str_id_padded).decode('utf-8', errors='ignore')
                if ':' in decoded:
                    parts = decoded.split(':')
                    if len(parts) > 1 and parts[1]:
                        str_id = parts[1]
                else:
                    str_id = decoded
            except Exception:
                pass

        temiz = re.sub(r'[\\/*?:"<>| ]', '_', str_id)
        return temiz

    def dosya_adi_olustur(self, kimlik_degeri):
        temiz = self.temizle_id_degeri(kimlik_degeri)
        return f"karar_{temiz}.json"

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
                    continue
                else:
                    self.log_yaz(f"[UYARI] Sunucu {response.status_code} yanıtı döndü. 5 saniye bekleniyor...")
                    time.sleep(5)
            except requests.exceptions.RequestException as e:
                self.log_yaz(f"[HATA] Bağlantı hatası: {e}. {3 * deneme} saniye sonra tekrar denenecek.")
                time.sleep(3 * deneme)

        return None

    def gunun_kararlarini_getir(self, tarih_iso):
        tum_kararlar = []
        page = 1
        page_size = 50

        while True:
            payload = {
                "kararTarihiStart": tarih_iso,
                "kararTarihiEnd": tarih_iso,
                "page": page,
                "size": page_size
            }
            sonuc = self.api_istek_gonder(payload)
            if not sonuc or not isinstance(sonuc, dict):
                break

            total = sonuc.get("total", 0)
            data = sonuc.get("data", [])
            if not data:
                break

            tum_kararlar.extend(data)

            if len(tum_kararlar) >= total or len(data) < page_size:
                break

            page += 1
            time.sleep(0.15)

        return tum_kararlar

    def karar_detay_getir(self, doc_id, karar_tipi=None):
        clean_id = doc_id
        if doc_id:
            try:
                clean_id = self.temizle_id_degeri(doc_id)
            except Exception:
                pass

        payload = {
            "id": clean_id,
            "size": 1
        }
        if karar_tipi is not None:
            payload["kararTipi"] = karar_tipi

        return self.api_istek_gonder(payload)

    def arsivle(self, baslangic_tarihi_str=None, bitis_tarihi_str="1962-01-01"):
        kayitli_son = self.ilerleme_oku()
        if not baslangic_tarihi_str:
            if kayitli_son:
                baslangic_tarihi_str = kayitli_son
                self.log_yaz(f"[*] Kayıtlı durumdan devam ediliyor: {baslangic_tarihi_str}")
            else:
                baslangic_tarihi_str = datetime.now().strftime("%Y-%m-%d")

        current_date = datetime.strptime(baslangic_tarihi_str, "%Y-%m-%d")
        end_date = datetime.strptime(bitis_tarihi_str, "%Y-%m-%d")

        self.log_yaz(f"[*] Arşivleme başlatılıyor: {current_date.strftime('%Y-%m-%d')} -> {end_date.strftime('%Y-%m-%d')}")

        toplam_indirilen = 0
        tarih_iso = current_date.strftime("%Y-%m-%d")
        self.aktif_tarih = tarih_iso

        try:
            while current_date >= end_date:
                tarih_iso = current_date.strftime("%Y-%m-%d")
                self.aktif_tarih = tarih_iso
                yil = current_date.strftime("%Y")
                ay = current_date.strftime("%m")
                gun = current_date.strftime("%d")

                karar_listesi = self.gunun_kararlarini_getir(tarih_iso)
                karar_sayisi = len(karar_listesi)

                if karar_sayisi > 0:
                    self.log_yaz(f"Tarih: {tarih_iso} | Bulunan Karar: {karar_sayisi}")
                    gunluk_indirilen = 0

                    for karar in karar_listesi:
                        k_id = karar.get("id")
                        if not k_id:
                            continue
                        k_tip = karar.get("kararTipi")

                        dosya_adi = self.dosya_adi_olustur(k_id)

                        hedef_klasor = os.path.join(self.root_output_dir, yil, ay, gun)
                        hedef_dosya_yolu = os.path.join(hedef_klasor, dosya_adi)

                        if dosya_adi in self.mevcut_dosyalar or os.path.exists(hedef_dosya_yolu):
                            if k_id not in self._basarili_set:
                                self._basarili_set.add(k_id)
                                self.basarili_idler.append(k_id)
                            if k_id in self._hatali_set:
                                self._hatali_set.discard(k_id)
                                self.hatali_idler.remove(k_id)
                            continue

                        detay_yanit = self.karar_detay_getir(k_id, k_tip)
                        if not detay_yanit or not isinstance(detay_yanit, dict) or not detay_yanit.get("data"):
                            self.log_yaz(f"[UYARI] Detay içeriği alınamadı, atlandı: ID {k_id}")
                            if k_id not in self._hatali_set:
                                self._hatali_set.add(k_id)
                                self.hatali_idler.append(k_id)
                                self.ilerleme_kaydet(tarih_iso, toplam_indirilen)
                            continue

                        os.makedirs(hedef_klasor, exist_ok=True)
                        with open(hedef_dosya_yolu, "w", encoding="utf-8") as f:
                            json.dump(detay_yanit, f, ensure_ascii=False, indent=4)

                        self.mevcut_dosyalar.add(dosya_adi)
                        if k_id not in self._basarili_set:
                            self._basarili_set.add(k_id)
                            self.basarili_idler.append(k_id)
                        if k_id in self._hatali_set:
                            self._hatali_set.discard(k_id)
                            self.hatali_idler.remove(k_id)
                        toplam_indirilen += 1
                        gunluk_indirilen += 1
                        self.ilerleme_kaydet(tarih_iso, toplam_indirilen)

                        print(f"  [+] Kaydedildi: {tarih_iso} | ID: {k_id} -> {yil}/{ay}/{gun}/{dosya_adi}")
                        time.sleep(random.uniform(0.05, 0.15))

                    if gunluk_indirilen > 0:
                        self.log_yaz(f"Tamamlandı: {tarih_iso} tarihinden {gunluk_indirilen} karar kaydedildi.")

                self.ilerleme_kaydet(tarih_iso, toplam_indirilen)

                current_date -= timedelta(days=1)
                time.sleep(0.05)

            self.log_yaz(f"[BİLGİ] Arşivleme tamamlandı. Toplam indirilen karar: {toplam_indirilen}")
        except KeyboardInterrupt:
            self.log_yaz("[UYARI] Kullanıcı tarafından durduruldu (Ctrl+C). İlerleme durumu kaydediliyor...")
            self.ilerleme_kaydet(self.aktif_tarih or tarih_iso, toplam_indirilen)
            self.log_yaz(f"[*] İlerleme '{self.ilerleme_dosyasi}' dosyasına kaydedildi.")


if __name__ == "__main__":
    base_dir = os.path.dirname(os.path.abspath(__file__))
    output_dir = os.path.join(base_dir, "aymjson")
    bot = AnayasaMahkemesiScraper(root_output_dir=output_dir)
    try:
        bot.arsivle()
    except KeyboardInterrupt:
        bot.ilerleme_kaydet(bot.aktif_tarih or datetime.now().strftime("%Y-%m-%d"))
        sys.exit(0)
