"""
Danıştay Karar Arama Portalı - Karar Künyesi Kazıyıcı

Bu modül, Danıştay Karar Arama servisi üzerinden yıllara ve sayfalara göre
arama yaparak karar künye bilgilerini (ID, daire, esas/karar no, tarih vb.)
sayfa bazlı JSON dosyaları halinde yerel diske arşivler.

Temel Özellikler:
- 2026 yılından 1950'ye kadar geriye dönük yıllık tarama
- Web Uygulama Güvenlik Duvarı (WAF) derinlik sınırına uyumlu sayfalama (maksimum 1000 sayfa)
- İstek aşımı ve 429/403 yanıt kodlarına karşı artan bekleme süreleri (exponential backoff)
- 'durum_detay.json' ile kesinti durumunda kalınan yıl ve sayfadan devam edebilme
"""

import json
import os
import time
import requests

# Danıştay detaylı arama API uç noktası
URL = "https://karararama.danistay.gov.tr/aramadetaylist"

# İstek başlıkları (Gerektiğinde güncel oturum çerezleri eklenebilir)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Content-Type": "application/json; charset=UTF-8",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://karararama.danistay.gov.tr",
    "Referer": "https://karararama.danistay.gov.tr/",
    "X-Requested-With": "XMLHttpRequest",
}

# Çıktı klasörü ve durum takip dosyası (kesinti durumunda devam edebilmek için)
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "danistay_detay_arsiv")
STATE_FILE = os.path.join(OUTPUT_DIR, "durum_detay.json")
os.makedirs(OUTPUT_DIR, exist_ok=True)


def durumu_yukle():
    """
    Önceki çalışmadan kalan yıl ve sayfa numarasını yükler.
    Kayıt yoksa varsayılan başlangıç değerlerini döner.
    """
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Sayfa 1000'i geçtiyse WAF sınırına takılmamak için 1'e çek
                if data.get("pageNumber", 1) > 999:
                    data["pageNumber"] = 1
                return data
        except Exception:
            pass
    return {"yil": 2026, "pageNumber": 1}


def durumu_kaydet(yil, page_number):
    """
    Mevcut taranan yıl ve sayfa numarasını durum takip dosyasına kaydeder.
    """
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"yil": yil, "pageNumber": page_number}, f, ensure_ascii=False, indent=4)


def arsivi_kaz():
    """
    Belirlenen yıllar aralığında Danıştay arama servisini tarayarak
    karar künyelerini sayfa bazında JSON dosyalarına kaydeder.
    """
    kayitli_durum = durumu_yukle()
    baslangic_yili = kayitli_durum.get("yil", 2026)
    baslangic_sayfasi = kayitli_durum.get("pageNumber", 1)

    page_size = 100
    toplam_cekilen = 0

    # 2026'dan 1950'ye kadar geriye dönük arama listesi
    yillar = list(range(2026, 1949, -1))

    print("\n[*] Danıştay karar künyesi tarama işlemi başlatıldı.")
    print(f"[*] Başlangıç Yılı: {baslangic_yili} | Başlangıç Sayfası: {baslangic_sayfasi}\n")

    for yil in yillar:
        if yil > baslangic_yili:
            continue

        baslangic_tarihi = f"01.01.{yil}"
        bitis_tarihi = f"31.12.{yil}"

        current_page = baslangic_sayfasi if yil == baslangic_yili else 1
        devam_et_yil = True

        print(f"\n[*] Yıl taranıyor: {yil} (Başlangıç Sayfası: {current_page})")

        while devam_et_yil:
            # WAF derinlik sınırını aşmamak için kontrol
            if current_page > 999:
                print(f"[UYARI] {yil} yılı için 1000 sayfa sınırına ulaşıldı. Sonraki yıla geçiliyor.")
                break

            payload = {
                "data": {
                    "daire": "",
                    "esasYil": "",
                    "esasIlkSiraNo": "",
                    "esasSonSiraNo": "",
                    "kararYil": "",
                    "kararIlkSiraNo": "",
                    "kararSonSiraNo": "",
                    "baslangicTarihi": baslangic_tarihi,
                    "bitisTarihi": bitis_tarihi,
                    "mevzuatNumarasi": "",
                    "mevzuatAdi": "",
                    "madde": "",
                    "siralama": "1",
                    "siralamaDirection": "desc",
                    "pageSize": page_size,
                    "pageNumber": current_page,
                }
            }

            basarili = False
            deneme = 0

            while not basarili and deneme < 5:
                try:
                    time.sleep(2)  # İstekler arası sunucuya yük bindirmemek için bekleme
                    response = requests.post(URL, json=payload, headers=HEADERS, timeout=35)

                    if response.status_code == 200:
                        try:
                            sonuc = response.json()
                        except Exception:
                            print("[UYARI] JSON ayrıştırma hatası, 5 saniye bekleniyor...")
                            deneme += 1
                            time.sleep(5)
                            continue

                        veri_alani = sonuc.get("data")
                        if not isinstance(veri_alani, dict):
                            print(f"[BİLGİ] {yil} yılı için veri sonlandı veya sayfa sınırına ulaşıldı.")
                            devam_et_yil = False
                            break

                        karar_listesi = veri_alani.get("data", [])
                        records_total = veri_alani.get("recordsTotal", 0)

                        if not karar_listesi:
                            print(f"[*] {yil} yılı için bu sayfada kayıt bulunamadı. Yıl tamamlandı.")
                            devam_et_yil = False
                            break

                        toplam_sayfa_yil = (records_total // page_size) + (1 if records_total % page_size > 0 else 0)

                        temiz_liste = []
                        for k in karar_listesi:
                            if isinstance(k, dict):
                                temiz_liste.append({
                                    "id": k.get("id"),
                                    "daire": k.get("daireKurul"),
                                    "esasNo": k.get("esasNo"),
                                    "kararNo": k.get("kararNo"),
                                    "tarih": k.get("kararTarihi"),
                                })

                        dosya_adi = os.path.join(OUTPUT_DIR, f"danistay_{yil}_sayfa_{current_page}.json")
                        with open(dosya_adi, "w", encoding="utf-8") as f:
                            json.dump(temiz_liste, f, ensure_ascii=False, indent=4)

                        toplam_cekilen += len(temiz_liste)
                        print(
                            f"  [+] [{yil}] Sayfa {current_page}/{toplam_sayfa_yil} | "
                            f"Kayıt: {len(temiz_liste)} | Toplam: {toplam_cekilen}"
                        )

                        current_page += 1
                        durumu_kaydet(yil, current_page)
                        basarili = True

                    elif response.status_code in [403, 429]:
                        deneme += 1
                        bekleme = deneme * 10
                        print(f"[UYARI] Erişim sınırı ({response.status_code}). {bekleme} saniye bekleniyor... (Deneme {deneme}/5)")
                        time.sleep(bekleme)
                    else:
                        print(f"[UYARI] Sunucu yanıt kodu: {response.status_code}, 10 saniye bekleniyor...")
                        deneme += 1
                        time.sleep(10)

                except Exception as e:
                    print(f"[HATA] Bağlantı hatası: {e}. 10 saniye sonra tekrar denenecek...")
                    deneme += 1
                    time.sleep(10)

            if not basarili and deneme >= 5:
                print(f"[UYARI] {yil} yılı Sayfa {current_page} için üst üste hata alındı. Sonraki yıla geçiliyor.")
                break

        baslangic_sayfasi = 1

    print(f"\n[BİLGİ] İşlem tamamlandı. Toplam çekilen künye sayısı: {toplam_cekilen}")


if __name__ == "__main__":
    arsivi_kaz()
