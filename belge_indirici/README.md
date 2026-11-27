# Kamu Kurumları Belge ve Rapor İndirici

Sayıştay ve benzeri kamu kurumlarının web sitelerindeki rapor/belge sayfalarını Selenium ile tarar; bulunan PDF raporlarını, detay sayfasının ham HTML kaynağını (`.source.html`) ve hiçbir metin temizliği veya tahmini alan üretimi yapmadan doğrudan sayfa/HTTP üst verilerini içeren `.metadata.json` dosyalarını indirir.

## Dosyalar

- `dosya_indirici.py`: Belirtilen kategori veya liste sayfalarındaki tüm sayfaları sonuna kadar (veya `-s` ile sınırlandırılan sayfa kadar) tarar; hem doğrudan PDF bağlantılarını hem de rapor detay sayfalarındaki tüm PDF dosyalarını indirir.
- `hatali_dosya_indirici.py`: Tarama veya indirme sırasında hata alıp `hata.json` dosyasına kaydedilen kategori sayfalarını, detay sayfalarını ve PDF bağlantıları yeniden dener.

## Özellikler

- **Ham Veri ve Kaynak HTML Kaydı:** Metinlerde boşluk normalizasyonu yapılmaz; ilk 3 paragraftan açıklama uydurma, gövdeden rastgele tarih seçme veya ilk `h1`/`h2`'yi rapor başlığı varsayma gibi tahmini işlemler uygulanmaz. Bunun yerine detay sayfasının tam kaynak HTML'i (`<dosya_adi>.pdf.source.html`) ve sayfadaki tüm `<meta>`, `<time>`, `<h1>..<h3>` etiketleri ham haliyle kaydedilir.
- **Gerçek PDF Doğrulaması (`%PDF-`) ve Atomik Yazma:** İndirilen dosyalar önce `.part` uzantılı geçici dosyaya yazılır ve ilk baytlarında `%PDF-` imzası olup olmadığı kontrol edilir. Hata sayfası (HTML) dönen veya yarım kalan indirmeler PDF olarak kaydedilmez.
- **Deterministik Dosya Adlandırma:** Dosyalar `<orijinal_ad>__[<kurum>__<kategori>__<url_sha256_8>].pdf` adıyla kaydedilir. Çalıştırma sırasına bağımlı olmadığı için aynı dosya farklı çalıştırmalarda iki kez inmez.
- **Çoklu PDF Desteği:** Bir rapor detay sayfasında birden fazla PDF eki varsa ilkinde durmaz, sayfadaki tüm geçerli PDF bağlantılarını indirir.
- **Birikimli Katalog (`indirilen_dosyalar_metadata.json`):** Katalog dosyası her çalıştırmada sıfırlanmaz; önceki çalıştırmalarda indirilen kayıtları koruyarak yeni inenlerle birleştirir.
- **Detaylı Hata Kaydı (`hata.json`):** Kategori, detay sayfası, PDF indirme ve kritik hatalar URL, HTTP durum kodu, hata mesajı ve deneme sayısıyla birlikte `hata.json` dosyasına yazılır.

## Kullanım

```bash
pip install selenium

python dosya_indirici.py -u https://www.sayistay.gov.tr/reports/category/13-faaliyet-genel-degerlendirme-raporlari -o faaliyet_raporlari

python dosya_indirici.py -u URL1 URL2 -s 5 -o indirilen_pdfler

python hatali_dosya_indirici.py -o indirilen_pdfler
```

### Parametreler (`dosya_indirici.py`)

| Parametre | Varsayılan | Açıklama |
| :--- | :--- | :--- |
| `-u`, `--urls` | *(Zorunlu)* | Taranacak bir veya birden fazla sayfa/kategori adresi (boşlukla ayrılmış) |
| `-s`, `--sayfa` | Sınırsız (Tüm sayfalar) | Her kategori için taranacak maksimum sayfa sayısı |
| `-o`, `--cikti` | `indirilen_pdfler` | PDF, `.source.html`, `.metadata.json` ve `hata.json` dosyalarının kaydedileceği klasör |
| `--category-pattern` | `/reports/category/` | Alt kategori linklerini tespit etmek için URL kalıbı |
| `--detail-pattern` | `/reports/` | Rapor detay sayfalarını tespit etmek için URL kalıbı (doğrudan `.pdf` linkleri kalıptan bağımsız her sitede otomatik yakalanır) |

### Parametreler (`hatali_dosya_indirici.py`)

| Parametre | Varsayılan | Açıklama |
| :--- | :--- | :--- |
| `-o`, `--cikti` | `indirilen_pdfler` | `hata.json` dosyasının bulunduğu arşiv klasörü |
| `--include-404` | `False` | Kalıcı hata (`HTTP 404`) almış PDF bağlantılarını da yeniden dener |
| `--category-pattern` | `/reports/category/` | Alt kategori linklerini tespit etmek için URL kalıbı |
| `--detail-pattern` | `/reports/` | Rapor detay sayfalarını tespit etmek için URL kalıbı |

## Yasal Uyarı ve Sorumluluk Reddi

Bu yazılım yalnızca eğitim, akademik araştırma ve kişisel arşivleme amaçlarıyla geliştirilmiştir. Sayıştay veya diğer kamu kurumları ile herhangi bir resmi bağı yoktur. Kamuya açık belgelerin indirilmesi, saklanması ve kullanılması süreçlerinde ilgili kurumların kullanım koşullarına ve yürürlükteki mevzuata uyulması tamamen kullanıcının sorumluluğundadır. Geliştirici, yazılımın kullanımından doğabilecek hiçbir hukuki, idari, cezai veya teknik sonuçtan sorumlu tutulamaz.
