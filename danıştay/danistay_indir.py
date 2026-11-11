"""
Danıştay Karar İndirici ve Arşivleyici

Bu modül, arama listelerinden (danistay_kazima.py tarafından üretilen JSON dosyaları)
elde edilen karar ID'lerini kullanarak Danıştay sunucusundan karar metinlerini indirir.
HTML içeriğini ayrıştırıp temiz metne dönüştürür; daire, esas no, karar no ve tarih
bilgilerini regex ile ayıklayarak 'Daire/Yıl/karar_{id}.json' hiyerarşik dizininde kaydeder.

Temel Özellikler:
- Çoklu iş parçacığı (ThreadPoolExecutor) ile eşzamanlı ve kontrollü indirme
- HTML ayrıştırma (BeautifulSoup) ile 'hiddencontent' etiketindeki kararı ayıklama
- Regex tabanlı üst veri çıkarma (daire, esas, karar no, karar yılı)
- Otomatik tekilleştirme: Daha önce indirilmiş ID'leri atlayarak mükerrerliği önleme
"""

import concurrent.futures
import glob
import html
import json
import os
import random
import re
import time
from datetime import timedelta
from bs4 import BeautifulSoup
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import urllib3

# SSL uyarılarını bastır
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Dizin yapılandırmaları
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ARAMA_LISTE_KLASORU = os.path.join(BASE_DIR, "danistay_detay_arsiv")
ARSIV_KOK_DIZINI = os.path.join(BASE_DIR, "danistay_arsiv_duzenli")

# Eşzamanlı istek iş parçacığı sayısı
MAX_WORKERS = 6

os.makedirs(ARSIV_KOK_DIZINI, exist_ok=True)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:123.0) Gecko/20100101 Firefox/123.0",
]


def get_http_session():
    """
    Yeniden deneme (retry) ve havuz yönetimi destekli HTTP oturumu oluşturur.
    """
    session = requests.Session()
    retry_strategy = Retry(
        total=4,
        backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=15, pool_maxsize=15)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


session = get_http_session()


def metinden_meta_veri_ayikla(metin):
    """
    Karar metni içerisinden daire, esas no, karar no ve tarih bilgilerini regex ile ayıklar.
    """
    meta = {
        "daire": "Bilinmeyen_Daire",
        "esas_no": "Bilinmeyen_Esas",
        "karar_no": "Bilinmeyen_Karar",
        "tarih": "Bilinmeyen_Tarih",
        "esas_yil": "Bilinmeyen",
        "karar_yil": "Bilinmeyen",
    }

    daire_match = re.search(r'(Danıştay\s+[0-9\.\w\s]+(?:Dairesi|Kurulu|Başkanlığı))', metin, re.IGNORECASE)
    if daire_match:
        meta["daire"] = daire_match.group(1).strip()

    esas_match = re.search(r'(?:Esas\s*No\s*[:\.]?\s*|Esas\s*[:\.]?\s*)?([0-9]{4}\s*/\s*[0-9]+)\s*(?:E\.|Esas)', metin, re.IGNORECASE)
    if esas_match:
        meta["esas_no"] = esas_match.group(1).replace(" ", "") + " E."
        meta["esas_yil"] = meta["esas_no"].split("/")[0]

    karar_match = re.search(r'(?:Karar\s*No\s*[:\.]?\s*|Karar\s*[:\.]?\s*)?([0-9]{4}\s*/\s*[0-9]+)\s*(?:K\.|Karar)', metin, re.IGNORECASE)
    if karar_match:
        meta["karar_no"] = karar_match.group(1).replace(" ", "") + " K."
        meta["karar_yil"] = meta["karar_no"].split("/")[0]

    tarih_match = re.search(r'\b(0?[1-9]|[12][0-9]|3[01])[\./](0?[1-9]|1[012])[\./](20[0-9]{2})\b', metin)
    if tarih_match:
        meta["tarih"] = f"{tarih_match.group(1)}.{tarih_match.group(2)}.{tarih_match.group(3)}"

    return meta


def guvenli_klasor_adi_yap(isim):
    """
    Dizin adlarında dosya sistemi uyumluluğunu sağlamak için geçersiz karakterleri temizler.
    """
    if not isim:
        return "Bilinmeyen_Daire"
    temiz = re.sub(r'[^\w\s\.\-_]', '', isim)
    return temiz.strip().replace(" ", "_")


def karar_dosyasini_klasorle_ve_kaydet(karar_objesi):
    """
    Kararı Daire/Yıl dizin yapısına göre JSON formatında diske yazar.
    Dosya zaten varsa yeniden yazmaz.
    """
    daire_klasor_adi = guvenli_klasor_adi_yap(karar_objesi["daire"])
    yil_degeri = karar_objesi.get("yil")
    if not yil_degeri or yil_degeri == "Bilinmeyen":
        meta = metinden_meta_veri_ayikla(karar_objesi.get("karar_metni", ""))
        yil_degeri = meta["karar_yil"] if meta["karar_yil"] != "Bilinmeyen" else meta["esas_yil"]
        karar_objesi["yil"] = yil_degeri

    yil_klasor_adi = str(yil_degeri) if yil_degeri and yil_degeri != "Bilinmeyen" else "Bilinmeyen_Yil"

    hedef_dizin = os.path.join(ARSIV_KOK_DIZINI, daire_klasor_adi, yil_klasor_adi)
    os.makedirs(hedef_dizin, exist_ok=True)

    dokuman_id = karar_objesi["dokuman_id"]
    dosya_adi = f"karar_{dokuman_id}.json"
    dosya_yolu = os.path.join(hedef_dizin, dosya_adi)

    if os.path.exists(dosya_yolu):
        return

    with open(dosya_yolu, "w", encoding="utf-8") as f:
        json.dump(karar_objesi, f, ensure_ascii=False, indent=4)


def mevcut_arsivi_tara_ve_idler_getir():
    """
    Diskte daha önce indirilmiş kararların ID listesini döner.
    """
    mevcut_idler = set()
    if not os.path.exists(ARSIV_KOK_DIZINI):
        return mevcut_idler

    print("[*] Mevcut arşiv taranıyor...")
    bulunan_dosyalar = glob.glob(os.path.join(ARSIV_KOK_DIZINI, "**", "*.json"), recursive=True)
    for dosya_yolu in bulunan_dosyalar:
        dosya_adi = os.path.basename(dosya_yolu)
        if dosya_adi.startswith("karar_"):
            dokuman_id = dosya_adi.replace("karar_", "").replace(".json", "")
            mevcut_idler.add(dokuman_id)

    return mevcut_idler


def karari_coz_ve_isle(html_icerik):
    """
    Danıştay getDokuman uç noktasından dönen HTML içindeki 'hiddencontent'
    etiketini ve karar metnini ayrıştırır.
    """
    try:
        soup = BeautifulSoup(html_icerik, "html.parser")
        hidden_tag = soup.find("p", id="hiddencontent")
        if not hidden_tag:
            return None
        decoded_html = html.unescape(hidden_tag.decode_contents())
        karar_soup = BeautifulSoup(decoded_html, "html.parser")
        temiz_metin = karar_soup.get_text(separator="\n")
        if len(temiz_metin.strip()) < 50:
            return None
        return temiz_metin
    except Exception:
        return None


def karar_indir_ve_kaydet(dokuman_id, aranan_kelime):
    """
    Tek bir karar dokümanını Danıştay sunucusundan çeker, ayrıştırır ve kaydeder.
    """
    url = "https://karararama.danistay.gov.tr/getDokuman"
    params = {"id": dokuman_id, "arananKelime": aranan_kelime}
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://karararama.danistay.gov.tr/",
        "Connection": "keep-alive",
    }

    for deneme in range(3):
        try:
            time.sleep(random.uniform(0.2, 0.5))
            response = session.get(url, params=params, headers=headers, timeout=15, verify=False)

            if response.status_code == 200 and len(response.text) > 200:
                temiz_metin = karari_coz_ve_isle(response.text)
                if temiz_metin:
                    meta = metinden_meta_veri_ayikla(temiz_metin)
                    yil_degeri = meta["karar_yil"] if meta["karar_yil"] != "Bilinmeyen" else meta["esas_yil"]

                    karar_objesi = {
                        "dokuman_id": dokuman_id,
                        "daire": meta["daire"],
                        "esas_no": meta["esas_no"],
                        "karar_no": meta["karar_no"],
                        "tarih": meta["tarih"],
                        "yil": yil_degeri,
                        "karar_metni": temiz_metin,
                    }

                    karar_dosyasini_klasorle_ve_kaydet(karar_objesi)
                    return ("success", dokuman_id)
                else:
                    return ("failed_parse", dokuman_id)
            elif response.status_code == 429:
                time.sleep((deneme + 1) * 3)
                continue
            else:
                return (f"failed_status_{response.status_code}", dokuman_id)
        except Exception:
            time.sleep(1.5)

    return ("failed_exception", dokuman_id)


def gorevleri_topla(json_dosyalari, islenen_idler):
    """
    Arama sonuçları klasöründeki JSON dosyalarından karar ID'lerini toplar,
    zaten indirilmiş veya tekrarlanan ID'leri eler.
    """
    print("[*] JSON listeleri taranıyor ve tekilleştirme yapılıyor...")
    islenecek_gorevler = []
    islenen_gecici_takip = set(islenen_idler)
    atlanan_mukerrer = 0

    for json_dosya in json_dosyalari:
        try:
            with open(json_dosya, "r", encoding="utf-8") as f:
                veri = json.load(f)

            items = veri if isinstance(veri, list) else []
            if isinstance(veri, dict):
                for v in veri.values():
                    if isinstance(v, list):
                        items.extend(v)

            for item in items:
                if not isinstance(item, dict):
                    continue
                dokuman_id = item.get("id") or item.get("dokumanId") or item.get("DocumentId")
                aranan_kelime = item.get("arananKelime") or item.get("kelime") or "2026"

                if dokuman_id:
                    str_id = str(dokuman_id).strip()
                    if str_id:
                        if str_id not in islenen_gecici_takip:
                            islenen_gecici_takip.add(str_id)
                            islenecek_gorevler.append((str_id, str(aranan_kelime)))
                        else:
                            atlanan_mukerrer += 1
        except Exception:
            pass

    print(f"[*] Mükerrer veya zaten arşivde bulunan {atlanan_mukerrer} kayıt filtrelendi.")
    return islenecek_gorevler


def ana_dongu():
    """
    Ana işlem akışını yönetir: mevcut arşiv analizi, görev havuzunun oluşturulması
    ve çoklu iş parçacığıyla kararların indirilmesi.
    """
    baslangic_zamani = time.time()

    islenen_idler = mevcut_arsivi_tara_ve_idler_getir()
    print(f"[*] Arşivde halihazırda bulunan karar sayısı: {len(islenen_idler)}")

    if not os.path.exists(ARAMA_LISTE_KLASORU):
        print(f"[HATA] Arama listesi klasörü bulunamadı: {ARAMA_LISTE_KLASORU}")
        print("[BİLGİ] Önce 'danistay_kazima.py' betiğini çalıştırarak karar künyelerini çekiniz.")
        return

    json_dosyalari = glob.glob(os.path.join(ARAMA_LISTE_KLASORU, "*.json"))

    if not json_dosyalari:
        print("[HATA] Taranacak liste JSON dosyası bulunamadı.")
        print("[BİLGİ] Önce 'danistay_kazima.py' betiğini çalıştırarak liste dosyalarını oluşturunuz.")
        return

    print(f"[*] Toplam {len(json_dosyalari)} adet kaynak liste dosyası bulundu.")

    gorevler = gorevleri_topla(json_dosyalari, islenen_idler)
    toplam_gorev = len(gorevler)

    if toplam_gorev == 0:
        print("\n[BİLGİ] İndirilecek yeni karar bulunamadı, arşiv güncel.")
        return

    print(f"[*] İndirilecek toplam karar sayısı: {toplam_gorev}")
    print(f"[*] İndirme havuzu başlatılıyor (Eşzamanlı İş Parçacığı: {MAX_WORKERS})\n")

    basari_sayisi = 0
    hata_sayisi = 0
    islenen_toplam = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_id = {
            executor.submit(karar_indir_ve_kaydet, dokuman_id, kelime): (dokuman_id, kelime)
            for dokuman_id, kelime in gorevler
        }

        for future in concurrent.futures.as_completed(future_to_id):
            islenen_toplam += 1
            try:
                sonuc, did = future.result()
                if sonuc == "success":
                    basari_sayisi += 1
                    if islenen_toplam <= 5 or islenen_toplam % 50 == 0:
                        yuzde = (islenen_toplam / toplam_gorev) * 100
                        print(f"  [+] İndirildi (ID: {did}) | İlerleme: {islenen_toplam}/{toplam_gorev} (%{yuzde:.1f})")
                else:
                    hata_sayisi += 1
                    if islenen_toplam <= 5 or islenen_toplam % 50 == 0:
                        print(f"  [-] İstek Durumu [{sonuc}] (ID: {did}) | İlerleme: {islenen_toplam}/{toplam_gorev}")
            except Exception:
                hata_sayisi += 1

    gecen_sure = time.time() - baslangic_zamani
    zaman_farki = timedelta(seconds=int(gecen_sure))
    ortalama_hiz = (islenen_toplam / gecen_sure) if gecen_sure > 0 else 0

    print("\n------------------------------------------------------------")
    print("[BİLGİ] İndirme işlemi tamamlandı.")
    print(f"  - İşlenen Toplam Karar  : {islenen_toplam}")
    print(f"  - Başarılı             : {basari_sayisi}")
    print(f"  - Başarısız / Atlanan   : {hata_sayisi}")
    print(f"  - Geçen Süre            : {zaman_farki} ({gecen_sure:.2f} saniye)")
    print(f"  - Ortalama Hız          : {ortalama_hiz:.2f} karar/saniye")
    print(f"  - Arşiv Dizini          : {ARSIV_KOK_DIZINI}")
    print("------------------------------------------------------------")


if __name__ == "__main__":
    ana_dongu()
