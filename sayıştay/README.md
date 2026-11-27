# Sayıştay Rapor ve Belge İndirici (`wget` + `dosya_indirici.py`)

Sayıştay Başkanlığı (`sayistay.gov.tr`) web sitesindeki denetim raporlarını, faaliyet raporlarını, kararları ve kurumsal belgeleri arşivlemek için kullanılan iki aşamalı hibrit indirme yöntemi.

---

## Neden İki Farklı Yöntem Kullanıldı?

Sayıştay web sitesinde belgeler tek bir standart yapıda sunulmamaktadır:

1. **Statik ve Doğrudan Bağlantılı Bölümler (`wget` ile Site Klonlama):** Sitenin pek çok alt sayfasında PDF, Word ve HTML belgeleri doğrudan statik linkler halinde yer alır. Bu kısımları hızlıca ve klasör hiyerarşisini bozmadan indirmek için `wget`'in tüm siteyi/alt dizinleri klonlama komutu kullanılmıştır.

2. **Dinamik ve Sayfalı Rapor Kategorileri ([`belge_indirici/dosya_indirici.py`](../belge_indirici/dosya_indirici.py)):** Sayıştay'ın `/reports/category/...` altındaki rapor sayfaları JavaScript/dinamik sayfalama (`?page=1`, `?page=2`) ve detay sayfası yönlendirmeleri içerir. Ayrıca buradaki PDF dosyalarının hangi kategoriye, hangi yıla ve hangi kuruma ait olduğunun kaybolmaması için her PDF'in sayfa başlığını, tarihini, SHA-256 özetini ve kaynak URL'sini `.metadata.json` olarak çıkaran Selenium tabanlı [`dosya_indirici.py`](../belge_indirici/dosya_indirici.py) betiği kullanılmıştır.

---

## 1. Yöntem: `wget` ile Sayıştay Sitesini / Alt Bölümlerini Klonlama

Sitenin statik bölümlerindeki tüm sayfaları ve gömülü belgeleri (`pdf`, `doc`, `docx`, `xls`, `xlsx`, `htm`, `html`) yerel diske aynı klasör yapısıyla klonlamak için kullanılan komut:

### A) Tüm Siteyi (veya Belirli Bir Bölümü) Tam Klonlama Komutu

```bash
wget --mirror --continue --no-clobber \
  --page-requisites --adjust-extension --convert-links \
  -e robots=off --no-check-certificate \
  --domains=sayistay.gov.tr,www.sayistay.gov.tr \
  --user-agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36" \
  https://www.sayistay.gov.tr/
```

### B) Sadece Belge ve Rapor Dosyalarını (`pdf`, `doc`, `docx`, `html`) Çekerek Klonlama

Tasarım, font ve script dosyalarını (`css`, `js`, `woff`, `svg`, `ico`) eleyip yalnızca belge ve metin sayfalarını indirmek için:

```bash
wget -r -l inf --continue --no-clobber \
  -e robots=off --no-check-certificate \
  --domains=sayistay.gov.tr,www.sayistay.gov.tr \
  -A "pdf,doc,docx,xls,xlsx,htm,html" \
  -R "css,js,woff,woff2,ttf,eot,svg,ico,png,jpg,jpeg,gif" \
  --user-agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36" \
  https://www.sayistay.gov.tr/
```

### `wget` Parametrelerinin Açıklaması

| Parametre | Ne İşe Yarar? |
| :--- | :--- |
| `--mirror` (`-m`) | Siteyi klonlama modunu açar (`-r -N -l inf --no-remove-listing` parametrelerinin birleşimidir). Sitenin tamamını sınırsız derinlikle tarar. |
| `--continue` (`-c`) | Bağlantı kopması durumunda yarım kalan büyük PDF/rapor dosyalarını kaldığı bayttan devam ettirir. |
| `--no-clobber` (`-nc`) | Daha önce indirilmiş dosyaları tekrar indirmez; işlemi durdurup yeniden başlattığınızda kaldığı yerden devam etmesini sağlar. |
| `--page-requisites` (`-p`) | Sayfaların çevrimdışı (offline) düzgün görüntülenmesi için gereken bileşenleri indirir. |
| `--adjust-extension` (`-E`) | Uzantısız gelen web sayfalarının sonuna otomatik `.html` uzantısı ekler. |
| `--convert-links` (`-k`) | İndirme bittiğinde sayfa içindeki linkleri yerel diskte çevrimdışı çalışacak şekilde dönüştürür. |
| `-e robots=off` | `robots.txt` engellerini yok sayarak tüm alt klasörlerdeki raporların taranabilmesini sağlar. |
| `--no-check-certificate` | Sunucu SSL sertifika doğrulama hatalarında indirme işleminin kesilmesini önler. |
| `--domains=sayistay.gov.tr,...` | `wget`'in dış sitelere verilen linklere çıkmasını engeller; sadece Sayıştay alan adında kalır. |
| `-A` / `-R` | İndirilecek (`-A`) ve hariç tutulacak (`-R`) dosya uzantılarını filtreler. |

---

## 2. Yöntem: `belge_indirici/dosya_indirici.py` ile Rapor Kategorilerini ve Metadata'yı İndirme

Sayıştay'ın kategorize edilmiş rapor sayfalarında (`/reports/category/...`) `wget` tek başına yeterli üst veriyi (raporun hangi kategoriden geldiği, yayın tarihi, özet açıklaması, SHA-256 doğrulaması) tutamaz ve dinamik detay sayfalarındaki indirme butonlarını kaçırabilir. Bu bölümler için deponun içindeki **[`belge_indirici/dosya_indirici.py`](../belge_indirici/dosya_indirici.py)** kullanılmıştır.

### `dosya_indirici.py` Ne Yapar?

1. Verilen ana kategori URL'sine (ve varsa alt kategorilerine) Selenium Headless Chrome ile girer.
2. `?page=1`, `?page=2` ... şeklinde kategorideki tüm liste sayfalarını (veya `-s` ile sınırlandırılan sayfa sayısı kadarını) gezer; doğrudan PDF bağlantılarını ve rapor detay bağlantılarını (`/reports/...`) toplar.
3. Her raporun detay sayfasına girerek:
   - Sayfadaki tüm gerçek PDF indirme bağlantılarını (`%PDF-` imzası doğrulamasıyla),
   - Detay sayfasının tam kaynak HTML'ini (`.source.html`),
   - Sayfadaki `<title>`, `<meta>`, `<time>` ve `<h1>..<h3>` etiketlerini ham haliyle ayıklar.
4. PDF dosyasını `<orijinal_ad>__[sayistay_gov_tr__<Kategori_Adi>__<url_sha256_8>].pdf` formatında deterministik bir isimle indirir.
5. Her PDF'in yanına dosyanın SHA-256 hash değerini, boyutunu, HTTP yanıt başlıklarını, tam kaynak URL zincirini ve sayfa ham verisini içeren `<dosya_adi>.pdf.metadata.json` dosyasını ve tüm indirilenlerin toplu listesini tutan `indirilen_dosyalar_metadata.json` kataloğunu kaydeder.

### Kullanım Komutları

```bash
pip install selenium

python ../belge_indirici/dosya_indirici.py \
  -u https://www.sayistay.gov.tr/reports/category/13-faaliyet-genel-degerlendirme-raporlari \
  -o sayistay_faaliyet_raporlari

python ../belge_indirici/dosya_indirici.py \
  -u https://www.sayistay.gov.tr/reports/category/1-kamu-idareleri-denetim-raporlari \
     https://www.sayistay.gov.tr/reports/category/2-dis-denetim-genel-degerlendirme-raporlari \
     https://www.sayistay.gov.tr/reports/category/13-faaliyet-genel-degerlendirme-raporlari \
  -s 10 \
  -o sayistay_tum_raporlar
```

### `dosya_indirici.py` Parametreleri

| Parametre | Varsayılan | Açıklama |
| :--- | :--- | :--- |
| `-u`, `--urls` | *(Zorunlu)* | Taranacak bir veya birden fazla Sayıştay kategori URL'si (boşlukla ayrılmış) |
| `-s`, `--sayfa` | Sınırsız (Tüm sayfalar) | Her kategori (ve alt kategori) için taranacak maksimum sayfa sayısı |
| `-o`, `--cikti` | `indirilen_pdfler` | İndirilen PDF'lerin, `.source.html`, `.metadata.json` ve `hata.json` dosyalarının kaydedileceği klasör |
| `--category-pattern` | `/reports/category/` | Alt kategori linklerini tespit etmek için URL kalıbı |
| `--detail-pattern` | `/reports/` | Rapor detay sayfalarını tespit etmek için URL kalıbı |

---

## Test ve Doğrulama Notu

Bu dokümanda yer alan tüm `wget` komutları ve [`belge_indirici/dosya_indirici.py`](../belge_indirici/dosya_indirici.py) betiği, **bu deponun (repository) oluşturulduğu ve yayımlandığı tarihte bizzat test edilmiş, çalıştığı ve hedeflenen rapor/belgeleri eksiksiz indirdiği onaylanmıştır.** Hedef kurumun ilerleyen tarihlerde web sitesi arayüzünü, URL yapısını veya sunucu güvenlik kurallarını değiştirmesi durumunda komutlarda güncelleme yapılması gerekebilir.

## Yasal Uyarı ve Sorumluluk Reddi

Bu dokümanda yer alan komutlar ve yazılımlar yalnızca eğitim, akademik araştırma ve kişisel arşivleme amaçlarıyla geliştirilmiştir. T.C. Sayıştay Başkanlığı veya diğer kamu kurumları ile herhangi bir resmi bağı yoktur.

Paylaşılan araçlar yalnızca kamuya açık olarak yayımlanan rapor ve belgelere standart HTTP/tarayıcı istekleriyle erişir; herhangi bir erişim kontrolünü, güvenlik duvarını veya kimlik doğrulama mekanizmasını aşmaz. Komutların ve betiklerin çalıştırılmasından, hedef sunuculara gönderilecek istek sıklığından, ilgili kurumun kullanım koşullarına, 6698 sayılı Kişisel Verilerin Korunması Kanunu (KVKK), 5651 sayılı Kanun ve yürürlükteki mevzuata uygun hareket edilmesinden tamamen kullanıcı sorumludur. Geliştirici ve katkıda bulunanlar, bu araçların doğrudan veya dolaylı kullanımından doğabilecek hiçbir hukuki, idari, cezai veya teknik sonuçtan sorumlu tutulamaz.
