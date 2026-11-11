"""
Kamu Kurumları Web Siteleri Toplu Rapor ve Belge İndirici

Bu modül, kamu kurumlarının (Sayıştay, bakanlıklar vb.) rapor ve duyuru
sayfalarını Selenium kullanarak tarar; kategori ve alt kategori sayfalarını dolaşıp
tespit ettiği PDF rapor dosyalarını yerel diske indirir.

Temel Özellikler:
- Sayfa gezintisi ve dinamik içerik yüklemeleri için headless Chromium desteği
- Verilen URL'lerdeki '/reports/category/' yapısındaki alt kategorileri otomatik keşfetme
- Sayfa numaralandırması (pagination) üzerinden sayfaları gezme
- Zaman aşımı ve ağ kesintilerine karşı yeniden deneme mekanizması
- Komut satırı (CLI) üzerinden esnek parametre yönetimi
"""

import argparse
import os
import time
import urllib.parse
import urllib.request
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By


def raporlari_tara_ve_indir(url_listesi, toplam_sayfa, cikti_klasoru):
    """
    Belirtilen ana kategori URL'lerini ve alt kategorilerini tarayarak
    bulunan tüm rapor detay sayfalarından PDF dosyalarını çeker.
    """
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
    taranacak_kategoriler = set()

    try:
        # 1. Aşama: Ana sayfalardan alt kategorilerin keşfi
        for ana_url in url_listesi:
            if not ana_url.startswith("http://") and not ana_url.startswith("https://"):
                ana_url = "https://" + ana_url

            print(f"\n[*] Ana kategori inceleniyor: {ana_url}")
            driver.get(ana_url)
            time.sleep(4)

            taranacak_kategoriler.add(ana_url)
            links = driver.find_elements(By.TAG_NAME, "a")
            for link in links:
                href = link.get_attribute("href")
                if href and "/reports/category/" in href:
                    alt_kategori_url = urllib.parse.urljoin(ana_url, href)
                    taranacak_kategoriler.add(alt_kategori_url)

        print(
            f"\n[*] Toplam {len(taranacak_kategoriler)} adet kategori (ana + alt) "
            "belirlendi. Raporlar taranıyor..."
        )

        # 2. Aşama: Kategori sayfalarındaki rapor bağlantılarının taranması
        rapor_linkleri = set()
        for kat_url in taranacak_kategoriler:
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
                            if tam_url not in rapor_linkleri:
                                rapor_linkleri.add(tam_url)
                                bulunan_sayfa += 1

                    if bulunan_sayfa > 0:
                        print(f"  [+] [Sayfa {sayfa}] {kat_url} -> {bulunan_sayfa} yeni rapor bulundu.")
                    else:
                        if sayfa > 1:
                            break
                except Exception as e:
                    print(f"  [-] Kategori taranamadı ({kat_url}): {e}")

        print(
            f"\n[*] Toplam {len(rapor_linkleri)} adet benzersiz rapor detay sayfası "
            "bulundu. Doğrudan PDF bağlantıları toplanıyor..."
        )

        # 3. Aşama: Rapor detay sayfalarından doğrudan PDF indirme bağlantılarının ayıklanması
        gercek_pdf_urls = []
        for i, rapor_url in enumerate(rapor_linkleri):
            print(f"  [{i+1}/{len(rapor_linkleri)}] Detay sayfası inceleniyor: {rapor_url}")
            try:
                driver.get(rapor_url)
                time.sleep(2)

                detay_links = driver.find_elements(By.TAG_NAME, "a")
                for d_link in detay_links:
                    d_href = d_link.get_attribute("href")
                    if d_href:
                        d_lower = d_href.lower()
                        if ".pdf" in d_lower or "pdf" in d_lower or "download" in d_lower:
                            pdf_url = urllib.parse.urljoin(rapor_url, d_href)
                            if pdf_url not in gercek_pdf_urls:
                                gercek_pdf_urls.append(pdf_url)
                                break
            except Exception as e:
                print(f"  [-] Detay sayfası okunamadı: {e}")

        print(f"\n[*] Toplam {len(gercek_pdf_urls)} adet PDF bağlantısı tespit edildi.")
        print("[*] İndirme işlemleri başlatılıyor...\n")

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        }

        # 4. Aşama: PDF dosyalarını parçalı okuma yöntemiyle diske kaydetme
        indirilen = 0
        for i, pdf_url in enumerate(gercek_pdf_urls):
            filename = os.path.basename(urllib.parse.urlparse(pdf_url).path)
            if not filename or not filename.endswith(".pdf"):
                filename = f"rapor_{i+1}.pdf"

            dosya_yolu = os.path.join(cikti_klasoru, filename)

            max_deneme = 3
            for deneme in range(1, max_deneme + 1):
                print(f"  [{i+1}/{len(gercek_pdf_urls)}] İndiriliyor: {filename} (Deneme {deneme}/{max_deneme})")
                try:
                    pdf_req = urllib.request.Request(pdf_url, headers=headers)
                    with urllib.request.urlopen(pdf_req, timeout=60) as response:
                        with open(dosya_yolu, "wb") as f:
                            while True:
                                chunk = response.read(1024 * 64)
                                if not chunk:
                                    break
                                f.write(chunk)

                    indirilen += 1
                    print(f"    -> Başarıyla kaydedildi: {filename}")
                    break
                except Exception as e:
                    print(f"    -> Hata oluştu: {e}")
                    if deneme < max_deneme:
                        time.sleep(3)
                    else:
                        print(f"    -> {filename} indirilemedi, atlanıyor.")

        print(f"\n[BİLGİ] İşlem tamamlandı. Toplam {indirilen} adet PDF '{cikti_klasoru}' klasörüne kaydedildi.")

    except Exception as e:
        print(f"[HATA] Kritik bir hata meydana geldi: {e}")
    finally:
        driver.quit()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Sayıştay ve benzeri kamu sitelerinden çoklu URL desteği ve "
            "alt kategori taramasıyla otomatik PDF rapor indirme aracı."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "KULLANIM ÖRNEKLERİ:\n"
            "  1) Tek kategori ile indirme:\n"
            "     python dosya_indirici.py -u https://www.sayistay.gov.tr/reports/category/13-faaliyet-genel-degerlendirme-raporlari -s 2 -o faaliyetler\n\n"
            "  2) Birden fazla adresi aynı anda tarama:\n"
            "     python dosya_indirici.py -u url1 url2 -s 3 -o indirilen_raporlar\n\n"
            "PARAMETRELER:\n"
            "  -u, --urls   : Taranacak web sitelerinin adresleri (boşluk bırakılarak birden fazla verilebilir)\n"
            "  -s, --sayfa  : Her kategori için taranacak maksimum sayfa sayısı (varsayılan: 1)\n"
            "  -o, --cikti  : PDF dosyalarının kaydedileceği hedef dizin (varsayılan: indirilen_pdfler)\n"
        ),
    )

    parser.add_argument(
        "-u",
        "--urls",
        nargs="+",
        required=True,
        help="Taranacak bir veya daha fazla sayfa adresi (boşlukla ayırarak giriniz)"
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
        help="PDF dosyalarının kaydedileceği klasör adı"
    )

    args = parser.parse_args()
    raporlari_tara_ve_indir(args.urls, args.sayfa, args.cikti)
