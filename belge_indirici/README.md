# Kamu Kurumları Belge ve Rapor İndirici

Bu modül, kamu kurumlarının (Sayıştay, bakanlıklar, kamu denetçiliği kurumları vb.) web sitelerindeki rapor ve duyuru sayfalarını tarar; alt kategorileri keşfederek yayımlanan PDF belgelerini ve her belgenin nereden geldiğini ve ne olduğunu açıklayan kapsamlı üstverilerini (metadata) otomatik olarak yerel diske indirir.

## Çalışma Mantığı

1. **Dinamik Sayfa Yükleme (Headless Chromium):**
   JavaScript ile dinamik yüklenen kamu sitelerinin içeriklerini eksiksiz almak için Selenium ve başsız (headless) tarayıcı kullanılır.

2. **Otomatik Kategori ve Alt Kategori Keşfi:**
   Verilen başlangıç URL'lerindeki bağlantılar taranarak `/reports/category/` şablonuna uyan tüm alt kategori adresleri ve kategori isimleri listeye eklenir.

3. **Sayfalama Desteği (Pagination):**
   Her kategori için belirtilen sayfa sayısınca `?page=X` parametresiyle gezinilir; raporların detay sayfaları ve bulundukları liste sayfası bilgileri kayıt altına alınır.

4. **Doğrudan İndirme Bağlantısı ve Sayfa Metadatası Tespiti:**
   Detay sayfalarındaki `.pdf` veya indirme butonları ayrıştırılarak doğrudan dosya bağlantıları ayıklanır. Aynı anda detay sayfasından rapor başlığı, açıklama metni, tespit edilen yayın tarihi ve HTML meta etiketleri (`og:title`, `description` vb.) toplanır.

5. **Parçalı İndirme, Kategorili İsimlendirme ve Metadata Kaydı:**
   Belirlenen PDF dosyaları `urllib` üzerinden 64 KB'lık parçalar (chunks) halinde indirilir; zaman aşımı ve ağ kopmalarına karşı 3 aşamalı yeniden deneme mekanizması işletilir.
   - **Orijinal Adın Yanına Kategorize Edici Bilgi Ekleme:** İndirilen her dosyanın orijinal adı olduğu gibi korunur ve hemen yanına kurum domaini, kategori adı ve tespit edilen tarih bilgisi eklenir (`<orijinal_ad>__[<kurum>__<kategori>__<tarih>].pdf`).
   - **`<dosya_adi>.pdf.metadata.json`**: Dosyanın bulunduğu klasörün içine, nereden geldiğini (`nereden_geldi`) ve ne olduğunu (`ne_oldugu`), orijinal ve kategorili dosya adını, dosya boyutunu, MIME türünü ve SHA-256 özetini içeren bireysel metadata JSON dosyası oluşturulur.
   - **`indirilen_dosyalar_metadata.json`**: Çıktı klasöründe indirilen tüm belgelerin üstverilerini tek bir yerde toplayan ana katalog dosyası oluşturulur.

## Gereksinimler

- Python 3.8+
- Google Chrome veya Chromium
- `selenium`

Kurulum:
```bash
pip install selenium
```

Ayrıca sisteminizde yüklü Chrome sürümüyle uyumlu `chromedriver` bulunmalıdır (Selenium 4+ bunu genellikle otomatik yönetir).

## Kullanım Parametreleri

Komut satırı üzerinden esnek şekilde çalıştırılabilir:

```bash
# Tek bir kategori sayfasını 2 sayfa derinliğinde tarayıp 'faaliyet_raporlari' klasörüne indirme
python dosya_indirici.py -u https://www.sayistay.gov.tr/reports/category/13-faaliyet-genel-degerlendirme-raporlari -s 2 -o faaliyet_raporlari

# Birden fazla adresi aynı anda tarama
python dosya_indirici.py -u URL1 URL2 -s 3 -o indirilen_pdfler
```

### Parametre Açıklamaları

- `-u, --urls`: Taranacak kategori bağlantıları (aralarında boşluk bırakarak birden fazla girilebilir).
- `-s, --sayfa`: Her kategori için taranacak sayfa adedi (varsayılan: 1).
- `-o, --cikti`: PDF ve metadata dosyalarının kaydedileceği hedef klasör yolu (varsayılan: `indirilen_pdfler`).

## Metadata JSON Formatı (`<dosya_adi>.pdf.metadata.json`)

```json
{
    "dosya_adi": "2023_faaliyet_raporu__[sayistay.gov.tr__Faaliyet_Genel_Degerlendirme_Raporlari__15.05.2024].pdf",
    "orijinal_dosya_adi": "2023_faaliyet_raporu.pdf",
    "dosya_yolu": "C:/.../faaliyet_raporlari/2023_faaliyet_raporu__[sayistay.gov.tr__Faaliyet_Genel_Degerlendirme_Raporlari__15.05.2024].pdf",
    "dosya_turu": "PDF",
    "mime_type": "application/pdf",
    "dosya_boyutu_byte": 1548290,
    "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "sunucu_son_degisiklik_tarihi": "Wed, 15 May 2024 10:20:30 GMT",
    "content_disposition": "",
    "indirme_zamani_utc": "2026-11-25T10:40:00.000000+00:00",
    "nereden_geldi": {
        "kurum_domain": "www.sayistay.gov.tr",
        "ana_giris_url": "https://www.sayistay.gov.tr/reports/category/13-faaliyet-genel-degerlendirme-raporlari",
        "kategori_url": "https://www.sayistay.gov.tr/reports/category/13-faaliyet-genel-degerlendirme-raporlari",
        "kategori_adi": "Faaliyet Genel Degerlendirme Raporlari",
        "bulundugu_liste_sayfasi_url": "https://www.sayistay.gov.tr/reports/category/13-faaliyet-genel-degerlendirme-raporlari?page=1",
        "bulundugu_sayfa_no": 1,
        "rapor_detay_sayfasi_url": "https://www.sayistay.gov.tr/reports/1234-2023-yili-faaliyet-raporu",
        "pdf_indirme_url": "https://www.sayistay.gov.tr/reports/download/1234"
    },
    "ne_oldugu": {
        "rapor_basligi": "2023 Yılı Faaliyet Genel Değerlendirme Raporu",
        "sayfa_basligi": "Sayıştay Başkanlığı - 2023 Yılı Faaliyet Genel Değerlendirme Raporu",
        "aciklama": "Rapor detay sayfasından çıkarılan özet ve açıklama metni...",
        "yayin_veya_tespit_tarihi": "15.05.2024",
        "kategori": "Faaliyet Genel Degerlendirme Raporlari",
        "kurum_domain": "www.sayistay.gov.tr",
        "html_meta_etiketleri": {},
        "liste_link_metni": "2023 Yılı Faaliyet Genel Değerlendirme Raporu",
        "indirme_link_metni": "PDF İndir"
    }
}
```

[Ana Dokümantasyona Dön](../README.md)
