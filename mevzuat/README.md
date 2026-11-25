# Mevzuat Bilgi Sistemi (`mevzuat.gov.tr`) Veri İndirici

> **Geliştirici Notu:** Bu modül ve `mevzuat_indirici.py` kodu **Alperen Enes Bayar** tarafından yazılmıştır.

Bu modül, Türkiye Cumhuriyeti Cumhurbaşkanlığı Mevzuat Bilgi Sistemi (`www.mevzuat.gov.tr`) üzerindeki kanun, kanun hükmünde kararname (KHK), tüzük, Cumhurbaşkanlığı kararnamesi, yönetmelik, tebliğ, Cumhurbaşkanı kararı ve genelgeleri indeksleyerek hem HTML tam metinlerini hem de resmi PDF belgelerini yerel diske arşivleyen üç aşamalı bir veri toplama aracıdır.

## Kapsanan Mevzuat Türleri

Modül, `MevzuatDatatable` uç noktasını kullanarak aşağıdaki 12 mevzuat kategorisini tarar ve belge kimliğine (`doc_id`) göre tekilleştirir:

- `Kanun`
- `MulgaKanun` (Mülga Kanunlar)
- `KHK` (Kanun Hükmünde Kararnameler)
- `Tuzuk` (Tüzükler)
- `CumhurbaskaniKararnameleri`
- `CumhurbaskanligiVeBakanlarKuruluYonetmelik`
- `KurumVeKurulusYonetmeligi`
- `Yonetmelik`
- `TumYonetmelik`
- `Teblig` (Tebliğler)
- `CumhurbaskaniKararlari`
- `CumhurbaskanligiGenelgeleri`

## Çalışma Mantığı (3 Aşamalı Mimari)

1. **`index` (İndeksleme ve Manifest Oluşturma):**
   - `https://www.mevzuat.gov.tr/anasayfa/MevzuatDatatable` API uç noktasına her mevzuat türü için sayfalı `POST` istekleri gönderir.
   - Dönen mevzuat kayıtlarını `doc_id` (`{mevzuatTur}.{mevzuatTertip}.{mevzuatNo}` veya genelgeler için `{mevzuatTur}.{pdf_adi}`) bazında tekilleştirerek çıktı dizinindeki `manifest.jsonl` dosyasına artımlı (incremental) olarak yazar.
   - Kesintiye uğradığında `manifest.jsonl` içindeki mevcut `doc_id` kayıtlarını okuyarak kaldığı yerden devam eder.

2. **`fetch` (HTML ve PDF Belgelerini İndirme):**
   - `manifest.jsonl` dosyasındaki her mevzuat kaydı için hedef bağlantıları oluşturur:
     - **HTML Metin:** `/anasayfa/MevzuatFihristDetayIframe?MevzuatTur=...&MevzuatNo=...&MevzuatTertip=...` -> `html/{tur}/{doc_id}.html`
     - **PDF Belge:** `/MevzuatMetin/[yonetmelik/]{tur}.{tertip}.{no}.pdf` -> `pdf/{tur}/{doc_id}.pdf`
   - `ThreadPoolExecutor` ile çoklu iş parçacığı (multi-threading) kullanarak indirme yapar.
   - Dosyalar önce `.part` uzantılı geçici dosyaya yazılıp tamamlandığında hedef dosya adına atomik olarak taşınır.
   - Her işlem `fetch_log.jsonl` dosyasına (`ok`, `missing`, `not_pdf`, `error`) işlenir; tekrar çalıştırıldığında daha önce tamamlanmış dosyalar atlanır.

3. **`status` (Durum Özeti):**
   - `manifest.jsonl` ve `fetch_log.jsonl` dosyalarını analiz ederek mevzuat türlerine göre toplam belge sayılarını, indirilen HTML/PDF adetlerini ve toplam disk boyutunu (GB) raporlar.

### SSL / Ara Sertifika Çözümü (`ensure_ca_bundle`)
`www.mevzuat.gov.tr` sunucusu TLS el sıkışmasında ara sertifikayı (`GeoTrust TLS RSA CA G1`) eksik gönderdiği için betik, DigiCert üzerinden ilgili ara sertifikayı otomatik indirir ve sistem kök sertifikalarıyla birleştirerek çıktı klasöründe `ca_bundle.pem` oluşturur.

## Gereksinimler

- Python 3.8+
- `requests`

Kurulum:
```bash
pip install requests
```

## Kullanım

### 1. Mevzuat İndeksini (`manifest.jsonl`) Oluşturma
```bash
python mevzuat_indirici.py index --out ./mevzuat_arsiv --page-size 100 --delay 1.0
```

### 2. HTML ve PDF Dosyalarını İndirme
```bash
# Tüm HTML ve PDF belgelerini 2 worker ile indirme:
python mevzuat_indirici.py fetch --out ./mevzuat_arsiv --workers 2 --delay 1.0

# Sadece HTML veya sadece PDF indirmek için:
python mevzuat_indirici.py fetch --out ./mevzuat_arsiv --only html
python mevzuat_indirici.py fetch --out ./mevzuat_arsiv --only pdf
```

### 3. İndirme Durumunu Görüntüleme
```bash
python mevzuat_indirici.py status --out ./mevzuat_arsiv
```

## Çıktı Dizin Yapısı

```text
mevzuat_arsiv/
├── ca_bundle.pem         # Birleştirilmiş SSL sertifika paketi
├── manifest.jsonl        # Tüm mevzuatların API liste künyeleri (JSON Lines)
├── fetch_log.jsonl       # İndirilen dosyaların durum kayıtları
├── html/
│   └── {mevzuatTur}/
│       └── {doc_id}.html # Mevzuat tam metin HTML içerikleri
└── pdf/
    └── {mevzuatTur}/
        └── {doc_id}.pdf  # Resmi PDF mevzuat belgeleri
```

[Ana Dokümantasyona Dön](../README.md)
