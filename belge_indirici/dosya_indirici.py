"""
Kamu Kurumları Belge ve PDF Rapor İndirici

Sayıştay ve benzeri kamu sitelerindeki kategori/rapor sayfalarını tarayarak
PDF dosyalarını ve kaynak/sayfa bilgilerini içeren metadata JSON kayıtlarını indirir.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import time
import urllib.parse
import urllib.request
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By


def metin_temizle(metin):
    if not metin:
        return ""
    return re.sub(r"\s+", " ", str(metin)).strip()


def slug_baslik_coz(url):
    if not url:
        return ""
    path = urllib.parse.urlparse(url).path.strip("/")
    if not path:
        return ""
    son_parca = urllib.parse.unquote(path.split("/")[-1])
    son_parca = re.sub(r"^\d+-", "", son_parca)
    return son_parca.replace("-", " ").strip().title()


def dosya_adi_etiketi_temizle(metin, maks_uzunluk=55):
    if not metin:
        return ""
    temiz = metin_temizle(metin)
    temiz = re.sub(r'[\\/*?:"<>|\[\]]', "", temiz)
    temiz = re.sub(r"[\s\-]+", "_", temiz).strip("._")
    if len(temiz) > maks_uzunluk:
        temiz = temiz[:maks_uzunluk].rstrip("._")
    return temiz


def kategorili_dosya_adi_olustur(orijinal_dosya_adi, pdf_kayit):
    kok, uzanti = os.path.splitext(orijinal_dosya_adi)
    if not uzanti:
        uzanti = ".pdf"

    kurum_raw = (pdf_kayit.get("kaynak_domain") or "").replace("www.", "")
    kurum_etiket = dosya_adi_etiketi_temizle(kurum_raw, maks_uzunluk=30)

    kategori_raw = (
        pdf_kayit.get("kategori_adi")
        or pdf_kayit.get("belge_bilgisi", {}).get("kategori")
        or "Genel_Rapor"
    )
    kategori_etiket = dosya_adi_etiketi_temizle(kategori_raw, maks_uzunluk=55)

    tarih_raw = pdf_kayit.get("belge_bilgisi", {}).get("yayin_veya_tespit_tarihi") or ""
    tarih_etiket = dosya_adi_etiketi_temizle(tarih_raw, maks_uzunluk=20)

    etiket_parcalari = [p for p in (kurum_etiket, kategori_etiket, tarih_etiket) if p]
    if etiket_parcalari:
        kategori_eki = "__[" + "__".join(etiket_parcalari) + "]"
    else:
        kategori_eki = "__[Genel_Kategori]"

    return f"{kok}{kategori_eki}{uzanti}"


def sayfa_metadatasini_cikar(driver):
    sayfa_basligi = metin_temizle(driver.title)
    ana_baslik = ""
    aciklama = ""
    yayin_tarihi = ""
    meta_etiketleri = {}

    for tag in ("h1", "h2", "h3"):
        try:
            elems = driver.find_elements(By.TAG_NAME, tag)
            for el in elems:
                txt = metin_temizle(el.text)
                if txt and len(txt) > 3:
                    ana_baslik = txt
                    break
            if ana_baslik:
                break
        except Exception:
            pass

    try:
        metas = driver.find_elements(By.TAG_NAME, "meta")
        for m in metas:
            name = (m.get_attribute("name") or m.get_attribute("property") or "").strip().lower()
            content = metin_temizle(m.get_attribute("content") or "")
            if name and content:
                if name in (
                    "description",
                    "og:description",
                    "og:title",
                    "og:site_name",
                    "keywords",
                    "author",
                    "article:published_time",
                    "publish_date",
                    "date",
                ):
                    meta_etiketleri[name] = content
    except Exception:
        pass

    if not ana_baslik:
        ana_baslik = meta_etiketleri.get("og:title") or sayfa_basligi

    try:
        p_elems = driver.find_elements(By.TAG_NAME, "p")
        paragraflar = []
        for p in p_elems:
            p_txt = metin_temizle(p.text)
            if len(p_txt) > 25:
                paragraflar.append(p_txt)
            if len(paragraflar) >= 3:
                break
        if paragraflar:
            aciklama = " ".join(paragraflar)
    except Exception:
        pass

    if not aciklama:
        aciklama = meta_etiketleri.get("description") or meta_etiketleri.get("og:description") or ""

    try:
        time_elems = driver.find_elements(By.TAG_NAME, "time")
        for t_el in time_elems:
            dt_val = metin_temizle(t_el.get_attribute("datetime") or t_el.text)
            if dt_val:
                yayin_tarihi = dt_val
                break
    except Exception:
        pass

    if not yayin_tarihi:
        yayin_tarihi = meta_etiketleri.get("article:published_time") or meta_etiketleri.get("date") or ""

    if not yayin_tarihi:
        try:
            body_text = driver.find_element(By.TAG_NAME, "body").text
            tarih_eslesme = re.search(r"\b(\d{2}[./]\d{2}[./]\d{4}|\d{4}-\d{2}-\d{2})\b", body_text)
            if tarih_eslesme:
                yayin_tarihi = tarih_eslesme.group(1)
        except Exception:
            pass

    return {
        "rapor_basligi": ana_baslik,
        "sayfa_basligi": sayfa_basligi,
        "aciklama": aciklama,
        "tespit_edilen_tarih": yayin_tarihi,
        "html_meta_etiketleri": meta_etiketleri,
    }


def raporlari_tara_ve_indir(url_listesi, toplam_sayfa, cikti_klasoru):
    if not os.path.exists(cikti_klasoru):
        os.makedirs(cikti_klasoru, exist_ok=True)
        print(f"[*] Çıktı klasörü oluşturuldu: {cikti_klasoru}")

    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument(
        "User-Agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )

    driver = webdriver.Chrome(options=chrome_options)
    taranacak_kategoriler = {}

    try:
        for ana_url in url_listesi:
            if not ana_url.startswith("http://") and not ana_url.startswith("https://"):
                ana_url = "https://" + ana_url

            print(f"\n[*] Ana kategori inceleniyor: {ana_url}")
            driver.get(ana_url)
            time.sleep(4)

            ana_baslik = metin_temizle(driver.title) or slug_baslik_coz(ana_url)
            taranacak_kategoriler[ana_url] = {
                "ana_kaynak_url": ana_url,
                "kategori_url": ana_url,
                "kategori_adi": slug_baslik_coz(ana_url) or ana_baslik,
            }

            links = driver.find_elements(By.TAG_NAME, "a")
            for link in links:
                href = link.get_attribute("href")
                if href and "/reports/category/" in href:
                    alt_kategori_url = urllib.parse.urljoin(ana_url, href)
                    link_metni = metin_temizle(link.text) or slug_baslik_coz(alt_kategori_url)
                    if alt_kategori_url not in taranacak_kategoriler:
                        taranacak_kategoriler[alt_kategori_url] = {
                            "ana_kaynak_url": ana_url,
                            "kategori_url": alt_kategori_url,
                            "kategori_adi": link_metni,
                        }

        print(
            f"\n[*] Toplam {len(taranacak_kategoriler)} adet kategori (ana + alt) "
            "belirlendi. Raporlar taranıyor..."
        )

        rapor_kayitlari = {}
        for kat_url, kat_bilgi in taranacak_kategoriler.items():
            for sayfa in range(1, toplam_sayfa + 1):
                parsed_url = urllib.parse.urlparse(kat_url)
                query_params = urllib.parse.parse_qs(parsed_url.query)
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

                try:
                    driver.get(current_url)
                    time.sleep(3)

                    links = driver.find_elements(By.TAG_NAME, "a")
                    bulunan_sayfa = 0
                    for link in links:
                        href = link.get_attribute("href")
                        if href and "/reports/" in href and "category" not in href:
                            tam_url = urllib.parse.urljoin(current_url, href)
                            if tam_url not in rapor_kayitlari:
                                liste_link_basligi = metin_temizle(link.text) or metin_temizle(
                                    link.get_attribute("title")
                                )
                                rapor_kayitlari[tam_url] = {
                                    "kaynak_domain": parsed_url.netloc,
                                    "ana_giris_url": kat_bilgi["ana_kaynak_url"],
                                    "kategori_url": kat_url,
                                    "kategori_adi": kat_bilgi["kategori_adi"] or slug_baslik_coz(kat_url),
                                    "bulundugu_liste_sayfasi_url": current_url,
                                    "bulundugu_sayfa_no": sayfa,
                                    "rapor_detay_sayfasi_url": tam_url,
                                    "liste_link_metni": liste_link_basligi,
                                }
                                bulunan_sayfa += 1

                    if bulunan_sayfa > 0:
                        print(f"  [+] [Sayfa {sayfa}] {kat_url} -> {bulunan_sayfa} yeni rapor bulundu.")
                    else:
                        if sayfa > 1:
                            break
                except Exception as e:
                    print(f"  [-] Kategori taranamadı ({kat_url}): {e}")

        print(
            f"\n[*] Toplam {len(rapor_kayitlari)} adet rapor detay sayfası "
            "bulundu. PDF bağlantıları ve sayfa bilgileri toplanıyor..."
        )

        gercek_pdf_kayitlari = []
        gorulen_pdf_urls = set()

        for i, (rapor_url, kaynak_meta) in enumerate(rapor_kayitlari.items()):
            print(f"  [{i+1}/{len(rapor_kayitlari)}] Detay sayfası inceleniyor: {rapor_url}")
            try:
                driver.get(rapor_url)
                time.sleep(2)

                sayfa_meta = sayfa_metadatasini_cikar(driver)
                detay_links = driver.find_elements(By.TAG_NAME, "a")

                for d_link in detay_links:
                    d_href = d_link.get_attribute("href")
                    if d_href:
                        d_lower = d_href.lower()
                        if ".pdf" in d_lower or "pdf" in d_lower or "download" in d_lower:
                            pdf_url = urllib.parse.urljoin(rapor_url, d_href)
                            if pdf_url not in gorulen_pdf_urls:
                                gorulen_pdf_urls.add(pdf_url)
                                indirme_buton_metni = metin_temizle(d_link.text) or metin_temizle(
                                    d_link.get_attribute("title")
                                )
                                kayit = {
                                    **kaynak_meta,
                                    "pdf_indirme_url": pdf_url,
                                    "indirme_link_metni": indirme_buton_metni,
                                    "belge_bilgisi": {
                                        "rapor_basligi": (
                                            sayfa_meta["rapor_basligi"]
                                            or kaynak_meta["liste_link_metni"]
                                            or slug_baslik_coz(rapor_url)
                                        ),
                                        "sayfa_basligi": sayfa_meta["sayfa_basligi"],
                                        "aciklama": sayfa_meta["aciklama"],
                                        "yayin_veya_tespit_tarihi": sayfa_meta["tespit_edilen_tarih"],
                                        "kategori": kaynak_meta["kategori_adi"],
                                        "kurum_domain": kaynak_meta["kaynak_domain"],
                                        "html_meta_etiketleri": sayfa_meta["html_meta_etiketleri"],
                                    },
                                }
                                gercek_pdf_kayitlari.append(kayit)
                                break
            except Exception as e:
                print(f"  [-] Detay sayfası okunamadı: {e}")

        print(f"\n[*] Toplam {len(gercek_pdf_kayitlari)} adet PDF bağlantısı tespit edildi.")
        print("[*] İndirme işlemi başlatılıyor...\n")

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        }

        indirilen = 0
        tum_metadatalar = []
        kullanilan_dosya_adlari = set()

        for i, pdf_kayit in enumerate(gercek_pdf_kayitlari):
            pdf_url = pdf_kayit["pdf_indirme_url"]
            raw_filename = urllib.parse.unquote(os.path.basename(urllib.parse.urlparse(pdf_url).path))
            raw_filename = re.sub(r'[\\/*?:"<>|]', "_", raw_filename).strip()

            if not raw_filename or not raw_filename.lower().endswith(".pdf"):
                aday_isim = slug_baslik_coz(pdf_kayit["rapor_detay_sayfasi_url"]).replace(" ", "_").lower()
                aday_isim = re.sub(r'[\\/*?:"<>|]', "_", aday_isim)
                raw_filename = f"{aday_isim or 'rapor'}_{i+1}.pdf"

            orijinal_dosya_adi = raw_filename
            kategorili_isim = kategorili_dosya_adi_olustur(orijinal_dosya_adi, pdf_kayit)

            filename = kategorili_isim
            if filename in kullanilan_dosya_adlari:
                kok, uzanti = os.path.splitext(kategorili_isim)
                filename = f"{kok}_{i+1}{uzanti}"
            kullanilan_dosya_adlari.add(filename)

            dosya_yolu = os.path.join(cikti_klasoru, filename)
            metadata_dosya_adi = f"{filename}.metadata.json"
            metadata_dosya_yolu = os.path.join(cikti_klasoru, metadata_dosya_adi)

            if os.path.exists(dosya_yolu) and os.path.exists(metadata_dosya_yolu):
                try:
                    with open(metadata_dosya_yolu, "r", encoding="utf-8") as mf:
                        mevcut_meta = json.load(mf)
                    tum_metadatalar.append(mevcut_meta)
                    indirilen += 1
                    print(f"  [{i+1}/{len(gercek_pdf_kayitlari)}] Zaten mevcut, atlanıyor: {filename}")
                    continue
                except Exception:
                    pass

            max_deneme = 3
            for deneme in range(1, max_deneme + 1):
                print(f"  [{i+1}/{len(gercek_pdf_kayitlari)}] İndiriliyor: {filename} (Deneme {deneme}/{max_deneme})")
                try:
                    pdf_req = urllib.request.Request(pdf_url, headers=headers)
                    sha256_hash = hashlib.sha256()
                    toplam_boyut = 0

                    with urllib.request.urlopen(pdf_req, timeout=60) as response:
                        http_headers = dict(response.info())
                        content_type = http_headers.get("Content-Type", "application/pdf")
                        last_modified = http_headers.get("Last-Modified", "")
                        content_disposition = http_headers.get("Content-Disposition", "")

                        with open(dosya_yolu, "wb") as f:
                            while True:
                                chunk = response.read(1024 * 64)
                                if not chunk:
                                    break
                                f.write(chunk)
                                sha256_hash.update(chunk)
                                toplam_boyut += len(chunk)

                    tam_metadata = {
                        "dosya_adi": filename,
                        "orijinal_dosya_adi": orijinal_dosya_adi,
                        "dosya_yolu": os.path.abspath(dosya_yolu),
                        "dosya_turu": "PDF",
                        "mime_type": content_type,
                        "dosya_boyutu_byte": toplam_boyut,
                        "sha256": sha256_hash.hexdigest(),
                        "sunucu_son_degisiklik_tarihi": last_modified,
                        "content_disposition": content_disposition,
                        "indirme_zamani_utc": datetime.now(timezone.utc).isoformat(),
                        "nereden_geldi": {
                            "kurum_domain": pdf_kayit["kaynak_domain"],
                            "ana_giris_url": pdf_kayit["ana_giris_url"],
                            "kategori_url": pdf_kayit["kategori_url"],
                            "kategori_adi": pdf_kayit["kategori_adi"],
                            "bulundugu_liste_sayfasi_url": pdf_kayit["bulundugu_liste_sayfasi_url"],
                            "bulundugu_sayfa_no": pdf_kayit["bulundugu_sayfa_no"],
                            "rapor_detay_sayfasi_url": pdf_kayit["rapor_detay_sayfasi_url"],
                            "pdf_indirme_url": pdf_url,
                        },
                        "ne_oldugu": {
                            **pdf_kayit["belge_bilgisi"],
                            "liste_link_metni": pdf_kayit["liste_link_metni"],
                            "indirme_link_metni": pdf_kayit["indirme_link_metni"],
                        },
                    }

                    with open(metadata_dosya_yolu, "w", encoding="utf-8") as mf:
                        json.dump(tam_metadata, mf, ensure_ascii=False, indent=4)

                    tum_metadatalar.append(tam_metadata)
                    indirilen += 1
                    print(f"    -> Kaydedildi: {filename} (+ {metadata_dosya_adi})")
                    break
                except Exception as e:
                    print(f"    -> Hata oluştu: {e}")
                    if deneme < max_deneme:
                        time.sleep(3)
                    else:
                        print(f"    -> {filename} indirilemedi, atlanıyor.")

        katalog_yolu = os.path.join(cikti_klasoru, "indirilen_dosyalar_metadata.json")
        with open(katalog_yolu, "w", encoding="utf-8") as kf:
            json.dump(
                {
                    "toplam_indirilen_dosya": indirilen,
                    "olusturulma_zamani_utc": datetime.now(timezone.utc).isoformat(),
                    "taranan_ana_urller": url_listesi,
                    "dosyalar": tum_metadatalar,
                },
                kf,
                ensure_ascii=False,
                indent=4,
            )

        print(
            f"\n[BİLGİ] İşlem tamamlandı. Toplam {indirilen} adet PDF ve metadata dosyası "
            f"'{cikti_klasoru}' klasörüne kaydedildi."
        )
        print(f"[BİLGİ] Toplu metadata kataloğu: {katalog_yolu}")

    except Exception as e:
        print(f"[HATA] Kritik bir hata meydana geldi: {e}")
    finally:
        driver.quit()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Sayıştay ve benzeri kamu sitelerinden PDF rapor ve metadata indirme aracı.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Örnek kullanım:\n"
            "  python dosya_indirici.py -u https://www.sayistay.gov.tr/reports/category/13-faaliyet-genel-degerlendirme-raporlari -s 2 -o faaliyetler\n"
            "  python dosya_indirici.py -u url1 url2 -s 3 -o indirilen_raporlar\n"
        ),
    )

    parser.add_argument(
        "-u",
        "--urls",
        nargs="+",
        required=True,
        help="Taranacak sayfa adresleri"
    )
    parser.add_argument(
        "-s",
        "--sayfa",
        type=int,
        default=1,
        help="Her kategori için taranacak sayfa sayısı (varsayılan: 1)"
    )
    parser.add_argument(
        "-o",
        "--cikti",
        default="indirilen_pdfler",
        help="PDF ve metadata dosyalarının kaydedileceği klasör adı"
    )

    args = parser.parse_args()
    raporlari_tara_ve_indir(args.urls, args.sayfa, args.cikti)
