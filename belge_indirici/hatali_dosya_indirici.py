"""
Kamu Kurumları Hatalı / Eksik Belge ve PDF Rapor İndirici

dosya_indirici.py tarafından oluşturulan hata.json içindeki:
1) İndirme hatalarını (indirme_hatalari),
2) Detay sayfası okuma hatalarını (detay_sayfasi_hatalari),
3) Kategori sayfası tarama hatalarını (kategori_hatalari)
yeniden dener. Başarılı olanları indirip hata.json'dan siler ve
indirilen_dosyalar_metadata.json kataloğuna ekler.
"""

import argparse
import os
import time
import urllib.parse
from typing import List, Dict, Any
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

from dosya_indirici import (
    HataKaydedici,
    ham_sayfa_verisini_cikar,
    pdf_baglantisi_mi,
    slug_baslik_coz,
    tek_pdf_indir_ve_kaydet,
    katalog_guncelle,
)


def hatali_belgeleri_yeniden_indir(
    cikti_klasoru: str,
    include_404: bool = False,
    kategori_kalibi: str = "/reports/category/",
    detay_kalibi: str = "/reports/"
):
    if not os.path.exists(cikti_klasoru):
        print(f"[HATA] Çıktı klasörü bulunamadı: {cikti_klasoru}")
        return

    hata_kaydedici = HataKaydedici(cikti_klasoru)
    if not os.path.exists(hata_kaydedici.hata_dosyasi):
        print(f"[BİLGİ] '{hata_kaydedici.hata_dosyasi}' bulunamadı. Yeniden denenecek hata yok.")
        return

    hata_verisi = hata_kaydedici._oku()
    kat_hatalari: Dict[str, Any] = dict(hata_verisi.get("kategori_hatalari", {}))
    detay_hatalari: Dict[str, Any] = dict(hata_verisi.get("detay_sayfasi_hatalari", {}))
    indirme_hatalari: Dict[str, Any] = dict(hata_verisi.get("indirme_hatalari", {}))

    if not kat_hatalari and not detay_hatalari and not indirme_hatalari:
        print("[BİLGİ] hata.json içinde yeniden denenecek kayıt bulunmuyor.")
        return

    print("=" * 70)
    print("Belge İndirici - Hatalı Kayıtları Yeniden Deneme Başlatıldı")
    print(f"Kategori Hataları : {len(kat_hatalari)}")
    print(f"Detay Hataları    : {len(detay_hatalari)}")
    print(f"İndirme Hataları  : {len(indirme_hatalari)}")
    print("=" * 70)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }

    kurtarilan_metadatalar: List[Dict[str, Any]] = []
    yeni_pdf_kayitlari: List[Dict[str, Any]] = []
    gorulen_pdf_urls = set()

    if kat_hatalari or detay_hatalari:
        chrome_options = Options()
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument(f"User-Agent={headers['User-Agent']}")
        driver = webdriver.Chrome(options=chrome_options)

        try:
            for idx, (kat_key, kat_info) in enumerate(kat_hatalari.items(), start=1):
                kat_url = kat_info.get("url") or kat_key.split("#page=")[0]
                sayfa = int(kat_info.get("sayfa_no") or 1)
                onceki_deneme = kat_info.get("deneme_sayisi", 1)
                kat_bilgi = kat_info.get("kategori_bilgisi") or {
                    "ana_kaynak_url": kat_url,
                    "kategori_url": kat_url,
                    "kategori_adi": slug_baslik_coz(kat_url),
                }

                parsed_url = urllib.parse.urlparse(kat_url)
                query_params = urllib.parse.parse_qs(parsed_url.query)
                if sayfa > 1 or "page" in query_params:
                    query_params["page"] = [str(sayfa)]
                new_query = urllib.parse.urlencode(query_params, doseq=True)
                current_url = urllib.parse.urlunparse((
                    parsed_url.scheme,
                    parsed_url.netloc,
                    parsed_url.path,
                    parsed_url.params,
                    new_query,
                    parsed_url.fragment,
                ))

                print(f"[Kategori {idx}/{len(kat_hatalari)}] Yeniden taranıyor (Sayfa {sayfa}, Önceki deneme: {onceki_deneme}): {current_url}")
                try:
                    driver.get(current_url)
                    time.sleep(3)

                    sayfa_meta, sayfa_raw_html = ham_sayfa_verisini_cikar(driver)
                    links = driver.find_elements(By.TAG_NAME, "a")
                    bulunan_detay = 0
                    bulunan_pdf = 0

                    for link in links:
                        try:
                            href = link.get_attribute("href")
                        except Exception:
                            continue
                        if not href:
                            continue

                        tam_url = urllib.parse.urljoin(current_url, href)

                        if pdf_baglantisi_mi(link, tam_url):
                            if tam_url not in gorulen_pdf_urls:
                                gorulen_pdf_urls.add(tam_url)
                                yeni_pdf_kayitlari.append({
                                    "kaynak_domain": parsed_url.netloc,
                                    "ana_giris_url": kat_bilgi.get("ana_kaynak_url", kat_url),
                                    "kategori_url": kat_url,
                                    "kategori_adi": kat_bilgi.get("kategori_adi") or slug_baslik_coz(kat_url),
                                    "bulundugu_liste_sayfasi_url": current_url,
                                    "bulundugu_sayfa_no": sayfa,
                                    "rapor_detay_sayfasi_url": current_url,
                                    "liste_link_metni": link.text,
                                    "liste_link_title_attr": link.get_attribute("title"),
                                    "pdf_indirme_url": tam_url,
                                    "indirme_link_metni": link.text,
                                    "indirme_link_title_attr": link.get_attribute("title"),
                                    "belge_bilgisi": sayfa_meta,
                                    "_detay_raw_html": sayfa_raw_html,
                                })
                                bulunan_pdf += 1
                            continue

                        if detay_kalibi and detay_kalibi in href:
                            if kategori_kalibi and kategori_kalibi in href:
                                continue
                            if tam_url not in detay_hatalari:
                                detay_hatalari[tam_url] = {
                                    "rapor_detay_sayfasi_url": tam_url,
                                    "deneme_sayisi": 0,
                                    "kaynak_meta": {
                                        "kaynak_domain": parsed_url.netloc,
                                        "ana_giris_url": kat_bilgi.get("ana_kaynak_url", kat_url),
                                        "kategori_url": kat_url,
                                        "kategori_adi": kat_bilgi.get("kategori_adi") or slug_baslik_coz(kat_url),
                                        "bulundugu_liste_sayfasi_url": current_url,
                                        "bulundugu_sayfa_no": sayfa,
                                        "rapor_detay_sayfasi_url": tam_url,
                                        "liste_link_metni": link.text,
                                        "liste_link_title_attr": link.get_attribute("title"),
                                    }
                                }
                                bulunan_detay += 1

                    hata_kaydedici.kategori_hatasi_sil(kat_url, sayfa)
                    print(f"  [+] Kategori sayfası kurtarıldı ({bulunan_detay} detay sayfası, {bulunan_pdf} doğrudan PDF bulundu).")
                except Exception as e:
                    err_msg = f"{type(e).__name__}: {e}"
                    print(f"  [-] Kategori sayfası yine okunamadı (Deneme #{onceki_deneme + 1}): {err_msg}")
                    hata_kaydedici.kategori_hatasi_ekle(kat_url, sayfa, err_msg, kat_bilgi=kat_bilgi)

            for idx, (rapor_url, info) in enumerate(detay_hatalari.items(), start=1):
                onceki_deneme = info.get("deneme_sayisi", 1)
                kaynak_meta = info.get("kaynak_meta") or {
                    "kaynak_domain": urllib.parse.urlparse(rapor_url).netloc,
                    "rapor_detay_sayfasi_url": rapor_url,
                }
                print(f"[Detay {idx}/{len(detay_hatalari)}] Yeniden inceleniyor (Önceki deneme: {onceki_deneme}): {rapor_url}")
                try:
                    driver.get(rapor_url)
                    time.sleep(2)
                    sayfa_meta, raw_html = ham_sayfa_verisini_cikar(driver)
                    detay_links = driver.find_elements(By.TAG_NAME, "a")
                    bulunan = 0
                    for d_link in detay_links:
                        try:
                            d_href = d_link.get_attribute("href")
                        except Exception:
                            continue
                        if not d_href:
                            continue
                        pdf_url = urllib.parse.urljoin(rapor_url, d_href)
                        if pdf_baglantisi_mi(d_link, pdf_url):
                            if pdf_url not in gorulen_pdf_urls:
                                gorulen_pdf_urls.add(pdf_url)
                                yeni_pdf_kayitlari.append({
                                    **kaynak_meta,
                                    "pdf_indirme_url": pdf_url,
                                    "indirme_link_metni": d_link.text,
                                    "indirme_link_title_attr": d_link.get_attribute("title"),
                                    "belge_bilgisi": sayfa_meta,
                                    "_detay_raw_html": raw_html,
                                })
                            bulunan += 1
                    if bulunan > 0:
                        hata_kaydedici.detay_hatasi_sil(rapor_url)
                        print(f"  [+] Detay sayfası kurtarıldı ({bulunan} PDF bulundu).")
                    else:
                        hata_kaydedici.detay_hatasi_ekle(
                            rapor_url,
                            "Detay sayfasında geçerli bir PDF bağlantısı bulunamadı.",
                            kaynak_meta=kaynak_meta
                        )
                except Exception as e:
                    err_msg = f"{type(e).__name__}: {e}"
                    print(f"  [-] Detay sayfası yine okunamadı (Deneme #{onceki_deneme + 1}): {err_msg}")
                    hata_kaydedici.detay_hatasi_ekle(rapor_url, err_msg, kaynak_meta=kaynak_meta)
        finally:
            try:
                driver.quit()
            except Exception:
                pass

    atlanan_kalici = 0
    for pdf_url, info in indirme_hatalari.items():
        if pdf_url in gorulen_pdf_urls:
            continue
        kalici = bool(info.get("kalici_hata")) or (info.get("http_kodu") == 404)
        if kalici and not include_404:
            atlanan_kalici += 1
            continue
        gorulen_pdf_urls.add(pdf_url)
        pdf_kayit = info.get("pdf_kayit") or {
            "pdf_indirme_url": pdf_url,
            "kaynak_domain": urllib.parse.urlparse(pdf_url).netloc,
        }
        pdf_kayit["pdf_indirme_url"] = pdf_url
        yeni_pdf_kayitlari.append(pdf_kayit)

    if atlanan_kalici > 0:
        print(f"[*] Kalıcı hata (HTTP 404) olarak işaretlenmiş {atlanan_kalici} PDF atlandı (--include-404 ile zorlanabilir).")

    print(f"\n[*] Toplam {len(yeni_pdf_kayitlari)} adet PDF indirmesi yeniden deneniyor...\n")

    for i, pdf_kayit in enumerate(yeni_pdf_kayitlari, start=1):
        sira_str = f"[{i}/{len(yeni_pdf_kayitlari)}]"
        meta = tek_pdf_indir_ve_kaydet(
            pdf_kayit=pdf_kayit,
            cikti_klasoru=cikti_klasoru,
            hata_kaydedici=hata_kaydedici,
            headers=headers,
            sira_bilgisi=sira_str
        )
        if meta:
            kurtarilan_metadatalar.append(meta)

    katalog_yolu, toplam_katalog = katalog_guncelle(cikti_klasoru, kurtarilan_metadatalar, [])
    print("=" * 70)
    print(f"[BİLGİ] Yeniden deneme tamamlandı. Kurtarılan PDF: {len(kurtarilan_metadatalar)} | Katalog Toplamı: {toplam_katalog}")
    print(f"[BİLGİ] Katalog: {katalog_yolu}")
    print("=" * 70)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hatalı / Eksik PDF Raporlarını Yeniden İndirme Aracı")
    parser.add_argument("-o", "--cikti", default="indirilen_pdfler", help="hata.json dosyasının bulunduğu çıktı klasörü")
    parser.add_argument("--include-404", action="store_true", help="HTTP 404 (kalıcı hata) almış PDF'leri de tekrar dene")
    parser.add_argument(
        "--category-pattern",
        default="/reports/category/",
        help="Alt kategori linklerini tespit etmek için URL alt dizgesi (varsayılan: /reports/category/)"
    )
    parser.add_argument(
        "--detail-pattern",
        default="/reports/",
        help="Rapor detay sayfalarını tespit etmek için URL alt dizgesi (varsayılan: /reports/)"
    )

    args = parser.parse_args()
    hatali_belgeleri_yeniden_indir(
        cikti_klasoru=args.cikti,
        include_404=args.include_404,
        kategori_kalibi=args.category_pattern,
        detay_kalibi=args.detail_pattern
    )
