# Kamu Kurumları Belge ve Rapor İndirici

Bu modül, kamu kurumlarının (Sayıştay, bakanlıklar, kamu denetçiliği kurumları vb.) web sitelerindeki rapor ve duyuru sayfalarını tarar; alt kategorileri keşfederek yayımlanan PDF belgelerini otomatik olarak yerel diske indirir.

## Çalışma Mantığı

1. **Dinamik Sayfa Yükleme (Headless Chromium):**
   JavaScript ile dinamik yüklenen kamu sitelerinin içeriklerini eksiksiz almak için Selenium ve başsız (headless) tarayıcı kullanılır.

2. **Otomatik Kategori ve Alt Kategori Keşfi:**
   Verilen başlangıç URL'lerindeki bağlantılar taranarak `/reports/category/` şablonuna uyan tüm alt kategori adresleri listeye eklenir.

3. **Sayfalama Desteği (Pagination):**
   Her kategori için belirtilen sayfa sayısınca `?page=X` parametresiyle gezinilir ve raporların detay sayfaları belirlenir.

4. **Doğrudan İndirme Bağlantısı Tespiti:**
   Detay sayfalarındaki `.pdf` veya indirme butonları ayrıştırılarak doğrudan dosya bağlantıları ayıklanır.

5. **Parçalı ve Kesinti Korumalı İndirme:**
   Belirlenen PDF dosyaları `urllib` üzerinden 64 KB'lık parçalar (chunks) halinde indirilir; zaman aşımı ve ağ kopmalarına karşı 3 aşamalı yeniden deneme mekanizması işletilir.

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
- `-o, --cikti`: PDF dosyalarının kaydedileceği hedef klasör yolu (varsayılan: `indirilen_pdfler`).

[Ana Dokümantasyona Dön](../README.md)
