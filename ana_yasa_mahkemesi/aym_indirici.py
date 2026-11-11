"""
Anayasa Mahkemesi Karar İndirici ve Arşivleyici

Bu modül, Anayasa Mahkemesi Kararlar Bilgi Bankası genel arama API'sini
kullanarak kararları gün gün tarar; karar detaylarını ve metin içeriklerini
HTML etiketlerinden arındırarak JSON formatında yerel diske kaydeder.

Temel Özellikler:
- Tarih bazlı geriye dönük tarama (varsayılan: günümüzden 1962'ye kadar)
- Yıl/Ay/Gün şeklinde hiyerarşik dizin yapısı
- Bağlantı kopmaları ve hız sınırlamalarına (Rate Limit - HTTP 429) karşı otomatik bekleme ve yeniden deneme
- Kesintiye uğrayan indirme işlemine 'ilerleme_durumu.json' üzerinden kaldığı günden devam edebilme
- Mükerrer indirmeleri önleyen disk önbellek kontrolü
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
    """
    Anayasa Mahkemesi Bilgi Bankası API'si ile iletişim kuran
    ve karar verilerini arşivleyen ana sınıf.
    """

    def __init__(self, root_output_dir="aymjson"):
        """
        Gerekli dizinleri, oturum ayarlarını ve mevcut dosya önbelleğini hazırlar.
        """
        self.root_output_dir = os.path.abspath(root_output_dir)
        if not os.path.exists(self.root_output_dir):
            os.makedirs(self.root_output_dir, exist_ok=True)
            print(f"[*] Çıktı klasörü oluşturuldu: {self.root_output_dir}")

        self.ilerleme_dosyasi = os.path.join(self.root_output_dir, "ilerleme_durumu.json")
        self.api_url = "https://kararlarbilgibankasi.anayasa.gov.tr/api/core/public/search"

        self.session = self.olustur_oturum()
        self.mevcut_dosyalar = set()
        self.dosyalar_onbellekle()

    def olustur_oturum(self):
        """
        Yeniden deneme (retry) kuralları ve bağlantı havuzu tanımlanmış
        bir requests.Session nesnesi yapılandırır.
        """
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
        """
        Daha önce indirilmiş JSON dosyalarının isimlerini tarayarak
        hafızaya (set) alır. Böylece mükerrer dosya kontrolü hızlıca yapılır.
        """
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
        """
        İlerleme dosyasını kontrol eder; varsa kalınan son tarihi (YYYY-MM-DD) döner.
        """
        if os.path.exists(self.ilerleme_dosyasi):
            try:
                with open(self.ilerleme_dosyasi, "r", encoding="utf-8") as f:
                    veri = json.load(f)
                    son_t = veri.get("son_tarih")
                    if son_t:
                        # GG.AA.YYYY formatı geldiyse YYYY-MM-DD formatına dönüştür
                        if "." in son_t:
                            parts = son_t.split(".")
                            if len(parts) == 3:
                                return f"{parts[2]}-{parts[1]}-{parts[0]}"
                        return son_t
            except Exception:
                pass
        return None

    def ilerleme_kaydet(self, tarih_str, indirilen_ekstra=0):
        """
        İşlenen son tarihi ve toplam dosya adedini durum dosyasına kaydeder.
        """
        try:
            with open(self.ilerleme_dosyasi, "w", encoding="utf-8") as f:
                json.dump({
                    "son_tarih": tarih_str,
                    "toplam_onbellek_dosya": len(self.mevcut_dosyalar),
                    "guncelleme_zamani_utc": datetime.now(timezone.utc).isoformat()
                }, f, ensure_ascii=False, indent=4)
        except Exception:
            pass

    def log_yaz(self, mesaj):
        """
        Zaman damgalı standart konsol çıktısı üretir.
        """
        zaman = time.strftime("%H:%M:%S")
        print(f"[{zaman}] {mesaj}")

    def html_temizle(self, html_metin):
        """
        HTML etiketlerini, özel karakter kodlarını (HTML entities) ve
        fazlalık boşlukları temizler.
        """
        if not html_metin or not isinstance(html_metin, str):
            return ""

        temiz = re.sub(r'<[^>]+>', ' ', html_metin)
        temiz = temiz.replace('&nbsp;', ' ')
        temiz = temiz.replace('&uuml;', 'ü').replace('&Uuml;', 'Ü')
        temiz = temiz.replace('&ouml;', 'ö').replace('&Ouml;', 'Ö')
        temiz = temiz.replace('&ccedil;', 'ç').replace('&Ccedil;', 'Ç')
        temiz = temiz.replace('&ş;', 'ş').replace('&Ş;', 'Ş')
        temiz = temiz.replace('&ğ;', 'ğ').replace('&Ğ;', 'Ğ')
        temiz = temiz.replace('&ı;', 'ı').replace('&İ;', 'İ')
        temiz = temiz.replace('&amp;', '&').replace('&quot;', '"').replace('&apos;', "'")
        temiz = re.sub(r'\s+', ' ', temiz).strip()
        return temiz

    def metinleri_recursive_temizle(self, veri):
        """
        İç içe geçmiş sözlük (dict) ve liste yapılarındaki tüm metin alanlarını
        özyinelemeli olarak dolaşarak HTML artıklarından arındırır.
        """
        if isinstance(veri, dict):
            return {k: self.metinleri_recursive_temizle(v) for k, v in veri.items()}
        elif isinstance(veri, list):
            return [self.metinleri_recursive_temizle(item) for item in veri]
        elif isinstance(veri, str):
            if '<' in veri and '>' in veri:
                return self.html_temizle(veri)
            return veri
        return veri

    def temizle_id_degeri(self, raw_id):
        """
        API'den dönen karmaşık veya Base64 formatlı ID değerlerini ayrıştırır
        ve dosya sisteminde kullanılabilecek güvenli bir metne dönüştürür.
        """
        if not raw_id:
            return ""
        str_id = str(raw_id).strip()

        # Base64 ile kodlanmış 'kbb:...' formatındaki ID'ler için çözümleme
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

        # İşletim sistemi dosya adlarında geçersiz karakterleri alt çizgiye dönüştür
        temiz = re.sub(r'[\\/*?:"<>| ]', '_', str_id)
        return temiz

    def dosya_adi_olustur(self, kimlik_degeri):
        """
        Verilen kimlik bilgisine göre standart JSON dosya adı üretir.
        """
        temiz = self.temizle_id_degeri(kimlik_degeri)
        return f"karar_{temiz}.json"

    def api_istek_gonder(self, payload, max_deneme=5):
        """
        Arama API'sine POST isteği gönderir.
        429 (Too Many Requests) ve geçici sunucu hatalarında katlanarak artan
        bekleme süreleriyle yeniden dener.
        """
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
        """
        Belirtilen güne (YYYY-MM-DD) ait karar özetlerini
        sayfalama (pagination) mantığıyla API'den toplar.
        """
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

    def karar_detay_getir(self, doc_id, karar_tipi):
        """
        Karar ID'si ve türü üzerinden kararın detaylı metnini ve künyesini sorgular.
        """
        clean_id = doc_id
        if doc_id:
            try:
                clean_id = self.temizle_id_degeri(doc_id)
            except Exception:
                pass

        payload = {
            "id": clean_id,
            "kararTipi": karar_tipi or "BireyselBasvuru",
            "size": 1
        }
        return self.api_istek_gonder(payload)

    def arsivle(self, baslangic_tarihi_str=None, bitis_tarihi_str="1962-01-01"):
        """
        Belirlenen başlangıç tarihinden bitiş tarihine kadar geriye doğru
        tüm günleri tarayarak kararları diskte arşivler.
        Tarih formatı: YYYY-MM-DD
        """
        # Kayıtlı ilerleme kontrolü
        if not baslangic_tarihi_str:
            kayitli_son = self.ilerleme_oku()
            if kayitli_son:
                baslangic_tarihi_str = kayitli_son
                self.log_yaz(f"[*] Kayıtlı durumdan devam ediliyor: {baslangic_tarihi_str}")
            else:
                baslangic_tarihi_str = datetime.now().strftime("%Y-%m-%d")

        current_date = datetime.strptime(baslangic_tarihi_str, "%Y-%m-%d")
        end_date = datetime.strptime(bitis_tarihi_str, "%Y-%m-%d")

        self.log_yaz(f"[*] Arşivleme başlatılıyor: {current_date.strftime('%Y-%m-%d')} -> {end_date.strftime('%Y-%m-%d')}")

        toplam_indirilen = 0

        while current_date >= end_date:
            tarih_iso = current_date.strftime("%Y-%m-%d")
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
                    k_tip = karar.get("kararTipi") or karar.get("karar_tipi") or "BireyselBasvuru"
                    basvuru_no = karar.get("basvuruNo") or karar.get("kararNo") or k_id

                    dosya_adi = self.dosya_adi_olustur(basvuru_no)
                    alt_dosya_adi = self.dosya_adi_olustur(k_id)

                    # Hiyerarşik klasör yapısı: aymjson/YYYY/MM/DD
                    hedef_klasor = os.path.join(self.root_output_dir, yil, ay, gun)
                    hedef_dosya_yolu = os.path.join(hedef_klasor, dosya_adi)

                    # Dosya daha önceden indirilmişse atla
                    if dosya_adi in self.mevcut_dosyalar or alt_dosya_adi in self.mevcut_dosyalar or os.path.exists(hedef_dosya_yolu):
                        continue

                    # Karar detayını sorgula
                    detay_yanit = self.karar_detay_getir(k_id, k_tip)
                    if not detay_yanit:
                        self.log_yaz(f"[UYARI] Detay içeriği alınamadı, atlandı: ID {k_id}")
                        continue

                    birlesik_veri = {
                        "baslik": karar.get("basvuruAdi") or karar.get("kararKonusu") or f"Karar {basvuru_no}",
                        "detay_bilgi": f"{basvuru_no} | {karar.get('kararTuruBasvuruSonucuLabel', '')} | Karar Tarihi: {tarih_iso}",
                        "id": k_id,
                        "karar_tipi": k_tip,
                        "kategori": karar.get("kararVerenBirimLabel") or k_tip,
                        "link": f"/kbb/pages/search/Tumu?id={k_id}&type={k_tip}",
                        "onizleme_metni": karar.get("kararKonusu", ""),
                        "api_detay_icerigi": detay_yanit,
                        "meta_veri": {
                            "arsivleme_tarihi_utc": datetime.now(timezone.utc).isoformat(),
                            "karar_tarihi": tarih_iso,
                            "yil": yil,
                            "ay": ay,
                            "gun": gun,
                            "kayit_dizini": f"{os.path.basename(self.root_output_dir)}/{yil}/{ay}/{gun}",
                            "kaynak": "Anayasa Mahkemesi Kararlar Bilgi Bankası"
                        }
                    }

                    # HTML etiketlerini ve fazlalıkları ayıkla
                    temiz_veri = self.metinleri_recursive_temizle(birlesik_veri)

                    os.makedirs(hedef_klasor, exist_ok=True)
                    with open(hedef_dosya_yolu, "w", encoding="utf-8") as f:
                        json.dump(temiz_veri, f, ensure_ascii=False, indent=4)

                    self.mevcut_dosyalar.add(dosya_adi)
                    self.mevcut_dosyalar.add(alt_dosya_adi)
                    toplam_indirilen += 1
                    gunluk_indirilen += 1

                    print(f"  [+] Kaydedildi: {tarih_iso} | {basvuru_no} -> {yil}/{ay}/{gun}/{dosya_adi}")
                    time.sleep(random.uniform(0.05, 0.15))

                if gunluk_indirilen > 0:
                    self.log_yaz(f"Tamamlandı: {tarih_iso} tarihinden {gunluk_indirilen} karar kaydedildi.")

            # Her günün sonunda durumu dosyaya yaz
            self.ilerleme_kaydet(tarih_iso, toplam_indirilen)

            # Bir önceki güne geç
            current_date -= timedelta(days=1)
            time.sleep(0.05)

        self.log_yaz(f"[BİLGİ] Arşivleme tamamlandı. Toplam indirilen karar: {toplam_indirilen}")


if __name__ == "__main__":
    base_dir = os.path.dirname(os.path.abspath(__file__))
    output_dir = os.path.join(base_dir, "aymjson")
    bot = AnayasaMahkemesiScraper(root_output_dir=output_dir)
    bot.arsivle()
